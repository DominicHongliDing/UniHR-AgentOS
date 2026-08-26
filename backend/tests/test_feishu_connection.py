from __future__ import annotations

import httpx
from datetime import datetime, timedelta, timezone

from app.domains.identity import Principal
from app.domains.workplace_connectors.feishu_connection import (
    FeishuConnectionModule,
    FeishuConnectionRepository,
)
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


def test_unconfigured_feishu_is_explicit(tmp_path):
    settings = Settings(database_path=tmp_path / "db.sqlite")
    module = FeishuConnectionModule(
        settings,
        FeishuConnectionRepository(settings.database_path),
        cipher=FakeCipher(),
    )
    status = module.status(_principal())
    assert status.configured is False
    assert status.connected is False
    assert set(status.missing_configuration) == {"app_id", "app_secret", "token_encryption_key"}


def test_oauth_pkce_state_is_one_time_and_tokens_are_not_plaintext(tmp_path):
    settings = Settings(
        database_path=tmp_path / "db.sqlite",
        feishu_app_id="cli_test",
        feishu_app_secret="secret_test",
        connector_token_encryption_key="configured-for-fake-cipher",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/token"):
            body = request.read().decode("utf-8")
            assert "code_verifier" in body
            return httpx.Response(200, json={
                "code": 0,
                "access_token": "user-access-token",
                "refresh_token": "refresh-token",
                "expires_in": 7200,
                "refresh_token_expires_in": 604800,
                "scope": settings.feishu_oauth_scopes,
            })
        return httpx.Response(200, json={"code": 0, "name": "HR 专员", "open_id": "ou_test"})

    repository = FeishuConnectionRepository(settings.database_path)
    module = FeishuConnectionModule(
        settings,
        repository,
        cipher=FakeCipher(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    principal = _principal()
    started = module.start(principal)
    assert "code_challenge_method=S256" in started.authorization_url
    assert "client_id=cli_test" in started.authorization_url
    state = started.authorization_url.split("state=", 1)[1].split("&", 1)[0]

    completed = module.complete(principal, state=state, code="authorization-code")
    assert completed.connected is True
    assert completed.display_name == "HR 专员"
    stored = repository.get(principal)
    assert stored is not None
    assert stored.access_token_ciphertext == "encrypted:user-access-token"
    assert "user-access-token" != stored.access_token_ciphertext

    try:
        module.complete(principal, state=state, code="authorization-code")
        raise AssertionError("state should have been consumed")
    except ValueError as exc:
        assert str(exc) == "oauth_state_invalid"


def test_expired_access_token_is_refreshed_server_side(tmp_path):
    settings = Settings(
        database_path=tmp_path / "db.sqlite",
        feishu_app_id="cli_test",
        feishu_app_secret="secret_test",
        connector_token_encryption_key="configured-for-fake-cipher",
    )
    now = datetime.now(timezone.utc)
    repository = FeishuConnectionRepository(settings.database_path)
    from app.domains.workplace_connectors.feishu_connection import FeishuConnectionRecord

    repository.upsert(
        FeishuConnectionRecord(
            tenant_id="tenant_a",
            owner_user_id="hr_a",
            access_token_ciphertext="encrypted:expired-token",
            refresh_token_ciphertext="encrypted:refresh-token",
            expires_at=(now - timedelta(minutes=1)).isoformat(),
            refresh_expires_at=(now + timedelta(days=1)).isoformat(),
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/oauth/token")
        assert b'"grant_type":"refresh_token"' in request.content
        return httpx.Response(
            200,
            json={
                "code": 0,
                "access_token": "refreshed-token",
                "refresh_token": "next-refresh-token",
                "expires_in": 7200,
                "refresh_token_expires_in": 604800,
            },
        )

    module = FeishuConnectionModule(
        settings,
        repository,
        cipher=FakeCipher(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    token = module.access_token_for(tenant_id="tenant_a", owner_user_id="hr_a")
    assert token == "refreshed-token"
    assert repository.get(_principal()).access_token_ciphertext == "encrypted:refreshed-token"
