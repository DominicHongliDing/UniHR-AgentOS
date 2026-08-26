from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal
from app.domains.identity.repository import ADMIN_USER_ID
from app.models.gateway import ModelGateway

from .authoring_graph import (
    SkillAuthoringGraph,
    SkillAuthoringPointer,
    SkillAuthoringRequest,
    SkillAuthoringResult,
    SkillAuthoringStatus,
)
from .contracts import SkillDraft, SkillRecord, SkillResourceKind, SkillResourceRecord, SkillSource
from .harness import SkillHarness, merge_harness_metadata
from .registry import SkillRegistry
from .resources import SkillResourceStore
from .skill_builder import (
    SkillBuilderAgent,
    SkillPackageValidator,
    refresh_package_metadata,
    render_skill_markdown,
    render_openai_yaml,
)


class SkillService:
    """Create governed Agent Skill packages from prompts, conversations, or uploads."""

    def __init__(
        self,
        registry: SkillRegistry,
        repository: ConversationRepository,
        gateway: ModelGateway,
        model: str,
        workflow_checkpointer: BaseCheckpointSaver | None = None,
        workflow_checkpoint_dsn: str = "",
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.gateway = gateway
        self.model = model
        self.builder = SkillBuilderAgent(gateway, model)
        self.validator = SkillPackageValidator()
        self.harness = SkillHarness()
        self.resources = SkillResourceStore(
            registry.database_path, registry.database_path.parent / "skill-resources",
        )
        self.authoring = (
            SkillAuthoringGraph(self.builder, checkpointer=workflow_checkpointer)
            if workflow_checkpointer is not None
            else SkillAuthoringGraph.with_postgres(self.builder, workflow_checkpoint_dsn)
            if workflow_checkpoint_dsn
            else SkillAuthoringGraph(self.builder, checkpointer=InMemorySaver())
        )

    def close(self) -> None:
        self.authoring.close()

    def generate(self, prompt: str, preferred_name: str = "", principal: Principal | None = None) -> SkillRecord:
        clean = prompt.strip()
        if not clean:
            raise ValueError("skill_prompt_required")
        result = self._start_authoring(
            task="根据用户描述生成一个可复用的人事工作 Skill。",
            material=clean,
            preferred_name=preferred_name,
            source_type="prompt",
            principal=principal,
        )
        return self.registry.create(self._draft_with_authoring(result), principal)

    def complete_authoring(
        self,
        skill_id: str,
        supplement: str,
        *,
        defer_missing_resources: bool = True,
        principal: Principal | None = None,
    ) -> SkillRecord:
        """Complete a needs-input draft through a new governed authoring run."""
        current = self.registry.get(skill_id, principal)
        if current.source_type == "system":
            raise ValueError("system_skill_is_read_only")
        clean = supplement.strip()
        if not clean and not defer_missing_resources:
            raise ValueError("skill_completion_input_required")
        metadata = dict(current.metadata)
        questions = [str(item) for item in metadata.get("unresolved_questions") or []]
        resources = [item for item in metadata.get("resource_plan") or [] if isinstance(item, dict)]
        material = json.dumps({
            "current_skill": {
                "name": current.name,
                "display_name": current.display_name,
                "description": current.description,
                "instructions": current.instructions,
                "trigger_phrases": current.trigger_phrases,
                "capabilities": current.capabilities,
            },
            "questions_to_resolve": questions,
            "planned_resources": resources,
            "user_supplement": clean,
            "resource_decision": (
                "未提供的模板、脚本和参考资料不得标记为必需；使用已注册工具或通用流程作为回退"
                if defer_missing_resources
                else "保留真正不可替代的必需资源"
            ),
        }, ensure_ascii=False)[:40_000]
        result = self._start_authoring(
            task=(
                f"完善现有 Skill。name 必须保持为 {current.name}；吸收用户补充，"
                "把运行时才提供的月份、文件等放入 inputs，不得继续列为作者待确认问题。"
            ),
            material=material,
            preferred_name=current.display_name,
            source_type=current.source_type,
            source_conversation_id=current.source_conversation_id,
            source_metadata={
                "completion_of_skill_id": current.id,
                "deferred_missing_resources": defer_missing_resources,
            },
            principal=principal,
        )
        completed = self._rebind_completed_draft(current, result)
        return self.registry.replace_draft(
            skill_id,
            completed,
            change_type="authoring_completed",
            principal=principal,
        )

    def from_conversation(
        self, conversation_id: str, preferred_name: str = "", principal: Principal | None = None,
    ) -> SkillRecord:
        conversation = self.repository.get_conversation(conversation_id, principal)
        messages = self.repository.list_messages(conversation_id, limit=100, principal=principal)
        roles = {str(item.role).casefold() for item in messages}
        if len(messages) < 2 or not {"user", "assistant"}.issubset(roles):
            raise ValueError("skill_conversation_not_ready")
        transcript = "\n".join(f"{item.role}: {item.content}" for item in messages)
        scrubbed = _scrub_sensitive_examples(transcript)
        material = f"对话标题：{conversation.title}\n{scrubbed[-40_000:]}"
        if len(scrubbed) > 40_000:
            material = self._compress_long_conversation(conversation.title, scrubbed)
        result = self._start_authoring(
            task=(
                "把这次完整的人事工作对话总结成可复用 Skill。只沉淀目标、输入、步骤、校验、"
                "审批闸门和输出格式，不复制一次性业务事实。"
            ),
            material=material,
            preferred_name=preferred_name,
            source_type="conversation",
            source_conversation_id=conversation_id,
            source_metadata={
                "privacy_scrubbed": True,
                "source_title": conversation.title,
                "source_message_count": len(messages),
            },
            principal=principal,
        )
        return self.registry.create(self._draft_with_authoring(result), principal)

    def import_package(
        self, filename: str, content: bytes, principal: Principal | None = None,
    ) -> SkillRecord:
        if not content:
            raise ValueError("skill_file_empty")
        if len(content) > 5 * 1024 * 1024:
            raise ValueError("skill_package_limit_5mb")
        extension = Path(filename).suffix.casefold()
        resources: list[str] = []
        resource_payloads: list[tuple[SkillResourceKind, str, bytes]] = []
        openai_yaml = ""
        if extension == ".md":
            markdown = _decode(content)
        elif extension == ".zip":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    entries = [item for item in archive.infolist() if not item.is_dir()]
                    if len(entries) > 200 or sum(item.file_size for item in entries) > 20 * 1024 * 1024:
                        raise ValueError("skill_package_expanded_limit")
                    if any(item.flag_bits & 0x1 for item in entries):
                        raise ValueError("skill_package_encrypted")
                    if any(not _safe_zip_name(item.filename) or _zip_entry_is_symlink(item) for item in entries):
                        raise ValueError("skill_package_unsafe_path")
                    safe_entries = entries
                    manifest = next((item for item in safe_entries if Path(item.filename).name.casefold() == "skill.md"), None)
                    if manifest is None:
                        raise ValueError("skill_manifest_missing")
                    if manifest.file_size > 200_000:
                        raise ValueError("skill_manifest_too_large")
                    markdown = _decode(archive.read(manifest))
                    resources = [item.filename for item in safe_entries if item != manifest][:100]
                    for item in safe_entries:
                        parts = Path(item.filename.replace("\\", "/")).parts
                        kind = next((part for part in parts if part in {"scripts", "references", "assets"}), "")
                        if kind and item.file_size <= 10 * 1024 * 1024:
                            resource_payloads.append((kind, Path(item.filename).name, archive.read(item)))
                    metadata_file = next(
                        (
                            item for item in safe_entries
                            if item.filename.replace("\\", "/").endswith("agents/openai.yaml")
                        ),
                        None,
                    )
                    if metadata_file is not None and metadata_file.file_size <= 100_000:
                        openai_yaml = _decode(archive.read(metadata_file))
            except zipfile.BadZipFile as exc:
                raise ValueError("skill_package_invalid") from exc
        else:
            raise ValueError("skill_file_must_be_md_or_zip")
        name, description, display_name, instructions = _parse_skill_markdown(markdown)
        draft = SkillDraft(
            name=name,
            display_name=display_name,
            description=description,
            instructions=instructions,
            trigger_phrases=[display_name, name],
            source_type="upload",
            enabled=False,
            status="draft",
            metadata={},
        )
        openai_yaml = openai_yaml or render_openai_yaml(name, display_name, description)
        validation = self.validator.validate(
            draft=draft, skill_markdown=markdown, openai_yaml=openai_yaml,
        )
        draft = draft.model_copy(update={"metadata": {
            "package_format": "agent-skills-v1",
            "skill_md": markdown,
            "openai_yaml": openai_yaml,
            "original_filename": Path(filename).name,
            "resource_names": resources,
            "resources_quarantined": bool(resources),
            "scripts_executed": False,
            "validation": validation,
        }})
        created = self.registry.create(draft, principal)
        for kind, resource_name, resource_content in resource_payloads:
            self.resources.add(
                created, principal, kind=kind, filename=resource_name,
                content=resource_content,
            )
        return self._sync_resources(created.id, principal, change_type="resources_imported") if resource_payloads else created

    def validate(self, skill_id: str, principal: Principal | None = None) -> SkillRecord:
        current = self.registry.get(skill_id, principal)
        metadata = self._metadata_with_resources(current, principal)
        if metadata.get("package_format") == "agent-skills-v1":
            metadata = refresh_package_metadata(current, metadata)
        else:
            markdown = render_skill_markdown(
                current.name,
                current.description,
                current.display_name,
                current.instructions,
            )
            openai_yaml = render_openai_yaml(current.name, current.display_name, current.description)
            validation = self.validator.validate(
                draft=current,
                skill_markdown=markdown,
                openai_yaml=openai_yaml,
            )
            metadata = {
                **metadata,
                "package_format": "agent-skills-v1",
                "skill_md": markdown,
                "openai_yaml": openai_yaml,
                "validation": validation,
                "legacy_upgraded": True,
            }
            metadata = refresh_package_metadata(current, metadata)
        report = self.harness.evaluate(current, metadata)
        metadata = merge_harness_metadata(metadata, report)
        authoring = dict(metadata.get("authoring") or {})
        authoring.update({
            "status": "validated" if metadata["validation"].get("publishable") else "needs_input",
            "resumable": False,
            "graph_path": [*list(authoring.get("graph_path") or []), "manual_validate", "harness"],
            "content_sha256": metadata.get("content_sha256"),
        })
        metadata["authoring"] = authoring
        self.registry.replace_metadata(skill_id, metadata, principal)
        return self.registry.get(skill_id, principal)

    def publish(
        self,
        skill_id: str,
        principal: Principal | None = None,
        comment: str = "",
    ) -> SkillRecord:
        current = self.registry.get(skill_id, principal)
        if current.source_type == "system":
            raise ValueError("system_skill_is_read_only")
        if current.enabled:
            return current
        validation = current.metadata.get("validation")
        if not isinstance(validation, dict) or not validation.get("publishable"):
            current = self.validate(skill_id, principal)
            validation = current.metadata.get("validation")
        if not isinstance(validation, dict) or not validation.get("publishable"):
            raise ValueError("skill_validation_failed")

        metadata = dict(current.metadata)
        authoring = dict(metadata.get("authoring") or {})
        if authoring.get("status") == SkillAuthoringStatus.WAITING_REVIEW.value and authoring.get("resumable"):
            tenant_id, owner_user_id = self._scope(principal)
            result = self.authoring.resume(
                SkillAuthoringPointer(
                    run_id=str(authoring.get("run_id") or ""),
                    tenant_id=tenant_id,
                    owner_user_id=owner_user_id,
                ),
                approved=True,
                comment=comment,
            )
            if result.status != SkillAuthoringStatus.PUBLISHED:
                raise ValueError("skill_publish_not_approved")
            authoring.update({
                "status": result.status.value,
                "resumable": False,
                "graph_path": result.graph_path,
                "approval_decision": result.approval_decision,
                "approval_comment": result.approval_comment,
            })
        else:
            authoring.update({
                "status": SkillAuthoringStatus.PUBLISHED.value,
                "resumable": False,
                "approval_decision": True,
                "approval_comment": comment.strip()[:1_000],
                "graph_path": [*list(authoring.get("graph_path") or []), "human_review", "publish"],
            })
        metadata["authoring"] = authoring
        self.registry.replace_metadata(skill_id, metadata, principal)
        return self.registry.update(skill_id, enabled=True, principal=principal)

    def request_organization_approval(
        self, skill_id: str, principal: Principal | None = None,
    ) -> SkillRecord:
        current = self.registry.get(skill_id, principal)
        validation = current.metadata.get("validation")
        if not isinstance(validation, dict) or not validation.get("publishable"):
            current = self.validate(skill_id, principal)
        return self.registry.request_organization_approval(current.id, principal)

    def delete(self, skill_id: str, principal: Principal | None = None) -> None:
        current = self.registry.get(skill_id, principal)
        authoring = current.metadata.get("authoring")
        if isinstance(authoring, dict) and authoring.get("run_id"):
            tenant_id, owner_user_id = self._scope(principal)
            self.authoring.delete(SkillAuthoringPointer(
                run_id=str(authoring["run_id"]),
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
            ))
        self.resources.delete_all(current, principal)
        self.registry.delete(skill_id, principal)

    def list_resources(
        self, skill_id: str, principal: Principal | None = None,
    ) -> list[SkillResourceRecord]:
        current = self.registry.get(skill_id, principal)
        return self.resources.list(current, principal)

    def add_resource(
        self,
        skill_id: str,
        *,
        kind: SkillResourceKind,
        filename: str,
        content: bytes,
        mime_type: str,
        principal: Principal | None = None,
    ) -> SkillResourceRecord:
        current = self.registry.get(skill_id, principal)
        resource = self.resources.add(
            current, principal, kind=kind, filename=filename,
            content=content, mime_type=mime_type,
        )
        self._sync_resources(skill_id, principal, change_type="resource_added")
        return resource

    def read_resource(
        self, skill_id: str, resource_id: str, principal: Principal | None = None,
    ) -> tuple[SkillResourceRecord, bytes]:
        current = self.registry.get(skill_id, principal)
        return self.resources.read(current, resource_id, principal)

    def delete_resource(
        self, skill_id: str, resource_id: str, principal: Principal | None = None,
    ) -> SkillRecord:
        current = self.registry.get(skill_id, principal)
        self.resources.delete(current, resource_id, principal)
        return self._sync_resources(skill_id, principal, change_type="resource_deleted")

    def export_package(self, skill_id: str, principal: Principal | None = None) -> bytes:
        item = self.registry.get(skill_id, principal)
        markdown = str(item.metadata.get("skill_md") or self.registry.export_markdown(skill_id, principal))
        openai_yaml = str(item.metadata.get("openai_yaml") or render_openai_yaml(
            item.name, item.display_name, item.description,
        ))
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{item.name}/SKILL.md", markdown)
            archive.writestr(f"{item.name}/agents/openai.yaml", openai_yaml)
            for resource in self.resources.list(item, principal):
                _, content = self.resources.read(item, resource.id, principal)
                archive.writestr(f"{item.name}/{resource.kind}/{resource.name}", content)
        return output.getvalue()

    def _metadata_with_resources(
        self, current: SkillRecord, principal: Principal | None,
    ) -> dict[str, object]:
        bindings = [item.model_dump(mode="json") for item in self.resources.list(current, principal)]
        return {**current.metadata, "resource_bindings": bindings, "scripts_executed": False}

    def _sync_resources(
        self, skill_id: str, principal: Principal | None, *, change_type: str,
    ) -> SkillRecord:
        current = self.registry.get(skill_id, principal)
        metadata = refresh_package_metadata(current, self._metadata_with_resources(current, principal))
        report = self.harness.evaluate(current, metadata)
        metadata = merge_harness_metadata(metadata, report)
        authoring = dict(metadata.get("authoring") or {})
        authoring.update({
            "status": "validated" if metadata["validation"].get("publishable") else "needs_input",
            "resumable": False,
            "graph_path": [*list(authoring.get("graph_path") or []), "resource_sync", "harness"],
        })
        metadata["authoring"] = authoring
        self.registry.replace_metadata(
            skill_id, metadata, principal, change_type=change_type,
            invalidate_activation=change_type in {"resource_added", "resource_deleted", "resources_imported"},
        )
        return self.registry.get(skill_id, principal)

    def _compress_long_conversation(self, title: str, transcript: str) -> str:
        reply = self.gateway.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "把长对话压缩为流程蓝图，只保留可复用的目标、输入、判断、工具步骤、校验、"
                        "审批点和输出；不得保留姓名、联系方式、证件号、金额、API密钥和具体候选人内容。"
                    ),
                },
                {"role": "user", "content": f"标题：{title}\n{transcript[-80_000:]}"},
            ],
            purpose="skill_conversation_blueprint",
            model=self.model,
            structured=False,
            max_tokens=3_000,
        )
        return _scrub_sensitive_examples(reply.content)[:40_000]

    def _start_authoring(
        self,
        *,
        task: str,
        material: str,
        preferred_name: str,
        source_type: SkillSource,
        principal: Principal | None,
        source_conversation_id: str | None = None,
        source_metadata: dict[str, object] | None = None,
    ) -> SkillAuthoringResult:
        tenant_id, owner_user_id = self._scope(principal)
        result = self.authoring.start(SkillAuthoringRequest(
            run_id=f"skillrun_{uuid4().hex}",
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            task=task,
            material=material,
            preferred_name=preferred_name,
            source_type=source_type,
            source_conversation_id=source_conversation_id,
            source_metadata=dict(source_metadata or {}),
        ))
        if result.draft is None:
            raise ValueError("skill_generation_validation_failed:" + ",".join(result.issues)[:300])
        return result

    @staticmethod
    def _draft_with_authoring(result: SkillAuthoringResult) -> SkillDraft:
        if result.draft is None:
            raise ValueError("skill_generation_missing_instructions")
        metadata = {
            **result.draft.metadata,
            "authoring": {
                "run_id": result.run_id,
                "status": result.status.value,
                "resumable": result.status == SkillAuthoringStatus.WAITING_REVIEW,
                "checkpoint_thread_id": result.checkpoint_thread_id,
                "attempt": result.attempt,
                "attempts": result.attempts,
                "issues": result.issues,
                "graph_path": result.graph_path,
                "content_sha256": result.draft.metadata.get("content_sha256"),
            },
        }
        return result.draft.model_copy(update={"metadata": metadata})

    def _rebind_completed_draft(
        self, current: SkillRecord, result: SkillAuthoringResult,
    ) -> SkillDraft:
        if result.draft is None:
            raise ValueError("skill_generation_missing_instructions")
        draft = result.draft.model_copy(update={
            "name": current.name,
            "display_name": current.display_name,
            "source_type": current.source_type,
            "source_conversation_id": current.source_conversation_id,
            "enabled": False,
            "status": "draft",
        })
        metadata = refresh_package_metadata(draft, dict(draft.metadata))
        draft = draft.model_copy(update={"metadata": metadata})
        report = self.harness.evaluate(draft, metadata)
        metadata = merge_harness_metadata(metadata, report)
        validation = dict(metadata.get("validation") or {})
        authoring_status = (
            SkillAuthoringStatus.WAITING_REVIEW
            if validation.get("publishable") and result.status == SkillAuthoringStatus.WAITING_REVIEW
            else result.status
        )
        metadata["authoring"] = {
            "run_id": result.run_id,
            "status": authoring_status.value,
            "resumable": authoring_status == SkillAuthoringStatus.WAITING_REVIEW,
            "checkpoint_thread_id": result.checkpoint_thread_id,
            "attempt": result.attempt,
            "attempts": result.attempts,
            "issues": result.issues,
            "graph_path": result.graph_path,
            "content_sha256": metadata.get("content_sha256"),
        }
        return draft.model_copy(update={"metadata": metadata})

    def _scope(self, principal: Principal | None) -> tuple[str, str]:
        if principal is None:
            return self.registry.default_tenant_id, ADMIN_USER_ID
        return principal.tenant_id, principal.user_id


def _parse_skill_markdown(markdown: str) -> tuple[str, str, str, str]:
    candidate = markdown.strip()
    frontmatter = ""
    body = candidate
    if candidate.startswith("---"):
        parts = candidate.split("---", 2)
        if len(parts) == 3:
            frontmatter, body = parts[1], parts[2]
    values: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip().casefold()] = value.strip().strip("\"'")
    heading = next((line.lstrip("# ").strip() for line in body.splitlines() if line.startswith("#")), "")
    name = values.get("name") or heading or "uploaded-skill"
    display_name = heading or values.get("display_name") or name
    description = values.get("description") or f"从 {display_name} 导入的用户 Skill"
    instructions = body.strip()
    if not instructions:
        raise ValueError("skill_instructions_required")
    return name, description, display_name, instructions[:12_000]


def _safe_zip_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return not normalized.startswith("/") and ".." not in normalized.split("/")


def _zip_entry_is_symlink(item: zipfile.ZipInfo) -> bool:
    return ((item.external_attr >> 16) & 0o170000) == 0o120000


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _scrub_sensitive_examples(value: str) -> str:
    cleaned = value
    replacements = (
        (r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号已移除]"),
        (r"(?<!\d)\d{17}[0-9Xx](?!\d)", "[证件号已移除]"),
        (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[邮箱已移除]"),
        (r"(?:sk|ak)-[A-Za-z0-9_-]{12,}", "[密钥已移除]"),
        (r"(?<!\d)(?:人民币|RMB|¥|￥)?\s*\d{4,}(?:\.\d{1,2})?\s*(?:元|万元)?", "[金额或编号已移除]"),
    )
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned
