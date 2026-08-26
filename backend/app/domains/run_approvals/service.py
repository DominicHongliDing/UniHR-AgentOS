from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.identity import RUN_APPROVE, Principal
from app.domains.runs import RunPointer, RunRepository, RunStatus


@dataclass(frozen=True)
class RunApprovalResult:
    run: Any
    events: list[Any]
    approved: bool


class RunApprovalService:
    """One authorization and resume seam shared by Web and chat channels."""

    def __init__(
        self, *, repository: RunRepository, workflow: Any,
        conversations: Any, quality_operations: Any,
    ) -> None:
        self.repository = repository
        self.workflow = workflow
        self.conversations = conversations
        self.quality_operations = quality_operations

    def decide(
        self, *, run_id: str, principal: Principal,
        approved: bool, comment: str = "",
    ) -> RunApprovalResult:
        if not principal.can(RUN_APPROVE):
            raise PermissionError("permission_denied")
        try:
            current = self.repository.get_for_tenant(run_id, principal.tenant_id)
        except KeyError as exc:
            raise KeyError("run_not_found") from exc
        if current.status != RunStatus.WAITING_REVIEW:
            raise RuntimeError("run_is_not_waiting_for_review")
        pointer = RunPointer(
            run_id=current.id,
            tenant_id=current.tenant_id,
            owner_user_id=current.owner_user_id,
        )
        try:
            result = self.workflow.resume(
                pointer, approved=approved, comment=comment,
            )
        except KeyError as exc:
            raise RuntimeError("run_checkpoint_not_found") from exc
        self.repository.finish(result, pointer)
        self.repository.append_events(pointer, [
            {
                "event": "approval.decision",
                "status": "approved" if approved else "rejected",
                "summary": comment or ("人工复核通过" if approved else "人工复核退回"),
                "approved": approved,
                "decided_by": principal.user_id,
            },
            {
                "event": "graph.run.completed",
                "status": result.status.value,
                "summary": f"Run 恢复后状态：{result.status.value}",
                "graph_path": result.graph_path,
            },
        ])
        if not approved:
            messages, _, _, _ = self.conversations.admin_conversation_content(
                current.conversation_id, principal,
            )
            question = next(
                (item.content for item in reversed(messages) if item.role == "user"),
                f"人工复核退回 {current.business_intent.value} 任务",
            )
            self.quality_operations.capture_feedback(
                feedback_id=f"run_rejected:{current.id}",
                source_message_id=current.message_id or current.id,
                prompt=question,
                correction=comment or "人工复核退回，请重新检查结果和执行依据。",
                principal=principal,
            )
        return RunApprovalResult(
            run=self.repository.get(pointer),
            events=self.repository.list_events(pointer),
            approved=approved,
        )
