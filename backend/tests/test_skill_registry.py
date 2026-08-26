import io
import json
import zipfile

import pytest

from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import IdentityRepository
from app.domains.skills import SkillDraft, SkillRegistry, SkillService
from app.models.gateway import ModelReply
from app.settings import Settings


class SkillGateway:
    def complete(self, messages, **kwargs):
        return ModelReply(json.dumps({
            "name": "verify-offer-package",
            "display_name": "Offer材料核验",
            "description": "核验 Offer 发放材料是否齐备，并在准备发放材料时触发。",
            "purpose": "依据材料清单核验 Offer 发放准备情况，输出缺口和人工复核建议。",
            "trigger_phrases": ["Offer核验", "Offer材料"],
            "capabilities": ["file.extract", "evidence.answer"],
            "inputs": [
                {"name": "offer_package", "description": "待核验的 Offer 材料包", "required": True},
                {"name": "checklist", "description": "适用的材料清单或政策", "required": True},
            ],
            "workflow_steps": [
                "确认材料范围和适用清单。",
                "逐项提取文件证据并标记缺失内容。",
                "生成可复核清单并提交人工确认。",
            ],
            "validations": ["核对必需文件数量、名称和版本，缺失项进入异常清单。"],
            "guardrails": ["不得自动发放 Offer；不得输出候选人敏感信息。"],
            "outputs": ["Offer 材料核验清单", "缺失与待确认事项"],
            "examples": [
                {
                    "user_request": "请检查这批 Offer 材料是否齐全",
                    "expected_behavior": "读取材料和清单，输出证据化缺口并等待人工确认。",
                },
                {
                    "user_request": "发 Offer 前帮我核验附件",
                    "expected_behavior": "按适用清单检查附件并列出缺失项。",
                },
            ],
            "non_examples": [
                {
                    "user_request": "帮我筛选十份简历",
                    "expected_behavior": "不要触发本 Skill，应交给简历筛选能力。",
                },
                {
                    "user_request": "查询今天的天气",
                    "expected_behavior": "不要触发本 Skill，应交给公共信息能力。",
                },
            ],
            "resource_plan": [{
                "kind": "references", "name": "offer-checklist.md",
                "purpose": "保存组织的 Offer 材料要求", "required": False,
            }],
            "risk_level": "medium",
            "data_classes": ["candidate_hr"],
            "approval_required": True,
            "unresolved_questions": [],
        }, ensure_ascii=False), "fake-model", 5, {})


class NeedsInputThenReadySkillGateway:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        payload = json.loads(SkillGateway().complete(messages, **kwargs).content)
        if self.calls == 1:
            payload["resource_plan"] = [{
                "kind": "assets",
                "name": "offer-template.docx",
                "purpose": "固定版式模板",
                "required": True,
            }]
            payload["unresolved_questions"] = ["正式输出是否必须使用固定模板？"]
        else:
            payload["resource_plan"] = [{
                "kind": "assets",
                "name": "offer-template.docx",
                "purpose": "有模板时沿用；没有时使用系统标准版式",
                "required": False,
            }]
            payload["unresolved_questions"] = []
        return ModelReply(json.dumps(payload, ensure_ascii=False), "fake-model", 5, {})


def test_skill_registry_supports_draft_enable_version_select_and_export(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    assert len(registry.list()) >= 4
    system_skills = [item for item in registry.list() if item.source_type == "system"]
    assert system_skills
    assert all(item.metadata["package_format"] == "agent-skills-v1" for item in system_skills)
    assert all(item.metadata["validation"]["publishable"] is True for item in system_skills)

    created = registry.create(SkillDraft(
        name="check-onboarding",
        display_name="入职材料核验",
        description="检查入职资料。",
        instructions="逐项检查并输出缺失清单。",
        trigger_phrases=["入职材料"],
        source_type="prompt",
    ))
    assert created.status == "draft" and not created.enabled
    assert registry.select("请核验入职材料") == []

    enabled = registry.update(created.id, enabled=True)
    assert enabled.status == "active"
    assert registry.select("请核验入职材料")[0].skill.id == created.id
    assert registry.select_by_ids([created.id])[0].matched_trigger == "explicit"

    revised = registry.update(created.id, instructions="检查身份、合同和审批，并输出缺失清单。")
    assert revised.version == 2
    assert revised.status == "draft" and not revised.enabled
    assert "name: check-onboarding" in registry.export_markdown(created.id)


def test_skill_versions_are_immutable_and_rollback_creates_a_new_version(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    created = registry.create(SkillDraft(
        name="immutable-workflow",
        display_name="不可变流程",
        description="验证版本不可变与回滚。",
        instructions="第一版：检查输入。",
        trigger_phrases=["不可变流程"],
    ))
    second = registry.update(created.id, instructions="第二版：检查输入并输出结果。")

    versions_before = registry.list_versions(created.id)
    assert [item.version for item in versions_before] == [2, 1]
    assert versions_before[1].snapshot.instructions == "第一版：检查输入。"
    assert versions_before[0].snapshot.instructions == "第二版：检查输入并输出结果。"

    rolled_back = registry.rollback(created.id, 1)
    versions_after = registry.list_versions(created.id)

    assert second.version == 2
    assert rolled_back.version == 3
    assert rolled_back.instructions == "第一版：检查输入。"
    assert rolled_back.status == "draft" and not rolled_back.enabled
    assert versions_after[0].change_type == "rollback_from_v1"
    assert versions_after[1].snapshot.instructions == "第二版：检查输入并输出结果。"
    assert versions_after[2].snapshot.instructions == "第一版：检查输入。"


def test_organization_skill_requires_admin_approval_and_becomes_shared(tmp_path):
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "unihr.db")
    identity = IdentityRepository(settings.database_path, settings)
    registry = SkillRegistry(settings.database_path)
    user = identity.login("hr-user", "UniHR@2026").user
    admin = identity.login("admin", "UniHR@2026").user
    created = registry.create(SkillDraft(
        name="organization-offer-check",
        display_name="组织 Offer 核验",
        description="在组织内复用的 Offer 核验流程。",
        instructions="检查材料并输出缺口。",
        trigger_phrases=["组织 Offer 核验"],
        metadata={"validation": {"passed": True, "publishable": True}},
    ), user)

    pending = registry.request_organization_approval(created.id, user)

    assert pending.status == "pending_approval"
    assert pending.approval_status == "pending"
    with pytest.raises(PermissionError, match="skill_organization_approval_required"):
        registry.list_organization_approvals(user)
    assert registry.list_organization_approvals(admin)[0].id == created.id

    approved = registry.decide_organization_approval(created.id, True, admin, "组织 QA 已复核")

    assert approved.visibility == "organization"
    assert approved.approval_status == "approved"
    assert approved.owner_user_id == "*"
    assert approved.enabled and approved.status == "active"
    assert registry.get(created.id, user).id == created.id
    with pytest.raises(PermissionError, match="organization_skill_admin_only"):
        registry.update(created.id, description="普通用户不能修改组织 Skill", principal=user)


def test_skill_service_generates_and_imports_quarantined_packages(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    repository = ConversationRepository(tmp_path / "unihr.db")
    service = SkillService(registry, repository, SkillGateway(), "fake-model")

    generated = service.generate("把Offer材料核验流程做成Skill", "我的 Offer 核验")
    assert generated.source_type == "prompt"
    assert generated.status == "draft" and not generated.enabled
    assert generated.display_name == "我的 Offer 核验"
    assert generated.metadata["package_format"] == "agent-skills-v1"
    assert generated.metadata["validation"]["passed"] is True
    assert generated.metadata["validation"]["publishable"] is True
    assert generated.metadata["eval_suite"]["status"] == "passed"
    assert generated.metadata["authoring"]["status"] == "waiting_review"
    assert generated.metadata["authoring"]["resumable"] is True
    assert generated.metadata["authoring"]["graph_path"] == [
        "author", "compile", "static_validate", "harness", "human_review_gate",
    ]
    assert "## 工作流程" in generated.instructions
    with zipfile.ZipFile(io.BytesIO(service.export_package(generated.id))) as archive:
        assert "verify-offer-package/SKILL.md" in archive.namelist()
        assert "verify-offer-package/agents/openai.yaml" in archive.namelist()

    published = service.publish(generated.id, comment="QA reviewed")
    assert published.enabled and published.status == "active"
    assert published.metadata["authoring"]["status"] == "published"
    assert published.metadata["authoring"]["approval_decision"] is True
    assert "approval.resume" in published.metadata["authoring"]["graph_path"]

    revised_generated = registry.update(
        generated.id,
        instructions=published.instructions + "\n\n补充一项新的输出约束。",
    )
    assert not revised_generated.enabled and revised_generated.status == "draft"
    assert revised_generated.metadata["eval_suite"]["status"] == "stale"
    assert revised_generated.metadata["validation"]["publishable"] is False

    legacy = registry.create(SkillDraft(
        name="legacy-checklist",
        display_name="Legacy Checklist",
        description="Legacy checklist skill used before package validation existed.",
        instructions="Check every item and return a list.",
        trigger_phrases=["legacy checklist"],
        source_type="prompt",
    ))
    upgraded = service.validate(legacy.id)
    assert upgraded.metadata["package_format"] == "agent-skills-v1"
    assert upgraded.metadata["legacy_upgraded"] is True
    assert upgraded.metadata["validation"]["passed"] is False

    empty_conversation = repository.create_conversation("Empty conversation")
    with pytest.raises(ValueError, match="skill_conversation_not_ready"):
        service.from_conversation(empty_conversation.id)

    completed_conversation = repository.create_conversation("Completed workflow")
    repository.add_message(completed_conversation.id, "user", "Check this reusable workflow.")
    repository.add_message(completed_conversation.id, "assistant", "The reviewed checklist is complete.")
    conversation_skill = service.from_conversation(completed_conversation.id, "Reviewed workflow")
    assert conversation_skill.source_type == "conversation"
    assert conversation_skill.source_conversation_id == completed_conversation.id
    assert conversation_skill.metadata["source_message_count"] == 2

    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("my-skill/SKILL.md", "---\nname: imported-skill\ndescription: 导入测试\n---\n# 导入 Skill\n\n核对输入并生成清单。")
        archive.writestr("my-skill/scripts/run.py", "raise RuntimeError('must never execute')")
    imported = service.import_package("imported.zip", package.getvalue())
    assert imported.source_type == "upload"
    assert imported.metadata["resources_quarantined"] is True
    assert imported.metadata["scripts_executed"] is False

    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../SKILL.md", "---\nname: unsafe\ndescription: unsafe package\n---\n# Unsafe")
    try:
        service.import_package("unsafe.zip", unsafe.getvalue())
    except ValueError as exc:
        assert str(exc) == "skill_package_unsafe_path"
    else:
        raise AssertionError("path traversal must be rejected")


def test_skill_needs_input_can_be_completed_and_resume_to_human_review(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    repository = ConversationRepository(tmp_path / "unihr.db")
    gateway = NeedsInputThenReadySkillGateway()
    service = SkillService(registry, repository, gateway, "fake-model")

    created = service.generate("把 Offer 材料核验做成 Skill")

    assert created.metadata["authoring"]["status"] == "needs_input"
    assert created.metadata["validation"]["blocking_issues"] == [
        "unresolved_questions", "required_resources_missing",
    ]

    completed = service.complete_authoring(
        created.id,
        "固定模板不是必须；没有模板时使用系统标准版式。",
        defer_missing_resources=True,
    )

    assert completed.id == created.id
    assert completed.version == created.version + 1
    assert completed.metadata["unresolved_questions"] == []
    assert completed.metadata["resource_plan"][0]["required"] is False
    assert completed.metadata["validation"]["publishable"] is True
    assert completed.metadata["eval_suite"]["status"] == "passed"
    assert completed.metadata["authoring"]["status"] == "waiting_review"
    assert completed.metadata["authoring"]["resumable"] is True
    assert completed.metadata["authoring"]["graph_path"][-2:] == [
        "harness", "human_review_gate",
    ]

    published = service.publish(completed.id, comment="用户已复核")

    assert published.enabled is True
    assert published.metadata["authoring"]["status"] == "published"


def test_duplicate_generated_skill_name_rebinds_harness_without_becoming_stale(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    repository = ConversationRepository(tmp_path / "unihr.db")
    service = SkillService(registry, repository, SkillGateway(), "fake-model")

    first = service.generate("把 Offer 材料核验做成 Skill")
    second = service.generate("为另一个场景再生成同名 Offer 核验 Skill")

    assert first.name != second.name
    assert second.metadata["validation"]["publishable"] is True
    assert second.metadata["eval_suite"]["status"] == "passed"
    assert second.metadata["authoring"]["status"] == "waiting_review"
    assert second.metadata["authoring"]["resumable"] is True


def test_skill_resource_workspace_resolves_required_reference_and_exports_package(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    repository = ConversationRepository(tmp_path / "unihr.db")
    service = SkillService(registry, repository, SkillGateway(), "fake-model")
    created = service.generate("把 Offer 材料核验做成 Skill")
    metadata = {
        **created.metadata,
        "resource_plan": [{
            "kind": "references",
            "name": "offer-checklist.md",
            "purpose": "组织 Offer 材料要求",
            "required": True,
        }],
    }
    registry.replace_metadata(created.id, metadata)

    blocked = service.validate(created.id)
    assert "required_resources_missing" in blocked.metadata["validation"]["blocking_issues"]

    reference = service.add_resource(
        created.id,
        kind="references",
        filename="offer-checklist.md",
        content=b"# Demo checklist\n\n- approval form\n- salary confirmation\n",
        mime_type="text/markdown",
    )
    resolved = registry.get(created.id)
    assert reference.status == "ready"
    assert "required_resources_missing" not in resolved.metadata["validation"]["blocking_issues"]
    assert resolved.metadata["resource_bindings"][0]["sha256"] == reference.sha256

    published = service.publish(created.id, comment="resource reviewed")
    assert published.enabled is True

    script = service.add_resource(
        created.id,
        kind="scripts",
        filename="check.py",
        content=b"raise RuntimeError('must never execute')\n",
        mime_type="text/x-python",
    )
    assert script.status == "quarantined"
    assert script.scripts_executed is False
    after_resource_change = registry.get(created.id)
    assert after_resource_change.enabled is False and after_resource_change.status == "draft"
    assert any("隔离" in warning for warning in after_resource_change.metadata["validation"]["warnings"])

    with zipfile.ZipFile(io.BytesIO(service.export_package(created.id))) as archive:
        assert "verify-offer-package/references/offer-checklist.md" in archive.namelist()
        assert "verify-offer-package/scripts/check.py" in archive.namelist()

    service.delete_resource(created.id, reference.id)
    assert "required_resources_missing" in registry.get(created.id).metadata["validation"]["blocking_issues"]

    with pytest.raises(ValueError, match="skill_resource_unsafe_name"):
        service.add_resource(
            created.id,
            kind="assets",
            filename="../template.docx",
            content=b"unsafe",
            mime_type="application/octet-stream",
        )
