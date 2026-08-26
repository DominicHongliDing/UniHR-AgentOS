from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_principal
from app.domains.identity import Principal
from app.domains.orchestration import WorkflowCase, WorkflowCaseAction
from app.runtime.container import get_workbench


router = APIRouter(prefix="/workflow-cases", tags=["workflow-cases"])


@router.get("/{case_id}", response_model=WorkflowCase)
def get_workflow_case(
    case_id: str,
    principal: Principal = Depends(get_current_principal),
) -> WorkflowCase:
    try:
        return get_workbench().workflow_cases.get(case_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="workflow_case_not_found") from exc


@router.post("/{case_id}/actions", response_model=WorkflowCase)
def apply_workflow_case_action(
    case_id: str,
    command: WorkflowCaseAction,
    principal: Principal = Depends(get_current_principal),
) -> WorkflowCase:
    try:
        return get_workbench().continue_workflow_case(
            case_id,
            action=command.action,
            comment=command.comment,
            selected_candidate_ids=command.selected_candidate_ids,
            principal=principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="workflow_case_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

