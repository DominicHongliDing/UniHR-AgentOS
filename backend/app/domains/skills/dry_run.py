from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator, Literal
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field

from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal
from app.domains.task_briefs import TaskBrief
from app.models.gateway import ModelGateway
from app.settings import Settings

from .contracts import SkillMatch, SkillRecord
from .registry import SkillRegistry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SkillDryRunArtifact(BaseModel):
    name: str
    kind: str
    mime_type: str
    byte_size: int = Field(ge=0)
    harness_status: str = "not_applicable"
    harness_issues: list[str] = Field(default_factory=list)


class SkillDryRunRecord(BaseModel):
    id: str
    skill_id: str
    skill_version: int
    skill_content_sha256: str
    tenant_id: str
    owner_user_id: str
    status: Literal["passed", "failed", "needs_input"]
    prompt: str
    answer_excerpt: str
    business_intent: str
    graph_path: list[str] = Field(default_factory=list)
    trace_events: list[str] = Field(default_factory=list)
    artifacts: list[SkillDryRunArtifact] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    created_at: str


class _SandboxSkillRegistry:
    """Expose exactly one reviewed draft as active, without mutating the real registry."""

    def __init__(self, source: SkillRegistry, skill: SkillRecord) -> None:
        self.source = source
        self.skill = skill.model_copy(update={"enabled": True, "status": "active"})
        # Runtime resources must still come from the real, governed resource store.
        self.database_path = source.database_path

    def select_by_ids(
        self, skill_ids: list[str], principal: Principal | None = None, max_results: int = 5,
    ) -> list[SkillMatch]:
        if self.skill.id not in skill_ids[:max_results]:
            return []
        return [SkillMatch(skill=self.skill, score=100.0, matched_trigger="dry_run_explicit")]

    def list(self, principal: Principal | None = None) -> list[SkillRecord]:
        """Expose only the reviewed draft to the runtime Skill Orchestrator."""
        if principal is not None and (
            principal.tenant_id != self.skill.tenant_id
            or self.skill.owner_user_id not in {"*", principal.user_id}
        ):
            return []
        return [self.skill]

    def select(
        self, query: str, max_results: int = 3, principal: Principal | None = None,
    ) -> list[SkillMatch]:
        return []

    @staticmethod
    def render_prompt(matches: list[SkillMatch]) -> str:
        return SkillRegistry.render_prompt(matches)


class SkillDryRunModule:
    """Run a draft Skill with real governed tools in a disposable workspace."""

    _recursive_capabilities = {"skill.generate", "skill.publish"}

    def __init__(
        self,
        settings: Settings,
        registry: SkillRegistry,
        model_gateway: ModelGateway,
        knowledge_index: Any | None = None,
    ) -> None:
        if settings.database_path is None:
            raise ValueError("database_path_required")
        self.settings = settings
        self.registry = registry
        self.gateway = model_gateway
        self.knowledge_index = knowledge_index
        self.database_path = Path(settings.database_path)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_dry_runs (
                    id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    skill_version INTEGER NOT NULL,
                    skill_content_sha256 TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    answer_excerpt TEXT NOT NULL,
                    business_intent TEXT NOT NULL,
                    graph_path_json TEXT NOT NULL DEFAULT '[]',
                    trace_events_json TEXT NOT NULL DEFAULT '[]',
                    artifacts_json TEXT NOT NULL DEFAULT '[]',
                    issues_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_skill_dry_runs_scope "
                "ON skill_dry_runs(skill_id, tenant_id, owner_user_id, created_at DESC)"
            )

    def list(self, skill_id: str, principal: Principal) -> list[SkillDryRunRecord]:
        self.registry.get(skill_id, principal)
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM skill_dry_runs
                WHERE skill_id=? AND tenant_id=? AND owner_user_id=?
                ORDER BY created_at DESC LIMIT 20""",
                (skill_id, principal.tenant_id, principal.user_id),
            ).fetchall()
        return [self._record(row) for row in rows]

    def run(
        self,
        skill_id: str,
        prompt: str,
        uploads: list[Any],
        principal: Principal,
    ) -> SkillDryRunRecord:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("dry_run_prompt_required")
        skill = self.registry.get(skill_id, principal)
        if self._recursive_capabilities.intersection(skill.capabilities):
            raise ValueError("recursive_skill_authoring_not_allowed_in_dry_run")

        # Lazy imports avoid coupling the Skill registry startup to the full Workbench graph.
        from app.domains.workbench import WorkbenchService

        response = None
        execution_error: Exception | None = None
        with TemporaryDirectory(prefix="unihr-skill-dry-run-") as temp_name:
            temp_root = Path(temp_name)
            sandbox_settings = self.settings.model_copy(update={
                "data_dir": temp_root / "data",
                "database_path": temp_root / "sandbox.db",
                "excel_automation_enabled": False,
                "word_automation_enabled": False,
            })
            sandbox_repository = ConversationRepository(
                sandbox_settings.database_path,
                default_tenant_id=principal.tenant_id,
                default_owner_user_id=principal.user_id,
            )
            workbench = WorkbenchService(
                sandbox_settings,
                sandbox_repository,
                model_gateway=self.gateway,
                knowledge_index=self.knowledge_index,
                skill_registry=_SandboxSkillRegistry(self.registry, skill),  # type: ignore[arg-type]
                workflow_checkpointer=InMemorySaver(),
            )
            try:
                response = workbench.handle_turn(
                    message=prompt,
                    conversation_id=None,
                    task_mode="自动选择",
                    uploads=uploads,
                    document_font="仿宋_GB2312",
                    document_font_size=16,
                    task_brief=TaskBrief(
                        kind="general",
                        objective=prompt,
                        selected_skill_ids=[skill.id],
                    ),
                    principal=principal,
                )
            except Exception as exc:  # A failed replay is a product result, not a lost HTTP request.
                execution_error = exc
            finally:
                workbench.close()

        if response is None:
            record = SkillDryRunRecord(
                id=f"sdr_{uuid4().hex}", skill_id=skill.id, skill_version=skill.version,
                skill_content_sha256=_skill_sha256(skill), tenant_id=principal.tenant_id,
                owner_user_id=principal.user_id, status="failed", prompt=_scrub(prompt, 600),
                answer_excerpt="隔离试运行执行失败；请检查模型、工具可用性或测试输入后重试。",
                business_intent="unknown", graph_path=[], trace_events=[], artifacts=[],
                issues=[f"execution_error:{type(execution_error).__name__}"], created_at=_now(),
            )
            self._save(record)
            return record

        trace_events = [str(item.get("event") or "") for item in response.trace if item.get("event")]
        artifacts = [self._artifact(item) for item in response.artifacts]
        issues = list(response.run.quality_issues)
        if "skill.selected" not in trace_events:
            issues.append("selected_skill_not_observed")
        required_event = _required_trace_event(skill.capabilities)
        if required_event and required_event not in trace_events:
            issues.append(f"required_tool_not_observed:{required_event}")
        for artifact in artifacts:
            if artifact.harness_status == "failed":
                issues.append(f"artifact_harness_failed:{artifact.name}")

        needs_input = bool({
            "skill.input.required", "skill.graph.node.waiting_input", "workflow.waiting_input",
        }.intersection(trace_events)) or response.run.status == "waiting_input"
        if needs_input:
            issues = [item for item in issues if not item.startswith("required_tool_not_observed:")]
        status_value: Literal["passed", "failed", "needs_input"] = (
            "needs_input" if needs_input else "failed" if issues or response.run.status == "failed" else "passed"
        )
        record = SkillDryRunRecord(
            id=f"sdr_{uuid4().hex}",
            skill_id=skill.id,
            skill_version=skill.version,
            skill_content_sha256=_skill_sha256(skill),
            tenant_id=principal.tenant_id,
            owner_user_id=principal.user_id,
            status=status_value,
            prompt=_scrub(prompt, 600),
            answer_excerpt=_scrub(response.assistant_message.content, 1_200),
            business_intent=str(response.interpretation.business_intent.value),
            graph_path=response.run.graph_path,
            trace_events=trace_events,
            artifacts=artifacts,
            issues=list(dict.fromkeys(issues)),
            created_at=_now(),
        )
        self._save(record)
        return record

    def _save(self, record: SkillDryRunRecord) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO skill_dry_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id, record.skill_id, record.skill_version, record.skill_content_sha256,
                    record.tenant_id, record.owner_user_id, record.status, record.prompt,
                    record.answer_excerpt, record.business_intent,
                    json.dumps(record.graph_path, ensure_ascii=False),
                    json.dumps(record.trace_events, ensure_ascii=False),
                    json.dumps([item.model_dump(mode="json") for item in record.artifacts], ensure_ascii=False),
                    json.dumps(record.issues, ensure_ascii=False), record.created_at,
                ),
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> SkillDryRunRecord:
        return SkillDryRunRecord(
            id=row["id"], skill_id=row["skill_id"], skill_version=row["skill_version"],
            skill_content_sha256=row["skill_content_sha256"], tenant_id=row["tenant_id"],
            owner_user_id=row["owner_user_id"], status=row["status"], prompt=row["prompt"],
            answer_excerpt=row["answer_excerpt"], business_intent=row["business_intent"],
            graph_path=json.loads(row["graph_path_json"]),
            trace_events=json.loads(row["trace_events_json"]),
            artifacts=[SkillDryRunArtifact.model_validate(item) for item in json.loads(row["artifacts_json"])],
            issues=json.loads(row["issues_json"]), created_at=row["created_at"],
        )

    @staticmethod
    def _artifact(item: Any) -> SkillDryRunArtifact:
        harness = dict(item.metadata.get("artifact_harness") or {})
        return SkillDryRunArtifact(
            name=item.name,
            kind=item.kind,
            mime_type=item.mime_type,
            byte_size=item.byte_size,
            harness_status=str(harness.get("status") or "not_applicable"),
            harness_issues=[str(value) for value in harness.get("issues") or []],
        )


def _skill_sha256(skill: SkillRecord) -> str:
    payload = skill.model_dump(mode="json", exclude={"created_at", "updated_at"})
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _required_trace_event(capabilities: list[str]) -> str:
    values = set(capabilities)
    if any(item.startswith("recruitment.") for item in values):
        return "recruitment.evaluate"
    if any(item.startswith("spreadsheet.") for item in values):
        return "spreadsheet.transform"
    if "document.compose" in values:
        return "document.compose"
    if "knowledge.retrieve" in values:
        return "policy.retrieve"
    return ""


def _scrub(value: str, limit: int) -> str:
    value = re.sub(r"\b1[3-9]\d{9}\b", "[手机号已隐藏]", value)
    value = re.sub(r"\b\d{17}[\dXx]\b", "[身份证号已隐藏]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已隐藏]", value)
    return value[:limit]
