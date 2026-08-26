from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Protocol

from langgraph.checkpoint.memory import InMemorySaver

from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal
from app.domains.skills import SkillPlanningRequest, SkillRegistry, SkillService
from app.domains.workbench import UploadPayload, WorkbenchService
from app.models.usage_store import model_usage_scope

from .adapters import observe_workbench_response
from .contracts import EvalCase, EvaluationObservation


class ReplayAdapter(Protocol):
    def replay(
        self, case: EvalCase, principal: Principal, *, evaluation_run_id: str = "",
    ) -> EvaluationObservation: ...
    def close(self) -> None: ...


class InMemoryReplayAdapter:
    def __init__(self, observations: dict[str, EvaluationObservation]) -> None:
        self.observations = observations

    def replay(
        self, case: EvalCase, principal: Principal, *, evaluation_run_id: str = "",
    ) -> EvaluationObservation:
        del principal
        del evaluation_run_id
        return self.observations[case.id]

    def close(self) -> None:
        return None


class WorkbenchReplayAdapter:
    """Run the formal Workbench in an isolated data directory and SQLite database."""

    def __init__(self, source: WorkbenchService, *, project_root: Path) -> None:
        self.source = source
        self.project_root = Path(project_root)
        self._temp = TemporaryDirectory(prefix="unihr-quality-replay-")
        data_dir = Path(self._temp.name)
        settings = source.settings.model_copy(update={
            "data_dir": data_dir,
            "database_path": data_dir / "quality-replay.db",
        })
        repository = ConversationRepository(
            settings.database_path, default_tenant_id=settings.default_tenant_id,
        )
        self.workbench = WorkbenchService(
            settings, repository, model_gateway=source.gateway,
            knowledge_index=source.knowledge, skill_registry=source.skills,
            workflow_checkpointer=InMemorySaver(),
        )
        self.skill_registry = SkillRegistry(settings.database_path, settings.default_tenant_id)
        self.skill_service = SkillService(
            self.skill_registry, repository, source.gateway, settings.primary_model,
            workflow_checkpointer=InMemorySaver(),
        )

    def replay(
        self, case: EvalCase, principal: Principal, *, evaluation_run_id: str = "",
    ) -> EvaluationObservation:
        scope_id = f"quality:{evaluation_run_id}:{case.id}" if evaluation_run_id else ""
        with model_usage_scope(principal.tenant_id, principal.user_id, scope_id):
            observation = self._replay(case, principal)
        usage_store = getattr(self.source.gateway, "usage_store", None)
        if usage_store is None or not scope_id:
            return observation
        usage = usage_store.scope_summary(principal.tenant_id, scope_id)
        return observation.model_copy(update={
            "estimated_cost_cny": (
                usage["estimated_cost_cny"] if usage["calls"] else None
            ),
            "cost_pricing_configured": (
                usage["pricing_coverage"] >= 1 if usage["calls"] else None
            ),
            "model_call_count": usage["calls"],
            "model_used": usage["models"] or observation.model_used,
        })

    def _replay(self, case: EvalCase, principal: Principal) -> EvaluationObservation:
        started = perf_counter()
        if case.source.startswith("routing-golden"):
            result = self.source.router.interpret(
                case.prompt, active_context=case.active_context,
                has_active_result=case.has_active_result,
            )
            return EvaluationObservation(
                capability_id=result.selected_capability_id,
                business_intent=result.business_intent.value,
                turn_action=result.turn_action.value,
                retrieval_mode=result.retrieval_mode.value,
                execution_mode=result.execution_mode.value,
                delivery_mode=result.delivery_mode.value,
                routing_fallback_used=result.routing_fallback_used,
                matched_layer=result.matched_layer,
                duration_ms=_duration_ms(started),
            )
        if case.source.startswith("skill-selection-golden"):
            interpretation = self.source.router.interpret(
                case.prompt,
                active_context=case.active_context,
                has_active_result=case.has_active_result,
            )
            visible = self.source.skills.list(principal) if self.source.skills is not None else []
            available_input_kinds = {"text", "task_brief"}
            if case.attachment_fixtures:
                available_input_kinds.add("attachment")
            plan = self.source.skill_orchestrator.plan(
                SkillPlanningRequest(
                    query=interpretation.resolved_request or case.prompt,
                    task_intent=interpretation.task_intent,
                    required_capabilities=interpretation.required_capabilities,
                    available_input_kinds=available_input_kinds,
                ),
                visible,
            )
            by_id = {item.id: item for item in visible}
            return EvaluationObservation(
                capability_id=interpretation.selected_capability_id,
                business_intent=interpretation.business_intent.value,
                turn_action=interpretation.turn_action.value,
                retrieval_mode=interpretation.retrieval_mode.value,
                execution_mode=interpretation.execution_mode.value,
                delivery_mode=interpretation.delivery_mode.value,
                routing_fallback_used=interpretation.routing_fallback_used,
                matched_layer=interpretation.matched_layer,
                selected_skill_names=[
                    by_id[item.skill_id].name for item in plan.nodes if item.skill_id in by_id
                ],
                skill_candidate_strategy=plan.candidate_strategy,
                skill_semantic_used=plan.semantic_retrieval_used,
                skill_rerank_used=plan.rerank_used,
                skill_selection_fallback_used=plan.fallback_used,
                trace_events=[
                    *(["skill.semantic_retrieved"] if plan.semantic_retrieval_used else []),
                    *(["skill.candidates.reranked"] if plan.rerank_used else []),
                    "skill.plan.validated",
                ],
                trace_statuses={"skill.plan.validated": "completed"},
                duration_ms=_duration_ms(started),
                raw_refs={"skill_plan_status": plan.status},
            )
        if case.expectation.capability_id == "skill_authoring" or "skill" in case.tags:
            record = self.skill_service.generate(case.prompt, principal=principal)
            metadata = dict(record.metadata or {})
            authoring = dict(metadata.get("authoring") or {})
            validation = dict(metadata.get("validation") or {})
            eval_suite = dict(metadata.get("eval_suite") or {})
            passed = bool(validation.get("publishable")) and eval_suite.get("status") == "passed"
            return EvaluationObservation(
                business_intent="skill_create", capability_id="skill_authoring",
                graph_path=[str(item) for item in authoring.get("graph_path") or []],
                trace_events=["skill.draft.created"],
                trace_statuses={"skill.draft.created": "completed"},
                artifact_harness_passed=passed, answer_visible=bool(record.display_name),
                answer_excerpt=record.display_name, duration_ms=_duration_ms(started),
                raw_refs={"skill_id": record.id},
            )
        uploads = [self._fixture(path) for path in case.attachment_fixtures]
        response = self.workbench.handle_turn(
            message=case.prompt, conversation_id=None, task_mode=case.task_mode,
            uploads=uploads, document_font="仿宋_GB2312", document_font_size=16,
            principal=principal,
        )
        return observe_workbench_response(response, duration_ms=_duration_ms(started))

    def close(self) -> None:
        self.skill_service.close()
        self.workbench.close()
        self._temp.cleanup()

    def _fixture(self, relative_path: str) -> UploadPayload:
        path = (self.project_root / relative_path).resolve()
        if not path.is_file() or self.project_root.resolve() not in path.parents:
            raise FileNotFoundError(relative_path)
        mime_type = "text/plain" if path.suffix.casefold() in {".txt", ".md"} else "application/octet-stream"
        return UploadPayload(filename=path.name, mime_type=mime_type, content=path.read_bytes())


def _duration_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))
