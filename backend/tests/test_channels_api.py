from __future__ import annotations

import hashlib
import hmac
import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import channels
from app.domains.channel_gateway import ChannelIngressReceipt


class FakeSettings:
    feishu_channel_enabled = True
    feishu_channel_verification_token = "verify-me"
    feishu_external_relay_enabled = True
    feishu_external_relay_secret = "external-relay-test-secret"
    feishu_external_max_clock_skew_seconds = 300


class FakeGateway:
    def ingest_feishu(self, payload):
        return ChannelIngressReceipt(
            event_id=payload["header"]["event_id"], status="queued"
        )

    def ingest_external_feishu(self, event):
        return ChannelIngressReceipt(
            event_id=f"external:{event.group_id}:{event.message_id}", status="queued"
        )


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(channels, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(channels, "get_channel_gateway", lambda: FakeGateway())
    app = FastAPI()
    app.include_router(channels.router, prefix="/api/v1")
    return TestClient(app)


def test_feishu_url_verification_requires_matching_token(monkeypatch):
    client = _client(monkeypatch)
    rejected = client.post(
        "/api/v1/channels/feishu/events",
        json={"type": "url_verification", "token": "wrong", "challenge": "abc"},
    )
    accepted = client.post(
        "/api/v1/channels/feishu/events",
        json={"type": "url_verification", "token": "verify-me", "challenge": "abc"},
    )
    assert rejected.status_code == 401
    assert accepted.json() == {"challenge": "abc"}


def test_feishu_event_is_acknowledged_after_durable_enqueue(monkeypatch):
    client = _client(monkeypatch)
    response = client.post(
        "/api/v1/channels/feishu/events",
        json={"header": {"event_id": "evt-1", "token": "verify-me"}},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_external_relay_requires_fresh_valid_hmac(monkeypatch):
    client = _client(monkeypatch)
    timestamp = str(int(time.time()))
    payload = {
        "group_id": "group-public",
        "message_id": "msg-1",
        "sender_id": "external-user",
        "text": "帮我查一下今天的公开新闻",
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    signature = hmac.new(
        FakeSettings.feishu_external_relay_secret.encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()

    rejected = client.post(
        "/api/v1/channels/feishu/external/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-UniHR-Timestamp": timestamp,
            "X-UniHR-Signature": "sha256=wrong",
        },
    )
    accepted = client.post(
        "/api/v1/channels/feishu/external/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-UniHR-Timestamp": timestamp,
            "X-UniHR-Signature": f"sha256={signature}",
        },
    )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "queued"
