from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal, require_permission
from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import RUN_APPROVE, Principal
from app.domains.runs import (
    ApprovalQueueItem,
    RunEventRecord,
    RunPointer,
    RunRecord,
    RunRepository,
)
from app.runtime.container import (
    get_repository,
    get_run_approvals,
    get_run_repository,
)


router = APIRouter(tags=["runs"])
approval_guard = require_permission(RUN_APPROVE)


class RunDetail(BaseModel):
    run: RunRecord
    events: list[RunEventRecord]


class RunDecisionCommand(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=1_000)


class RunSkillRecoveryCommand(BaseModel):
    node_id: str = Field(default="", max_length=160)
    supplied_inputs: dict[str, object] = Field(default_factory=dict)
    comment: str = Field(default="", max_length=1_000)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(
    run_id: str,
    principal: Principal = Depends(get_current_principal),
    repository: RunRepository = Depends(get_run_repository),
) -> RunDetail:
    try:
        current = (
            repository.get_for_tenant(run_id, principal.tenant_id)
            if principal.can(RUN_APPROVE)
            else repository.get(RunPointer(
                run_id=run_id,
                tenant_id=principal.tenant_id,
                owner_user_id=principal.user_id,
            ))
        )
        pointer = RunPointer(
            run_id=current.id,
            tenant_id=current.tenant_id,
            owner_user_id=current.owner_user_id,
        )
        return RunDetail(run=current, events=repository.list_events(pointer))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run_not_found") from exc


@router.get("/approvals", response_model=list[ApprovalQueueItem])
def list_pending_approvals(
    principal: Principal = Depends(approval_guard),
    repository: RunRepository = Depends(get_run_repository),
) -> list[ApprovalQueueItem]:
    return repository.list_waiting_review(principal.tenant_id)


@router.get("/conversations/{conversation_id}/runs", response_model=list[RunRecord])
def list_conversation_runs(
    conversation_id: str,
    principal: Principal = Depends(get_current_principal),
    runs: RunRepository = Depends(get_run_repository),
    conversations: ConversationRepository = Depends(get_repository),
) -> list[RunRecord]:
    try:
        conversations.get_conversation(conversation_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation_not_found") from exc
    return runs.list_for_conversation(
        conversation_id, tenant_id=principal.tenant_id, owner_user_id=principal.user_id,
    )


@router.post("/runs/{run_id}/decision", response_model=RunDetail)
def decide_run(
    run_id: str,
    command: RunDecisionCommand,
    principal: Principal = Depends(get_current_principal),
    repository: RunRepository = Depends(get_run_repository),
) -> RunDetail:
    # Keep the HTTP edge fail-closed before resolving the shared service. The
    # domain service performs the same authorization for non-HTTP channels.
    if not principal.can(RUN_APPROVE):
        raise HTTPException(status_code=403, detail="permission_denied")
    try:
        result = get_run_approvals().decide(
            run_id=run_id,
            principal=principal,
            approved=command.approved,
            comment=command.comment,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="permission_denied") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run_not_found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RunDetail(run=result.run, events=result.events)


@router.post("/runs/{run_id}/skill-recovery", response_model=RunDetail)
def recover_skill_run(
    run_id: str,
    command: RunSkillRecoveryCommand,
    principal: Principal = Depends(get_current_principal),
    repository: RunRepository = Depends(get_run_repository),
) -> RunDetail:
    try:
        current = repository.get(RunPointer(
            run_id=run_id,
            tenant_id=principal.tenant_id,
            owner_user_id=principal.user_id,
        ))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run_not_found") from exc
    pointer = RunPointer(
        run_id=current.id,
        tenant_id=current.tenant_id,
        owner_user_id=current.owner_user_id,
    )
    try:
        get_workbench().recover_skill_run(
            pointer,
            principal=principal,
            node_id=command.node_id,
            supplied_inputs=command.supplied_inputs,
            comment=command.comment,
        )
    except KeyError as exc:
        raise HTTPException(status_code=409, detail="run_checkpoint_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RunDetail(run=repository.get(pointer), events=repository.list_events(pointer))
