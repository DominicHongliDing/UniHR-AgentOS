from __future__ import annotations

import hashlib
import operator
from enum import Enum
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from .contracts import SkillDraft, SkillSource
from .harness import SkillHarness, SkillHarnessReport, merge_harness_metadata
from .skill_builder import SkillBuildSpec, SkillBuilderAgent


class SkillAuthoringStatus(str, Enum):
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    WAITING_REVIEW = "waiting_review"
    FAILED = "failed"
    PUBLISHED = "published"
    REJECTED = "rejected"


class SkillAuthoringRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=100)
    tenant_id: str = Field(min_length=1, max_length=100)
    owner_user_id: str = Field(min_length=1, max_length=100)
    task: str = Field(min_length=1, max_length=2_000)
    material: str = Field(min_length=1, max_length=40_000)
    preferred_name: str = Field(default="", max_length=80)
    source_type: SkillSource
    source_conversation_id: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=2, ge=1, le=3)


class SkillAuthoringPointer(BaseModel):
    run_id: str
    tenant_id: str
    owner_user_id: str


class SkillAuthoringResult(BaseModel):
    run_id: str
    status: SkillAuthoringStatus
    draft: SkillDraft | None = None
    validation: dict[str, Any] = Field(default_factory=dict)
    harness: dict[str, Any] = Field(default_factory=dict)
    attempt: int = 0
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    graph_path: list[str] = Field(default_factory=list)
    approval_decision: bool | None = None
    approval_comment: str = ""
    checkpoint_thread_id: str


class _SkillAuthoringState(TypedDict, total=False):
    request: dict[str, Any]
    attempt: int
    attempts: Annotated[list[dict[str, Any]], operator.add]
    feedback: str
    spec: dict[str, Any]
    draft: dict[str, Any]
    validation: dict[str, Any]
    harness: dict[str, Any]
    issues: list[str]
    status: str
    approval_decision: bool | None
    approval_comment: str
    graph_path: Annotated[list[str], operator.add]


class SkillAuthoringGraph:
    """Durable author → validate → repair → harness → human-review workflow."""

    def __init__(
        self,
        builder: SkillBuilderAgent,
        *,
        checkpointer: BaseCheckpointSaver,
        harness: SkillHarness | None = None,
    ) -> None:
        self.builder = builder
        self.checkpointer = checkpointer
        self.harness = harness or SkillHarness()
        self._checkpoint_resource: Any | None = None
        self.graph = self._compile()

    @classmethod
    def with_postgres(
        cls,
        builder: SkillBuilderAgent,
        dsn: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 4,
    ) -> "SkillAuthoringGraph":
        pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_pool_size,
            max_size=max_pool_size,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=True,
        )
        saver = PostgresSaver(pool)
        saver.setup()
        runtime = cls(builder, checkpointer=saver)
        runtime._checkpoint_resource = pool
        return runtime

    def close(self) -> None:
        resource = self._checkpoint_resource
        self._checkpoint_resource = None
        if resource is not None:
            resource.close()

    def start(self, request: SkillAuthoringRequest) -> SkillAuthoringResult:
        values = self.graph.invoke(
            {
                "request": request.model_dump(mode="json"),
                "attempt": 0,
                "attempts": [],
                "feedback": "",
                "spec": {},
                "draft": {},
                "validation": {},
                "harness": {},
                "issues": [],
                "status": SkillAuthoringStatus.RUNNING.value,
                "approval_decision": None,
                "approval_comment": "",
                "graph_path": [],
            },
            config=self._config(request),
        )
        return self._to_result(values, self._thread_id(request))

    def resume(
        self,
        pointer: SkillAuthoringPointer,
        *,
        approved: bool,
        comment: str = "",
    ) -> SkillAuthoringResult:
        values = self.graph.invoke(
            Command(resume={"approved": approved, "comment": comment.strip()[:1_000]}),
            config=self._config(pointer),
        )
        if not values or "request" not in values:
            raise KeyError(pointer.run_id)
        return self._to_result(values, self._thread_id(pointer))

    def get(self, pointer: SkillAuthoringPointer) -> SkillAuthoringResult | None:
        snapshot = self.graph.get_state(self._config(pointer))
        values = dict(snapshot.values)
        if not values or "request" not in values:
            return None
        return self._to_result(values, self._thread_id(pointer))

    def delete(self, pointer: SkillAuthoringPointer) -> None:
        self.checkpointer.delete_thread(self._thread_id(pointer))

    def _compile(self):
        graph = StateGraph(_SkillAuthoringState)
        graph.add_node("author", self._author)
        graph.add_node("compile", self._compile_draft)
        graph.add_node("static_validate", self._static_validate)
        graph.add_node("repair", self._repair)
        graph.add_node("harness", self._run_harness)
        graph.add_node("needs_input", self._needs_input)
        graph.add_node("failed", self._failed)
        graph.add_node("human_review_gate", self._human_review_gate)
        graph.add_node("await_human_review", self._await_human_review)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "author")
        graph.add_conditional_edges(
            "author", self._route_after_author,
            {"compile": "compile", "repair": "repair", "failed": "failed"},
        )
        graph.add_edge("compile", "static_validate")
        graph.add_conditional_edges(
            "static_validate", self._route_after_static_validation,
            {
                "harness": "harness",
                "repair": "repair",
                "needs_input": "needs_input",
                "failed": "failed",
            },
        )
        graph.add_conditional_edges(
            "harness", self._route_after_harness,
            {"review": "human_review_gate", "repair": "repair", "failed": "failed"},
        )
        graph.add_edge("repair", "author")
        graph.add_edge("needs_input", "finalize")
        graph.add_edge("failed", "finalize")
        graph.add_edge("human_review_gate", "await_human_review")
        graph.add_edge("await_human_review", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=self.checkpointer, name="unihr-skill-authoring")

    def _author(self, state: _SkillAuthoringState) -> dict[str, Any]:
        request = SkillAuthoringRequest.model_validate(state["request"])
        attempt = int(state.get("attempt", 0)) + 1
        try:
            spec, attempt_trace = self.builder.author_spec(
                task=request.task,
                material=request.material,
                preferred_name=request.preferred_name,
                feedback=str(state.get("feedback") or ""),
                attempt=attempt,
            )
            return {
                "attempt": attempt,
                "attempts": [attempt_trace],
                "spec": spec.model_dump(mode="json"),
                "issues": [],
                "status": SkillAuthoringStatus.RUNNING.value,
                "graph_path": ["author" if attempt == 1 else "author.repair"],
            }
        except Exception as exc:
            issue = f"author_error:{str(exc)[:500]}"
            return {
                "attempt": attempt,
                "spec": {},
                "issues": [issue],
                "feedback": issue,
                "graph_path": ["author.failed"],
            }

    @staticmethod
    def _route_after_author(state: _SkillAuthoringState) -> Literal["compile", "repair", "failed"]:
        if state.get("spec"):
            return "compile"
        request = SkillAuthoringRequest.model_validate(state["request"])
        return "repair" if int(state.get("attempt", 0)) < request.max_attempts else "failed"

    def _compile_draft(self, state: _SkillAuthoringState) -> dict[str, Any]:
        request = SkillAuthoringRequest.model_validate(state["request"])
        spec = SkillBuildSpec.model_validate(state["spec"])
        result = self.builder.compile_spec(
            spec,
            preferred_name=request.preferred_name,
            source_type=request.source_type,
            source_conversation_id=request.source_conversation_id,
            source_metadata=request.source_metadata,
            attempts=list(state.get("attempts") or []),
        )
        return {
            "draft": result.draft.model_dump(mode="json"),
            "validation": result.validation,
            "issues": list(result.validation.get("failed_checks") or []),
            "graph_path": ["compile"],
        }

    @staticmethod
    def _static_validate(state: _SkillAuthoringState) -> dict[str, Any]:
        validation = dict(state.get("validation") or {})
        issues = [
            *list(validation.get("failed_checks") or []),
            *list(validation.get("blocking_issues") or []),
        ]
        return {"issues": list(dict.fromkeys(issues)), "graph_path": ["static_validate"]}

    @staticmethod
    def _route_after_static_validation(
        state: _SkillAuthoringState,
    ) -> Literal["harness", "repair", "needs_input", "failed"]:
        validation = dict(state.get("validation") or {})
        request = SkillAuthoringRequest.model_validate(state["request"])
        if validation.get("passed") and validation.get("publishable"):
            return "harness"
        if validation.get("passed") and validation.get("blocking_issues"):
            return "needs_input"
        return "repair" if int(state.get("attempt", 0)) < request.max_attempts else "failed"

    @staticmethod
    def _repair(state: _SkillAuthoringState) -> dict[str, Any]:
        feedback = "，".join(str(item) for item in state.get("issues") or [])[:2_000]
        return {
            "feedback": feedback or "未通过 Skill 质量门",
            "spec": {},
            "draft": {},
            "validation": {},
            "harness": {},
            "status": SkillAuthoringStatus.RUNNING.value,
            "graph_path": ["repair"],
        }

    def _run_harness(self, state: _SkillAuthoringState) -> dict[str, Any]:
        draft = SkillDraft.model_validate(state["draft"])
        report = self.harness.evaluate(draft, draft.metadata)
        metadata = merge_harness_metadata(draft.metadata, report)
        draft = draft.model_copy(update={"metadata": metadata})
        issues = list(report.failed_checks)
        if not report.checks.get("positive_trigger_replay"):
            failed_requests = [
                str(item.get("request") or "") for item in report.positive_results if not item.get("passed")
            ]
            issues.append(
                "正例未命中生产触发词；请让每个正例逐字包含至少一个 trigger_phrases。失败正例："
                + "｜".join(failed_requests)[:1_200]
            )
        if not report.checks.get("negative_trigger_replay"):
            issues.append("反例误命中触发词；请移除反例中的 trigger_phrases 原文。")
        if not report.checks.get("capabilities_allowlisted"):
            issues.extend(report.warnings)
        return {
            "draft": draft.model_dump(mode="json"),
            "validation": metadata["validation"],
            "harness": report.model_dump(mode="json"),
            "issues": issues,
            "graph_path": ["harness"],
        }

    @staticmethod
    def _route_after_harness(state: _SkillAuthoringState) -> Literal["review", "repair", "failed"]:
        harness = dict(state.get("harness") or {})
        request = SkillAuthoringRequest.model_validate(state["request"])
        if harness.get("passed"):
            return "review"
        return "repair" if int(state.get("attempt", 0)) < request.max_attempts else "failed"

    @staticmethod
    def _needs_input(state: _SkillAuthoringState) -> dict[str, Any]:
        return {"status": SkillAuthoringStatus.NEEDS_INPUT.value, "graph_path": ["needs_input"]}

    @staticmethod
    def _failed(state: _SkillAuthoringState) -> dict[str, Any]:
        return {"status": SkillAuthoringStatus.FAILED.value, "graph_path": ["failed"]}

    @staticmethod
    def _human_review_gate(state: _SkillAuthoringState) -> dict[str, Any]:
        return {
            "status": SkillAuthoringStatus.WAITING_REVIEW.value,
            "graph_path": ["human_review_gate"],
        }

    @staticmethod
    def _await_human_review(state: _SkillAuthoringState) -> dict[str, Any]:
        request = SkillAuthoringRequest.model_validate(state["request"])
        draft = SkillDraft.model_validate(state["draft"])
        decision = interrupt({
            "run_id": request.run_id,
            "skill_name": draft.name,
            "display_name": draft.display_name,
            "question": "请复核 Skill 的触发范围、权限、流程、校验和输出后决定是否启用。",
            "validation": state.get("validation") or {},
            "harness": state.get("harness") or {},
        })
        payload = decision if isinstance(decision, dict) else {"approved": bool(decision)}
        approved = bool(payload.get("approved"))
        return {
            "status": (
                SkillAuthoringStatus.PUBLISHED.value if approved else SkillAuthoringStatus.REJECTED.value
            ),
            "approval_decision": approved,
            "approval_comment": str(payload.get("comment") or "")[:1_000],
            "graph_path": ["approval.resume"],
        }

    @staticmethod
    def _finalize(state: _SkillAuthoringState) -> dict[str, Any]:
        return {"status": state.get("status") or SkillAuthoringStatus.FAILED.value, "graph_path": ["finalize"]}

    @classmethod
    def _to_result(cls, values: dict[str, Any], checkpoint_thread_id: str) -> SkillAuthoringResult:
        request = SkillAuthoringRequest.model_validate(values["request"])
        draft_value = values.get("draft")
        return SkillAuthoringResult(
            run_id=request.run_id,
            status=SkillAuthoringStatus(values.get("status") or SkillAuthoringStatus.FAILED.value),
            draft=SkillDraft.model_validate(draft_value) if draft_value else None,
            validation=dict(values.get("validation") or {}),
            harness=dict(values.get("harness") or {}),
            attempt=int(values.get("attempt", 0)),
            attempts=list(values.get("attempts") or []),
            issues=list(values.get("issues") or []),
            graph_path=list(values.get("graph_path") or []),
            approval_decision=values.get("approval_decision"),
            approval_comment=str(values.get("approval_comment") or ""),
            checkpoint_thread_id=checkpoint_thread_id,
        )

    @classmethod
    def _config(cls, pointer: SkillAuthoringPointer | SkillAuthoringRequest) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": cls._thread_id(pointer)}}

    @staticmethod
    def _thread_id(pointer: SkillAuthoringPointer | SkillAuthoringRequest) -> str:
        raw = f"{pointer.tenant_id}|{pointer.owner_user_id}|skill|{pointer.run_id}".encode("utf-8")
        return "unihr-skill-" + hashlib.sha256(raw).hexdigest()
