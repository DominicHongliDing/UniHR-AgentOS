from __future__ import annotations

import json

import httpx

from app.domains.workplace_connectors.feishu_bot import (
    FeishuBotClient,
    FeishuBotConfig,
    FeishuBotError,
)


def test_send_file_uses_app_token_upload_and_file_message(tmp_path):
    document = tmp_path / "产品白皮书.docx"
    document.write_bytes(b"word-file-content")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal/"):
            assert b'"app_id":"cli_test"' in request.content
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        assert request.headers["Authorization"] == "Bearer tenant-token"
        if request.url.path.endswith("/im/v1/files"):
            assert b"word-file-content" in request.content
            assert "multipart/form-data" in request.headers["Content-Type"]
            return httpx.Response(200, json={"code": 0, "data": {"file_key": "file_123"}})
        assert request.url.path.endswith("/im/v1/messages")
        assert request.url.params["receive_id_type"] == "chat_id"
        body = json.loads(request.content)
        assert body["receive_id"] == "oc_demo"
        assert body["msg_type"] == "file"
        assert json.loads(body["content"]) == {"file_key": "file_123"}
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"message_id": "om_123", "chat_id": "oc_demo"},
            },
        )

    client = FeishuBotClient(
        FeishuBotConfig(app_id="cli_test", app_secret="secret_test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    receipt = client.send_file(receive_id="oc_demo", file_path=document)

    assert receipt.safe_dict() == {
        "status": "sent",
        "chat_id": "oc_demo",
        "message_id": "om_123",
        "file_key": "file_123",
        "caption_message_id": None,
    }
    assert len(requests) == 3


def test_file_validation_rejects_missing_and_oversized_files(tmp_path):
    try:
        FeishuBotClient.validate_file(tmp_path / "missing.docx")
        raise AssertionError("missing file should fail")
    except FeishuBotError as exc:
        assert str(exc) == "file_not_found"

    oversized = tmp_path / "oversized.docx"
    with oversized.open("wb") as handle:
        handle.truncate(FeishuBotClient.MAX_FILE_BYTES + 1)
    try:
        FeishuBotClient.validate_file(oversized)
        raise AssertionError("oversized file should fail")
    except FeishuBotError as exc:
        assert str(exc) == "file_exceeds_30mb_limit"


def test_api_error_is_sanitized_and_keeps_request_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-tt-logid": "trace_123"},
            json={"code": 230035, "msg": "permission denied with sensitive detail"},
        )

    client = FeishuBotClient(
        FeishuBotConfig(app_id="cli_test", app_secret="secret_test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        client.send_text(receive_id="oc_demo", text="hello")
        raise AssertionError("permission error should fail")
    except FeishuBotError as exc:
        assert str(exc) == "feishu_api_230035:request_id=trace_123"


def test_card_can_be_sent_updated_and_message_resource_downloaded():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/auth/v3/tenant_access_token/internal/"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "tenant-token"}
            )
        assert request.headers["Authorization"] == "Bearer tenant-token"
        if path.endswith("/im/v1/messages"):
            body = json.loads(request.content)
            assert body["msg_type"] == "interactive"
            assert json.loads(body["content"])["header"]["template"] == "blue"
            return httpx.Response(
                200, json={"code": 0, "data": {"message_id": "om_card", "chat_id": "oc_demo"}}
            )
        if path.endswith("/im/v1/messages/om_card"):
            assert request.method == "PATCH"
            body = json.loads(request.content)
            assert json.loads(body["content"])["header"]["template"] == "green"
            return httpx.Response(
                200, json={"code": 0, "data": {"message_id": "om_card", "chat_id": "oc_demo"}}
            )
        assert path.endswith("/im/v1/messages/om_input/resources/file_key_1")
        assert request.url.params["type"] == "file"
        return httpx.Response(200, content=b"resume-pdf-bytes")

    client = FeishuBotClient(
        FeishuBotConfig(app_id="cli_test", app_secret="secret_test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    sent = client.send_card(
        receive_id="oc_demo",
        card={"header": {"template": "blue"}, "elements": []},
    )
    updated = client.update_card(
        message_id=sent.message_id,
        card={"header": {"template": "green"}, "elements": []},
    )
    resource = client.download_message_resource(
        message_id="om_input", resource_key="file_key_1", resource_type="file",
    )

    assert sent.message_id == updated.message_id == "om_card"
    assert resource == b"resume-pdf-bytes"
    assert len(requests) == 6


def test_import_docx_uploads_polls_shares_and_announces(tmp_path):
    document = tmp_path / "UniHR 产品白皮书.docx"
    document.write_bytes(b"editable-word-content")
    requests: list[httpx.Request] = []
    import_poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal import_poll_count
        requests.append(request)
        path = request.url.path
        if path.endswith("/auth/v3/tenant_access_token/internal/"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        assert request.headers["Authorization"] == "Bearer tenant-token"
        if path.endswith("/drive/v1/medias/upload_all"):
            assert b"editable-word-content" in request.content
            assert b"ccm_import_open" in request.content
            assert b'file_extension\\\"%3A\\\"docx' not in request.content
            assert b"docx" in request.content
            return httpx.Response(
                200, json={"code": 0, "data": {"file_token": "file_token_123"}}
            )
        if path.endswith("/drive/v1/import_tasks") and request.method == "POST":
            body = json.loads(request.content)
            assert body == {
                "file_extension": "docx",
                "file_token": "file_token_123",
                "type": "docx",
                "file_name": "UniHR AgentOS 白皮书",
                "point": {"mount_type": 1, "mount_key": "fld_demo"},
            }
            return httpx.Response(
                200, json={"code": 0, "data": {"ticket": "ticket_123"}}
            )
        if path.endswith("/drive/v1/import_tasks/ticket_123"):
            import_poll_count += 1
            if import_poll_count == 1:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {"result": {"job_status": 1, "ticket": "ticket_123"}},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "result": {
                            "job_status": 0,
                            "ticket": "ticket_123",
                            "type": "docx",
                            "token": "docx_token_123",
                            "url": "https://example.feishu.cn/docx/docx_token_123",
                        }
                    },
                },
            )
        if path.endswith("/drive/v1/permissions/docx_token_123/members"):
            assert request.url.params["type"] == "docx"
            assert request.url.params["need_notification"] == "false"
            assert json.loads(request.content) == {
                "member_type": "openchat",
                "member_id": "oc_demo",
                "perm": "edit",
                "type": "chat",
            }
            return httpx.Response(200, json={"code": 0, "data": {"member": {}}})
        assert path.endswith("/im/v1/messages")
        body = json.loads(request.content)
        assert body["receive_id"] == "oc_demo"
        assert "https://example.feishu.cn/docx/docx_token_123" in json.loads(
            body["content"]
        )["text"]
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"message_id": "om_announce", "chat_id": "oc_demo"},
            },
        )

    client = FeishuBotClient(
        FeishuBotConfig(app_id="cli_test", app_secret="secret_test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    receipt = client.import_docx(
        file_path=document,
        folder_token="fld_demo",
        title="UniHR AgentOS 白皮书",
        share_chat_id="oc_demo",
        share_perm="edit",
        announce_chat_id="oc_demo",
        poll_interval_seconds=0,
    )

    assert receipt.safe_dict() == {
        "status": "imported",
        "ticket": "ticket_123",
        "document_token": "docx_token_123",
        "document_url": "https://example.feishu.cn/docx/docx_token_123",
        "document_type": "docx",
        "job_status": 0,
        "shared_member_id": "oc_demo",
        "announcement_message_id": "om_announce",
    }
    assert len(requests) == 7


def test_import_validation_rejects_wrong_extension_and_oversized_docx(tmp_path):
    wrong_extension = tmp_path / "report.pdf"
    wrong_extension.write_bytes(b"pdf")
    try:
        FeishuBotClient.validate_import_file(wrong_extension)
        raise AssertionError("non-docx import should fail")
    except FeishuBotError as exc:
        assert str(exc) == "import_requires_docx"

    oversized = tmp_path / "oversized.docx"
    with oversized.open("wb") as handle:
        handle.truncate(FeishuBotClient.MAX_IMPORT_BYTES + 1)
    try:
        FeishuBotClient.validate_import_file(oversized)
        raise AssertionError("oversized import should fail")
    except FeishuBotError as exc:
        assert str(exc) == "file_exceeds_20mb_import_limit"
