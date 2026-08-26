from __future__ import annotations

import pytest

from app.domains.identity import Principal
from app.domains.workplace_connectors import (
    ApprovalGrant,
    ConnectorAuthorizationError,
    ConnectorContext,
    ConnectorProvider,
    ExternalActionRequest,
    ExternalReadRequest,
    FakeWorkplaceConnector,
    ProposalConflictError,
    WorkplaceConnectorModule,
    WorkplaceConnectorRepository,
)


def _principal(user_id: str = "hr_a", tenant_id: str = "tenant_a") -> Principal:
    return Principal(
        user_id=user_id,
        username=user_id,
        display_name=user_id,
        role="user",
        tenant_id=tenant_id,
        permissions=[],
    )


def _context(principal: Principal) -> ConnectorContext:
    return ConnectorContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        provider=ConnectorProvider.FAKE,
        allowed_capabilities=[
            "calendar.read_availability",
            "calendar.create_event",
        ],
        case_id="case_1",
        run_id="run_1",
    )


def _module(tmp_path):
    adapter = FakeWorkplaceConnector()
    module = WorkplaceConnectorModule(
        WorkplaceConnectorRepository(tmp_path / "connectors.db"), [adapter]
    )
    return module, adapter


def test_read_is_owner_scoped_and_capability_granted(tmp_path):
    module, _ = _module(tmp_path)
    principal = _principal()
    bundle = module.read(
        ExternalReadRequest(
            capability="calendar.read_availability",
            arguments={"people": ["teacher_a"]},
        ),
        principal=principal,
        context=_context(principal),
    )
    assert bundle.capability == "calendar.read_availability"
    assert bundle.evidence[0].metadata["test_only"] is True

    with pytest.raises(ConnectorAuthorizationError):
        module.read(
            ExternalReadRequest(capability="workspace.search_documents"),
            principal=principal,
            context=_context(principal),
        )


def test_propose_has_no_side_effect_and_commit_checks_snapshot(tmp_path):
    module, adapter = _module(tmp_path)
    principal = _principal()
    proposal = module.propose(
        ExternalActionRequest(
            capability="calendar.create_event",
            summary="创建候选人复试日程",
            arguments={"title": "复试", "starts_at": "2026-08-18T14:00:00+08:00"},
        ),
        principal=principal,
        context=_context(principal),
    )
    assert adapter.commit_count == 0

    with pytest.raises(ProposalConflictError):
        module.commit(
            proposal.id,
            ApprovalGrant(
                approved_by=principal.user_id,
                expected_parameter_hash="changed-after-review",
            ),
            principal=principal,
        )
    assert adapter.commit_count == 0


def test_commit_is_durable_and_idempotent(tmp_path):
    module, adapter = _module(tmp_path)
    principal = _principal()
    proposal = module.propose(
        ExternalActionRequest(
            capability="calendar.create_event",
            summary="创建候选人复试日程",
            arguments={"title": "复试", "starts_at": "2026-08-18T14:00:00+08:00"},
        ),
        principal=principal,
        context=_context(principal),
    )
    grant = ApprovalGrant(
        approved_by=principal.user_id,
        expected_parameter_hash=proposal.parameter_hash,
    )

    first = module.commit(proposal.id, grant, principal=principal)
    second = module.commit(proposal.id, grant, principal=principal)

    assert first.id == second.id
    assert first.external_ids == ["fake-event-1"]
    assert adapter.commit_count == 1


def test_proposal_cannot_be_read_or_committed_by_another_user(tmp_path):
    module, _ = _module(tmp_path)
    owner = _principal()
    proposal = module.propose(
        ExternalActionRequest(
            capability="calendar.create_event",
            summary="创建复试日程",
            arguments={"title": "复试"},
        ),
        principal=owner,
        context=_context(owner),
    )
    intruder = _principal("hr_b")
    with pytest.raises(KeyError):
        module.commit(
            proposal.id,
            ApprovalGrant(
                approved_by=intruder.user_id,
                expected_parameter_hash=proposal.parameter_hash,
            ),
            principal=intruder,
        )

