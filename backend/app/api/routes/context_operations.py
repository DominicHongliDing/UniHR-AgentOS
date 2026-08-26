from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.api.deps import get_current_principal, require_permission
from app.domains.context_lifecycle import (
    ContextOperationsDashboard,
    ModelBudgetPolicy,
    ModelCostControl,
    ContextRetentionPolicy,
    ContextRetentionReport,
    ResourceQueryEvalReport,
)
from app.domains.context_lifecycle import SemanticFact
from app.domains.conversations.lifecycle import ConversationCleanupReport
from app.domains.identity import Principal
from app.domains.identity.contracts import ANSWER_HISTORY_MANAGE
from app.runtime.container import (
    get_context_operations,
    get_context_retention,
    get_conversation_lifecycle,
)


router = APIRouter(prefix="/context-operations", tags=["context-operations"])
admin_guard = require_permission(ANSWER_HISTORY_MANAGE)


class ResolveSemanticFactCommand(BaseModel):
    approved: bool


class RunRetentionCommand(BaseModel):
    execute: bool = False
    policy: ContextRetentionPolicy = ContextRetentionPolicy()


class UpdateModelBudgetCommand(BaseModel):
    monthly_budget_cny: float = 0
    per_run_budget_cny: float = 0
    daily_call_limit: int = 0
    warning_ratio: float = 0.8


@router.get(
    "/conversations/{conversation_id}",
    response_model=ContextOperationsDashboard,
)
def conversation_context_dashboard(
    conversation_id: str,
    principal: Principal = Depends(get_current_principal),
) -> ContextOperationsDashboard:
    try:
        return get_context_operations().conversation_dashboard(conversation_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation_not_found") from exc


@router.get("/deletion-audits", response_model=list[ConversationCleanupReport])
def list_own_deletion_audits(
    principal: Principal = Depends(get_current_principal),
) -> list[ConversationCleanupReport]:
    return get_conversation_lifecycle().list_audits(principal)


@router.post("/semantic-facts/{fact_id}/resolve", response_model=SemanticFact)
def resolve_semantic_fact(
    fact_id: str,
    command: ResolveSemanticFactCommand,
    principal: Principal = Depends(get_current_principal),
) -> SemanticFact:
    try:
        return get_context_operations().resolve_semantic_fact(
            fact_id, command.approved, principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="semantic_fact_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/admin/dashboard", response_model=ContextOperationsDashboard)
def tenant_context_dashboard(
    principal: Principal = Depends(admin_guard),
) -> ContextOperationsDashboard:
    return get_context_operations().tenant_dashboard(principal)


@router.put("/admin/model-budget", response_model=ModelCostControl)
def update_tenant_model_budget(
    command: UpdateModelBudgetCommand,
    principal: Principal = Depends(admin_guard),
) -> ModelCostControl:
    try:
        return get_context_operations().update_model_budget(
            principal,
            monthly_budget_cny=command.monthly_budget_cny,
            per_run_budget_cny=command.per_run_budget_cny,
            daily_call_limit=command.daily_call_limit,
            warning_ratio=command.warning_ratio,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/admin/deletion-audits", response_model=list[ConversationCleanupReport])
def list_tenant_deletion_audits(
    principal: Principal = Depends(admin_guard),
) -> list[ConversationCleanupReport]:
    return get_conversation_lifecycle().list_audits(principal, admin=True)


@router.post("/admin/resource-query-evaluations", response_model=ResourceQueryEvalReport)
async def run_resource_query_evaluation(
    principal: Principal = Depends(admin_guard),
) -> ResourceQueryEvalReport:
    return await run_in_threadpool(get_context_operations().run_resource_query_eval)


@router.post("/admin/retention-runs", response_model=ContextRetentionReport)
async def run_context_retention(
    command: RunRetentionCommand,
    principal: Principal = Depends(admin_guard),
) -> ContextRetentionReport:
    return await run_in_threadpool(
        get_context_retention().run,
        principal.tenant_id,
        command.policy,
        execute=command.execute,
    )
