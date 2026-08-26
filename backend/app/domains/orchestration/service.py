from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.domains.identity import Principal

from .contracts import (
    WorkflowCase,
    WorkflowCaseStatus,
    WorkflowHandoff,
    WorkflowPlan,
    WorkflowStepStatus,
)
from .repository import WorkflowCaseRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowCaseService:
    """Small lifecycle API; all graph transition rules live behind this boundary."""

    def __init__(self, repository: WorkflowCaseRepository) -> None:
        self.repository = repository

    def start(self, plan: WorkflowPlan, conversation_id: str, principal: Principal) -> WorkflowCase:
        return self.repository.create(plan, conversation_id, principal)

    def get(self, case_id: str, principal: Principal) -> WorkflowCase:
        return self.repository.get(case_id, principal)

    def list_for_conversation(self, conversation_id: str, principal: Principal) -> list[WorkflowCase]:
        return self.repository.list_for_conversation(conversation_id, principal)

    def complete_step(
        self, case_id: str, step_id: str, principal: Principal, *,
        run_ids: list[str] | None = None, artifact_ids: list[str] | None = None,
        recruitment_case_id: str = "", summary: str = "", payload: dict[str, Any] | None = None,
    ) -> WorkflowCase:
        item = self.repository.get(case_id, principal)
        step = self._step(item, step_id)
        if step.status not in {WorkflowStepStatus.RUNNING, WorkflowStepStatus.PENDING}:
            raise ValueError("workflow_step_not_active")
        step.status = WorkflowStepStatus.SUCCEEDED
        step.started_at = step.started_at or _now()
        step.completed_at = _now()
        step.run_ids = list(dict.fromkeys([*step.run_ids, *(run_ids or [])]))
        step.artifact_ids = list(dict.fromkeys([*step.artifact_ids, *(artifact_ids or [])]))
        step.summary = summary.strip()[:4_000]
        item.artifact_ids = list(dict.fromkeys([*item.artifact_ids, *(artifact_ids or [])]))
        if recruitment_case_id:
            item.recruitment_case_id = recruitment_case_id
        next_step = self._first_ready_step(item)
        if next_step is None:
            item.status = WorkflowCaseStatus.SUCCEEDED
            item.current_step_id = ""
        elif next_step.requires_human_review:
            next_step.status = WorkflowStepStatus.WAITING_HUMAN
            next_step.started_at = _now()
            item.status = WorkflowCaseStatus.WAITING_HUMAN
            item.current_step_id = next_step.id
        else:
            next_step.status = WorkflowStepStatus.RUNNING
            next_step.started_at = _now()
            item.status = WorkflowCaseStatus.RUNNING
            item.current_step_id = next_step.id
        if next_step is not None:
            item.handoffs.append(WorkflowHandoff(
                id=f"handoff_{uuid4().hex}", from_step_id=step.id, to_step_id=next_step.id,
                summary=summary.strip()[:2_000], run_ids=run_ids or [], artifact_ids=artifact_ids or [],
                recruitment_case_id=recruitment_case_id, payload=payload or {}, created_at=_now(),
            ))
        return self.repository.save(item, principal)

    def approve_human_step(
        self, case_id: str, principal: Principal, *, comment: str,
        selected_candidate_ids: list[str],
    ) -> WorkflowCase:
        item = self.repository.get(case_id, principal)
        if item.status != WorkflowCaseStatus.WAITING_HUMAN:
            raise ValueError("workflow_case_not_waiting_human")
        step = self._step(item, item.current_step_id)
        if step.status != WorkflowStepStatus.WAITING_HUMAN:
            raise ValueError("workflow_human_step_not_waiting")
        step.status = WorkflowStepStatus.SUCCEEDED
        step.completed_at = _now()
        step.summary = comment.strip() or "人工确认采用当前候选人建议并进入材料生成。"
        item.decision_log.append({
            "action": "approve", "step_id": step.id, "comment": comment.strip(),
            "selected_candidate_ids": selected_candidate_ids,
            "decided_by": principal.display_name, "decided_at": _now(),
        })
        ready = self.ready_steps(item)
        for target in ready:
            target.status = WorkflowStepStatus.RUNNING
            target.started_at = _now()
            item.handoffs.append(WorkflowHandoff(
                id=f"handoff_{uuid4().hex}", from_step_id=step.id, to_step_id=target.id,
                summary="人工确认快照已交接给后续 Agent。",
                recruitment_case_id=item.recruitment_case_id,
                payload={"selected_candidate_ids": selected_candidate_ids}, created_at=_now(),
            ))
        item.status = WorkflowCaseStatus.RUNNING
        item.current_step_id = ready[0].id if ready else ""
        return self.repository.save(item, principal)

    def revise(self, case_id: str, principal: Principal, comment: str) -> WorkflowCase:
        item = self.repository.get(case_id, principal)
        if item.status != WorkflowCaseStatus.WAITING_HUMAN:
            raise ValueError("workflow_case_not_waiting_human")
        human = self._step(item, item.current_step_id)
        human.status = WorkflowStepStatus.PENDING
        human.started_at = ""
        recruitment = self._step(item, "screen_candidates")
        recruitment.status = WorkflowStepStatus.RUNNING
        recruitment.completed_at = ""
        recruitment.summary = ""
        item.status = WorkflowCaseStatus.RUNNING
        item.current_step_id = recruitment.id
        item.decision_log.append({
            "action": "revise", "step_id": human.id, "comment": comment.strip(),
            "decided_by": principal.display_name, "decided_at": _now(),
        })
        return self.repository.save(item, principal)

    def complete_active_step(
        self, case_id: str, step_id: str, principal: Principal, *,
        run_id: str, artifact_ids: list[str], artifact_kinds: list[str], summary: str,
        run_status: str = "succeeded", quality_issues: list[str] | None = None,
    ) -> WorkflowCase:
        item = self.repository.get(case_id, principal)
        step = self._step(item, step_id)
        if step.status != WorkflowStepStatus.RUNNING:
            raise ValueError("workflow_step_not_running")
        if run_status not in {"succeeded", "waiting_review"} or quality_issues:
            raise ValueError("workflow_agent_quality_gate_failed")
        delivered_kinds = set(artifact_kinds)
        if step.required_artifact_kinds and (
            not artifact_ids
            or not set(step.required_artifact_kinds).issubset(delivered_kinds)
        ):
            raise ValueError("workflow_required_artifact_missing")
        step.status = WorkflowStepStatus.SUCCEEDED
        step.run_ids = list(dict.fromkeys([*step.run_ids, run_id]))
        step.artifact_ids = list(dict.fromkeys([*step.artifact_ids, *artifact_ids]))
        step.summary = summary[:4_000]
        step.completed_at = _now()
        item.artifact_ids = list(dict.fromkeys([*item.artifact_ids, *artifact_ids]))
        remaining_running = [value for value in item.steps if value.status == WorkflowStepStatus.RUNNING]
        remaining_pending = [value for value in item.steps if value.status == WorkflowStepStatus.PENDING]
        if not remaining_running and not remaining_pending:
            failed = [value for value in item.steps if value.status == WorkflowStepStatus.FAILED]
            if failed:
                item.status = WorkflowCaseStatus.FAILED
                item.current_step_id = failed[0].id
            else:
                item.status = WorkflowCaseStatus.SUCCEEDED
                item.current_step_id = ""
        elif remaining_running:
            item.status = WorkflowCaseStatus.RUNNING
            item.current_step_id = remaining_running[0].id
        return self.repository.save(item, principal)

    def fail_step(
        self, case_id: str, step_id: str, principal: Principal, error_code: str, *,
        run_id: str = "", summary: str = "",
    ) -> WorkflowCase:
        item = self.repository.get(case_id, principal)
        step = self._step(item, step_id)
        step.status = WorkflowStepStatus.FAILED
        step.error_code = error_code[:160]
        if run_id:
            step.run_ids = list(dict.fromkeys([*step.run_ids, run_id]))
        step.summary = summary.strip()[:4_000] or "该 Agent 未满足本步骤的完成契约，可单独重试。"
        step.completed_at = _now()
        remaining_running = [
            value for value in item.steps
            if value.id != step.id and value.status == WorkflowStepStatus.RUNNING
        ]
        if remaining_running:
            item.status = WorkflowCaseStatus.RUNNING
            item.current_step_id = remaining_running[0].id
        else:
            item.status = WorkflowCaseStatus.FAILED
            item.current_step_id = step.id
        return self.repository.save(item, principal)

    def retry_failed_steps(
        self, case_id: str, principal: Principal, *, comment: str = "",
    ) -> WorkflowCase:
        item = self.repository.get(case_id, principal)
        if item.status != WorkflowCaseStatus.FAILED:
            raise ValueError("workflow_case_not_failed")
        failed = [step for step in item.steps if step.status == WorkflowStepStatus.FAILED]
        if not failed:
            raise ValueError("workflow_failed_step_missing")
        for step in failed:
            step.status = WorkflowStepStatus.RUNNING
            step.error_code = ""
            step.summary = "正在重试该 Agent 步骤。"
            step.started_at = _now()
            step.completed_at = ""
        running = [step for step in item.steps if step.status == WorkflowStepStatus.RUNNING]
        item.status = WorkflowCaseStatus.RUNNING
        item.current_step_id = running[0].id
        item.decision_log.append({
            "action": "retry",
            "step_ids": [step.id for step in failed],
            "comment": comment.strip(),
            "decided_by": principal.display_name,
            "decided_at": _now(),
        })
        return self.repository.save(item, principal)

    @staticmethod
    def ready_steps(item: WorkflowCase) -> list[Any]:
        completed = {
            step.id for step in item.steps
            if step.status in {WorkflowStepStatus.SUCCEEDED, WorkflowStepStatus.SKIPPED}
        }
        return [
            step for step in item.steps
            if step.status == WorkflowStepStatus.PENDING
            and all(parent in completed for parent in step.depends_on)
        ]

    @classmethod
    def _first_ready_step(cls, item: WorkflowCase) -> Any | None:
        ready = cls.ready_steps(item)
        return ready[0] if ready else None

    @staticmethod
    def _step(item: WorkflowCase, step_id: str) -> Any:
        for step in item.steps:
            if step.id == step_id:
                return step
        raise ValueError("workflow_step_not_found")
