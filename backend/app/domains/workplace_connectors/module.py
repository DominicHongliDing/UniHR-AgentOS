from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.domains.identity import Principal

from .contracts import (
    ActionReceipt,
    ApprovalGrant,
    ConnectorAuthorizationError,
    ConnectorContext,
    ConnectorProvider,
    ConnectorUnavailableError,
    EvidenceBundle,
    ExternalActionRequest,
    ExternalReadRequest,
    ProposalConflictError,
    ProposalExpiredError,
    ProposalStatus,
    ProposedAction,
    ReceiptStatus,
    WorkplaceConnector,
    utc_now_iso,
)
from .repository import WorkplaceConnectorRepository


class WorkplaceConnectorModule:
    """Deep module for governed reads and propose -> approve -> commit writes."""

    def __init__(
        self,
        repository: WorkplaceConnectorRepository,
        adapters: list[WorkplaceConnector],
        *,
        proposal_ttl_minutes: int = 30,
    ) -> None:
        self.repository = repository
        self.adapters = {adapter.provider: adapter for adapter in adapters}
        self.proposal_ttl_minutes = proposal_ttl_minutes

    def read(
        self,
        request: ExternalReadRequest,
        *,
        principal: Principal,
        context: ConnectorContext,
    ) -> EvidenceBundle:
        self._authorize(principal, context, request.capability)
        adapter = self._resolve(context.provider, request.capability)
        return adapter.read(request, context)

    def propose(
        self,
        request: ExternalActionRequest,
        *,
        principal: Principal,
        context: ConnectorContext,
    ) -> ProposedAction:
        self._authorize(principal, context, request.capability)
        adapter = self._resolve(context.provider, request.capability)
        normalized = adapter.normalize_action(request, context)
        parameter_hash = self._parameter_hash(
            provider=context.provider,
            capability=request.capability,
            arguments=normalized,
        )
        now = datetime.now(timezone.utc)
        proposal = ProposedAction(
            id=f"extprop_{uuid4().hex}",
            tenant_id=principal.tenant_id,
            owner_user_id=principal.user_id,
            provider=context.provider,
            capability=request.capability,
            summary=request.summary,
            normalized_arguments=normalized,
            risk_level=request.risk_level,
            parameter_hash=parameter_hash,
            case_id=context.case_id,
            run_id=context.run_id,
            expires_at=(now + timedelta(minutes=self.proposal_ttl_minutes)).isoformat(),
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        return self.repository.create_proposal(proposal)

    def commit(
        self,
        proposal_id: str,
        approval: ApprovalGrant,
        *,
        principal: Principal,
    ) -> ActionReceipt:
        proposal = self.repository.get_proposal(proposal_id, principal)
        existing = self.repository.get_receipt_for_proposal(proposal_id, principal)
        if existing is not None and existing.status == ReceiptStatus.SUCCEEDED:
            return existing
        if proposal.status not in {ProposalStatus.PROPOSED, ProposalStatus.FAILED}:
            raise ProposalConflictError(f"proposal_not_committable:{proposal.status.value}")
        if approval.approved_by != principal.user_id:
            raise ConnectorAuthorizationError("approval_principal_mismatch")
        if approval.expected_parameter_hash != proposal.parameter_hash:
            raise ProposalConflictError("proposal_parameters_changed")
        if datetime.fromisoformat(proposal.expires_at) <= datetime.now(timezone.utc):
            raise ProposalExpiredError("proposal_expired")

        adapter = self._resolve(proposal.provider, proposal.capability)
        idempotency_key = hashlib.sha256(
            f"{proposal.id}:{proposal.parameter_hash}".encode("utf-8")
        ).hexdigest()
        result = adapter.commit(proposal, idempotency_key=idempotency_key)
        now = utc_now_iso()
        receipt = ActionReceipt(
            id=existing.id if existing is not None else f"extrec_{uuid4().hex}",
            proposal_id=proposal.id,
            tenant_id=proposal.tenant_id,
            owner_user_id=proposal.owner_user_id,
            provider=proposal.provider,
            capability=proposal.capability,
            status=result.status,
            idempotency_key=idempotency_key,
            external_ids=result.external_ids,
            details=result.details,
            error_code=result.error_code,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.repository.save_receipt(receipt)
        self.repository.set_proposal_status(
            proposal.id,
            principal,
            ProposalStatus.COMMITTED
            if result.status == ReceiptStatus.SUCCEEDED
            else ProposalStatus.FAILED,
        )
        return receipt

    def reject(self, proposal_id: str, *, principal: Principal) -> ProposedAction:
        proposal = self.repository.get_proposal(proposal_id, principal)
        if proposal.status != ProposalStatus.PROPOSED:
            raise ProposalConflictError(f"proposal_not_rejectable:{proposal.status.value}")
        return self.repository.set_proposal_status(
            proposal_id, principal, ProposalStatus.REJECTED
        )

    def _resolve(
        self, provider: ConnectorProvider, capability: str
    ) -> WorkplaceConnector:
        adapter = self.adapters.get(provider)
        if adapter is None or not adapter.healthy() or not adapter.supports(capability):
            raise ConnectorUnavailableError(
                f"connector_unavailable:{provider.value}:{capability}"
            )
        return adapter

    @staticmethod
    def _authorize(
        principal: Principal, context: ConnectorContext, capability: str
    ) -> None:
        if (
            principal.tenant_id != context.tenant_id
            or principal.user_id != context.user_id
        ):
            raise ConnectorAuthorizationError("connector_context_owner_mismatch")
        if capability not in context.allowed_capabilities:
            raise ConnectorAuthorizationError("connector_capability_not_granted")

    @staticmethod
    def _parameter_hash(
        *, provider: ConnectorProvider, capability: str, arguments: dict[str, object]
    ) -> str:
        canonical = json.dumps(
            {
                "provider": provider.value,
                "capability": capability,
                "arguments": arguments,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

