import json

from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal
from app.domains.skills import SkillDraft, SkillDryRunModule, SkillRegistry
from app.models.gateway import ModelReply
from app.settings import Settings


class QueueGateway:
    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, messages, **kwargs):
        return ModelReply(self.replies.pop(0), kwargs.get("model") or "fake", 7, {})


class FailingGateway:
    def complete(self, messages, **kwargs):
        raise RuntimeError("provider unavailable")


def _route(intent="conversation"):
    return json.dumps({
        "primary_intent": intent,
        "secondary_intents": [],
        "context_mode": "uses_conversation",
        "missing_inputs": [],
        "risk_level": "low",
        "data_class": "internal_hr",
        "required_capabilities": [],
        "confidence": 0.96,
        "reason_summary": "测试路由",
        "turn_action": "start_new",
    }, ensure_ascii=False)


def _principal():
    return Principal(
        user_id="user_hr", username="hr-user", display_name="HR 用户",
        role="user", tenant_id="tenant_a",
    )


def test_draft_skill_runs_in_disposable_workspace_and_persists_only_summary(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "unihr.db")
    registry = SkillRegistry(settings.database_path, default_tenant_id="tenant_a")
    skill = registry.create(SkillDraft(
        name="onboarding-check",
        display_name="入职材料核验",
        description="按清单核验入职材料。",
        instructions="逐项核验并列出缺口，不得编造材料。",
        trigger_phrases=["核验入职材料"],
        source_type="prompt",
        enabled=False,
        status="draft",
    ), _principal())
    module = SkillDryRunModule(
        settings, registry, QueueGateway([_route(), "已核验：身份证明待补充。"]),
    )

    result = module.run(skill.id, "帮我核验这份入职材料", [], _principal())

    assert result.status == "passed"
    assert result.skill_version == 1
    assert "skill.selected" in result.trace_events
    assert "execute" in result.graph_path
    assert "身份证明待补充" in result.answer_excerpt
    assert ConversationRepository(settings.database_path).list_conversations(_principal()) == []
    assert module.list(skill.id, _principal())[0].id == result.id


def test_data_skill_without_upload_reports_needs_input_instead_of_false_failure(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "unihr.db")
    registry = SkillRegistry(settings.database_path, default_tenant_id="tenant_a")
    skill = registry.create(SkillDraft(
        name="contract-expiry",
        display_name="合同到期检查",
        description="读取合同台账并检查到期日期。",
        instructions="必须读取真实台账，不得虚构员工。",
        trigger_phrases=["合同到期检查"],
        capabilities=["file.extract", "spreadsheet.inspect", "spreadsheet.transform"],
        source_type="prompt",
        enabled=False,
        status="draft",
    ), _principal())
    module = SkillDryRunModule(settings, registry, QueueGateway([_route("spreadsheet_processing")]))

    result = module.run(skill.id, "看看未来30天谁的合同到期", [], _principal())

    assert result.status == "needs_input"
    assert any(event in result.trace_events for event in (
        "skill.input.required", "skill.graph.node.waiting_input", "workflow.waiting_input",
    ))
    assert not any(item.startswith("required_tool_not_observed") for item in result.issues)


def test_execution_failure_is_persisted_as_a_visible_dry_run_result(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "unihr.db")
    registry = SkillRegistry(settings.database_path, default_tenant_id="tenant_a")
    skill = registry.create(SkillDraft(
        name="visible-failure", display_name="可见失败测试", description="验证异常会留下记录。",
        instructions="执行并返回结果。", trigger_phrases=["执行测试"], source_type="prompt",
    ), _principal())
    module = SkillDryRunModule(settings, registry, FailingGateway())

    result = module.run(skill.id, "执行测试", [], _principal())

    assert result.status == "failed"
    assert result.issues == ["execution_error:RuntimeError"]
    assert module.list(skill.id, _principal())[0].id == result.id
