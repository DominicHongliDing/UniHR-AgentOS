from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.domains.identity import Principal
from app.domains.workplace_connectors import (
    ActionReceipt,
    ApprovalGrant,
    ConnectorAuthorizationError,
    ConnectorContext,
    ConnectorProvider,
    ConnectorUnavailableError,
    EvidenceBundle,
    ExternalActionRequest,
    ExternalReadRequest,
    FeishuAuthorizationStart,
    FeishuConnectionStatus,
    ProposalConflictError,
    ProposalExpiredError,
    ProposedAction,
)
from app.runtime.container import get_feishu_connections, get_workplace_connectors


router = APIRouter(prefix="/connections", tags=["connections"])


class FeishuReadBody(BaseModel):
    capability: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    case_id: str | None = None
    run_id: str | None = None


class FeishuActionBody(FeishuReadBody):
    summary: str
    risk_level: str = "medium"


class FeishuCommitBody(BaseModel):
    expected_parameter_hash: str


@router.get("/feishu", response_model=FeishuConnectionStatus)
def feishu_status(principal: Principal = Depends(get_current_principal)) -> FeishuConnectionStatus:
    return get_feishu_connections().status(principal)


@router.post("/feishu/authorize", response_model=FeishuAuthorizationStart)
def authorize_feishu(principal: Principal = Depends(get_current_principal)) -> FeishuAuthorizationStart:
    try:
        return get_feishu_connections().start(principal)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/feishu/callback", include_in_schema=False)
def feishu_callback(
    state: str = Query(...),
    code: str = Query(""),
    error: str = Query(""),
    principal: Principal = Depends(get_current_principal),
) -> RedirectResponse:
    if error or not code:
        return RedirectResponse("http://127.0.0.1:5180/#connections?feishu=denied")
    try:
        get_feishu_connections().complete(principal, state=state, code=code)
    except (ValueError, RuntimeError):
        return RedirectResponse("http://127.0.0.1:5180/#connections?feishu=failed")
    return RedirectResponse("http://127.0.0.1:5180/#connections?feishu=connected")


@router.delete("/feishu", response_model=FeishuConnectionStatus)
def disconnect_feishu(principal: Principal = Depends(get_current_principal)) -> FeishuConnectionStatus:
    return get_feishu_connections().disconnect(principal)


def _context(principal: Principal, body: FeishuReadBody) -> ConnectorContext:
    return ConnectorContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        provider=ConnectorProvider.FEISHU,
        allowed_capabilities=[body.capability],
        case_id=body.case_id,
        run_id=body.run_id,
    )


@router.post("/feishu/read", response_model=EvidenceBundle)
def read_feishu(
    body: FeishuReadBody,
    principal: Principal = Depends(get_current_principal),
) -> EvidenceBundle:
    try:
        return get_workplace_connectors().read(
            ExternalReadRequest(capability=body.capability, arguments=body.arguments),
            principal=principal,
            context=_context(principal, body),
        )
    except ConnectorAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConnectorUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/feishu/actions/propose", response_model=ProposedAction)
def propose_feishu_action(
    body: FeishuActionBody,
    principal: Principal = Depends(get_current_principal),
) -> ProposedAction:
    try:
        return get_workplace_connectors().propose(
            ExternalActionRequest(
                capability=body.capability,
                arguments=body.arguments,
                summary=body.summary,
                risk_level=body.risk_level,
            ),
            principal=principal,
            context=_context(principal, body),
        )
    except ConnectorAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConnectorUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/feishu/actions/{proposal_id}/commit", response_model=ActionReceipt)
def commit_feishu_action(
    proposal_id: str,
    body: FeishuCommitBody,
    principal: Principal = Depends(get_current_principal),
) -> ActionReceipt:
    try:
        return get_workplace_connectors().commit(
            proposal_id,
            ApprovalGrant(
                approved_by=principal.user_id,
                expected_parameter_hash=body.expected_parameter_hash,
            ),
            principal=principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="proposal_not_found") from exc
    except ConnectorAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ProposalConflictError, ProposalExpiredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConnectorUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/feishu/actions/{proposal_id}/reject", response_model=ProposedAction)
def reject_feishu_action(
    proposal_id: str,
    principal: Principal = Depends(get_current_principal),
) -> ProposedAction:
    try:
        return get_workplace_connectors().reject(proposal_id, principal=principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="proposal_not_found") from exc
    except ProposalConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
