from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConnectorProvider(StrEnum):
    FEISHU = "feishu"
    FAKE = "fake"


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    COMMITTED = "committed"
    FAILED = "failed"
    REJECTED = "rejected"


class ReceiptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ConnectorContext(BaseModel):
    tenant_id: str
    user_id: str
    provider: ConnectorProvider
    allowed_capabilities: list[str] = Field(default_factory=list)
    case_id: str | None = None
    run_id: str | None = None
    data_class: str = "internal"


class ExternalReadRequest(BaseModel):
    capability: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ExternalEvidence(BaseModel):
    source_id: str
    title: str
    content: str
    url: str | None = None
    observed_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(BaseModel):
    provider: ConnectorProvider
    capability: str
    evidence: list[ExternalEvidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExternalActionRequest(BaseModel):
    capability: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str
    risk_level: str = "medium"


class ProposedAction(BaseModel):
    id: str
    tenant_id: str
    owner_user_id: str
    provider: ConnectorProvider
    capability: str
    summary: str
    normalized_arguments: dict[str, Any] = Field(default_factory=dict)
    risk_level: str
    parameter_hash: str
    status: ProposalStatus = ProposalStatus.PROPOSED
    case_id: str | None = None
    run_id: str | None = None
    expires_at: str
    created_at: str
    updated_at: str


class ApprovalGrant(BaseModel):
    approved_by: str
    expected_parameter_hash: str
    approved_at: str = Field(default_factory=utc_now_iso)


class AdapterCommitResult(BaseModel):
    status: ReceiptStatus
    external_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None


class ActionReceipt(BaseModel):
    id: str
    proposal_id: str
    tenant_id: str
    owner_user_id: str
    provider: ConnectorProvider
    capability: str
    status: ReceiptStatus
    idempotency_key: str
    external_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    created_at: str
    updated_at: str


class WorkplaceConnector(Protocol):
    """Adapter seam. Tokens and provider tool names stay behind this interface."""

    provider: ConnectorProvider

    def healthy(self) -> bool: ...

    def supports(self, capability: str) -> bool: ...

    def read(
        self, request: ExternalReadRequest, context: ConnectorContext
    ) -> EvidenceBundle: ...

    def normalize_action(
        self, request: ExternalActionRequest, context: ConnectorContext
    ) -> dict[str, Any]: ...

    def commit(
        self, proposal: ProposedAction, *, idempotency_key: str
    ) -> AdapterCommitResult: ...


class ConnectorUnavailableError(RuntimeError):
    pass


class ConnectorAuthorizationError(RuntimeError):
    pass


class ProposalConflictError(RuntimeError):
    pass


class ProposalExpiredError(RuntimeError):
    pass

