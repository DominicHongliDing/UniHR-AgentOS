from __future__ import annotations

from typing import Any

from .contracts import (
    AdapterCommitResult,
    ConnectorContext,
    ConnectorProvider,
    EvidenceBundle,
    ExternalActionRequest,
    ExternalEvidence,
    ExternalReadRequest,
    ProposedAction,
    ReceiptStatus,
)


class FakeWorkplaceConnector:
    """Deterministic contract adapter for tests; never presented as a live connection."""

    provider = ConnectorProvider.FAKE

    def __init__(self, capabilities: set[str] | None = None) -> None:
        self.capabilities = capabilities or {
            "calendar.read_availability",
            "calendar.create_event",
        }
        self.commit_count = 0
        self.committed_keys: set[str] = set()

    def healthy(self) -> bool:
        return True

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def read(
        self, request: ExternalReadRequest, context: ConnectorContext
    ) -> EvidenceBundle:
        return EvidenceBundle(
            provider=self.provider,
            capability=request.capability,
            evidence=[
                ExternalEvidence(
                    source_id="fake-source-1",
                    title="Fake evidence",
                    content=str(request.arguments),
                    metadata={"test_only": True},
                )
            ],
        )

    def normalize_action(
        self, request: ExternalActionRequest, context: ConnectorContext
    ) -> dict[str, Any]:
        return dict(sorted(request.arguments.items()))

    def commit(
        self, proposal: ProposedAction, *, idempotency_key: str
    ) -> AdapterCommitResult:
        if idempotency_key not in self.committed_keys:
            self.committed_keys.add(idempotency_key)
            self.commit_count += 1
        return AdapterCommitResult(
            status=ReceiptStatus.SUCCEEDED,
            external_ids=["fake-event-1"],
            details={"test_only": True},
        )

