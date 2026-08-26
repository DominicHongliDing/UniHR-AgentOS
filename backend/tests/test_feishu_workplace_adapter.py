from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.domains.identity import Principal
from app.domains.workplace_connectors import (
    ApprovalGrant,
    ConnectorContext,
    ConnectorProvider,
    ExternalActionRequest,
    ExternalReadRequest,
    FeishuConnectionModule,
    FeishuConnectionRepository,
    FeishuWorkplaceConnector,
    WorkplaceConnectorModule,
    WorkplaceConnectorRepository,
)
from app.domains.workplace_connectors.feishu_connection import FeishuConnectionRecord
from app.settings import Settings


class FakeCipher:
    def encrypt(self, value: str) -> str:
        return f"encrypted:{value}"

    def decrypt(self, value: str) -> str:
        return value.removeprefix("encrypted:")


def _principal() -> Principal:
    return Principal(
        user_id="hr_a",
        username="hr_a",
        display_name="HR A",
        role="user",
        tenant_id="tenant_a",
        permissions=[],
    )


def _configured(tmp_path, handler):
    settings = Settings(
        database_path=tmp_path / "db.sqlite",
        feishu_app_id="cli_test",
        feishu_app_secret="secret_test",
        connector_token_encryption_key="configured",
    )
    repository = FeishuConnectionRepository(settings.database_path)
    now = datetime.now(timezone.utc)
    repository.upsert(
        FeishuConnectionRecord(
            tenant_id="tenant_a",
            owner_user_id="hr_a",
            access_token_ciphertext="encrypted:live-user-token",
            refresh_token_ciphertext="encrypted:refresh-token",
            scopes=["offline_access"],
            expires_at=(now + timedelta(hours=1)).isoformat(),
            refresh_expires_at=(now + timedelta(days=7)).isoformat(),
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
    )
    connection = FeishuConnectionModule(settings, repository, cipher=FakeCipher())
    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = FeishuWorkplaceConnector(settings, connection, client=client)
    module = WorkplaceConnectorModule(
        WorkplaceConnectorRepository(settings.database_path), [adapter]
    )
    return module, connection


def _context(*capabilities: str) -> ConnectorContext:
    return ConnectorContext(
        tenant_id="tenant_a",
        user_id="hr_a",
        provider=ConnectorProvider.FEISHU,
        allowed_capabilities=list(capabilities),
        case_id="case_demo",
        run_id="run_demo",
    )


def test_live_document_search_is_normalized_as_external_evidence(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer live-user-token"
        assert request.url.path.endswith("/suite/docs-api/search/object")
        assert b'"search_key":"offer"' in request.content
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "docs_entities": [
                        {
                            "token": "doc_1",
                            "title": "Offer 审批说明",
                            "url": "https://example.feishu.cn/docx/doc_1",
                        }
                    ]
                },
            },
        )

    module, connection = _configured(tmp_path, handler)
    principal = _principal()
    bundle = module.read(
        ExternalReadRequest(
            capability="workspace.search_documents",
            arguments={"search_key": "offer"},
        ),
        principal=principal,
        context=_context("workspace.search_documents"),
    )

    assert connection.status(principal).capabilities[0].available is True
    assert bundle.provider == ConnectorProvider.FEISHU
    assert bundle.evidence[0].source_id == "doc_1"
    assert bundle.evidence[0].metadata == {"provider": "feishu", "live": True}


def test_calendar_write_is_only_called_after_matching_human_approval(tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.endswith("/calendar/v4/calendars/primary/events")
        assert request.url.params.get("idempotency_key")
        return httpx.Response(
            200,
            json={"code": 0, "data": {"event": {"event_id": "event_123"}}},
        )

    module, _ = _configured(tmp_path, handler)
    principal = _principal()
    proposal = module.propose(
        ExternalActionRequest(
            capability="calendar.create_event",
            summary="创建候选人复试日程",
            arguments={
                "calendar_id": "primary",
                "summary": "候选人复试",
                "starts_at": "2026-08-18T14:00:00+08:00",
                "ends_at": "2026-08-18T15:00:00+08:00",
            },
        ),
        principal=principal,
        context=_context("calendar.create_event"),
    )
    assert requests == []

    receipt = module.commit(
        proposal.id,
        ApprovalGrant(
            approved_by=principal.user_id,
            expected_parameter_hash=proposal.parameter_hash,
        ),
        principal=principal,
    )
    assert receipt.external_ids == ["event_123"]
    assert len(requests) == 1


def test_changed_artifact_is_blocked_after_approval_snapshot(tmp_path):
    uploaded = tmp_path / "result.docx"
    uploaded.write_bytes(b"version-one")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("changed artifact must never be uploaded")

    module, _ = _configured(tmp_path, handler)
    principal = _principal()
    proposal = module.propose(
        ExternalActionRequest(
            capability="drive.upload_artifact",
            summary="上传已通过校验的材料",
            arguments={"artifact_path": str(uploaded), "parent_node": "folder_1"},
        ),
        principal=principal,
        context=_context("drive.upload_artifact"),
    )
    uploaded.write_bytes(b"version-two")
    receipt = module.commit(
        proposal.id,
        ApprovalGrant(
            approved_by=principal.user_id,
            expected_parameter_hash=proposal.parameter_hash,
        ),
        principal=principal,
    )
    assert receipt.status.value == "failed"
    assert receipt.error_code == "artifact_changed_after_approval"
