from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.domains.channel_gateway import (
    ChannelBinding,
    ChannelContextPolicy,
    ChannelIngressReceipt,
    FeishuExternalGroupConfig,
    FeishuExternalGroupView,
    FeishuExternalRelayEvent,
)
from app.domains.identity import Principal
from app.runtime.container import (
    get_channel_gateway,
    get_channel_repository,
    get_settings,
)


router = APIRouter(prefix="/channels", tags=["channels"])


class FeishuBindingBody(BaseModel):
    external_tenant_key: str
    external_user_id: str
    internal_user_id: str


class FeishuGroupContextBody(BaseModel):
    external_tenant_key: str
    external_conversation_id: str
    enabled: bool = False
    max_messages: int = Field(default=12, ge=1, le=50)
    retention_hours: int = Field(default=72, ge=1, le=720)


class FeishuExternalGroupBody(BaseModel):
    group_id: str = Field(min_length=3, max_length=160)
    display_name: str = Field(min_length=1, max_length=160)
    owner_user_id: str = Field(min_length=1, max_length=160)
    webhook_url: str = Field(default="", max_length=1000)
    webhook_secret: str = Field(default="", max_length=500)
    enabled: bool = True


def _admin(principal: Principal = Depends(get_current_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin_required")
    return principal


@router.post("/feishu/events", response_model=ChannelIngressReceipt | dict[str, str])
async def receive_feishu_event(request: Request) -> ChannelIngressReceipt | dict[str, str]:
    settings = get_settings()
    if not settings.feishu_channel_enabled:
        raise HTTPException(status_code=503, detail="feishu_channel_disabled")
    payload: dict[str, Any] = await request.json()
    if "encrypt" in payload:
        raise HTTPException(
            status_code=422,
            detail="encrypted_events_require_long_connection_or_sdk_adapter",
        )
    if payload.get("type") == "url_verification":
        if settings.feishu_channel_verification_token and payload.get("token") != settings.feishu_channel_verification_token:
            raise HTTPException(status_code=401, detail="invalid_feishu_verification_token")
        return {"challenge": str(payload.get("challenge") or "")}
    header = payload.get("header") or {}
    if settings.feishu_channel_verification_token and header.get("token") != settings.feishu_channel_verification_token:
        raise HTTPException(status_code=401, detail="invalid_feishu_verification_token")
    try:
        if (payload.get("header") or {}).get("event_type") == "card.action.trigger":
            return get_channel_gateway().ingest_feishu_action(payload)
        return get_channel_gateway().ingest_feishu(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/feishu/external/events", response_model=ChannelIngressReceipt,
)
async def receive_feishu_external_event(request: Request) -> ChannelIngressReceipt:
    settings = get_settings()
    if not settings.feishu_external_relay_enabled:
        raise HTTPException(status_code=503, detail="feishu_external_relay_disabled")
    secret = settings.feishu_external_relay_secret.strip()
    if not secret:
        raise HTTPException(status_code=503, detail="feishu_external_relay_secret_missing")
    raw_body = await request.body()
    timestamp = request.headers.get("X-UniHR-Timestamp", "").strip()
    signature = request.headers.get("X-UniHR-Signature", "").strip()
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid_relay_timestamp") from exc
    if abs(int(time.time()) - timestamp_value) > settings.feishu_external_max_clock_skew_seconds:
        raise HTTPException(status_code=401, detail="expired_relay_request")
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    supplied = signature.removeprefix("sha256=")
    if not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="invalid_relay_signature")
    try:
        event = FeishuExternalRelayEvent.model_validate_json(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_external_event") from exc
    receipt = get_channel_gateway().ingest_external_feishu(event)
    if receipt.status == "ignored" and receipt.reason == "external_group_not_configured":
        raise HTTPException(status_code=404, detail=receipt.reason)
    return receipt


@router.post("/feishu/bindings", response_model=ChannelBinding)
def bind_feishu_user(
    body: FeishuBindingBody,
    principal: Principal = Depends(_admin),
) -> ChannelBinding:
    try:
        return get_channel_gateway().bind(
            ChannelBinding(
                external_tenant_key=body.external_tenant_key,
                external_user_id=body.external_user_id,
                tenant_id=principal.tenant_id,
                user_id=body.internal_user_id,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/feishu/external/groups", response_model=FeishuExternalGroupView,
)
def configure_feishu_external_group(
    body: FeishuExternalGroupBody,
    principal: Principal = Depends(_admin),
) -> FeishuExternalGroupView:
    if body.webhook_url.strip():
        _validate_external_webhook_url(body.webhook_url)
        try:
            get_channel_gateway().bind(ChannelBinding(
                provider="feishu_external",
                external_tenant_key=f"external:{body.group_id}",
                external_user_id="*",
                tenant_id=principal.tenant_id,
                user_id=body.owner_user_id,
            ))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return get_channel_repository().save_external_group(FeishuExternalGroupConfig(
        group_id=body.group_id,
        display_name=body.display_name,
        owner_user_id=body.owner_user_id,
        webhook_url=body.webhook_url,
        webhook_secret=body.webhook_secret,
        enabled=body.enabled,
    ))


@router.get(
    "/feishu/external/groups", response_model=list[FeishuExternalGroupView],
)
def list_feishu_external_groups(
    principal: Principal = Depends(_admin),
) -> list[FeishuExternalGroupView]:
    return get_channel_repository().list_external_groups()


@router.put("/feishu/group-context", response_model=ChannelContextPolicy)
def configure_feishu_group_context(
    body: FeishuGroupContextBody,
    principal: Principal = Depends(_admin),
) -> ChannelContextPolicy:
    return get_channel_repository().save_context_policy(ChannelContextPolicy(
        external_tenant_key=body.external_tenant_key,
        external_conversation_id=body.external_conversation_id,
        enabled=body.enabled,
        max_messages=body.max_messages,
        retention_hours=body.retention_hours,
    ))


@router.get("/feishu/group-context", response_model=list[ChannelContextPolicy])
def list_feishu_group_context(
    principal: Principal = Depends(_admin),
) -> list[ChannelContextPolicy]:
    return get_channel_repository().list_context_policies()


@router.get("/feishu/status")
def feishu_channel_status(
    principal: Principal = Depends(get_current_principal),
) -> dict[str, Any]:
    settings = get_settings()
    return {
        "enabled": settings.feishu_channel_enabled,
        "mode": settings.feishu_channel_mode,
        "credentials_configured": bool(settings.feishu_app_id and settings.feishu_app_secret),
        "verification_token_configured": bool(settings.feishu_channel_verification_token),
        "default_identity_enabled": bool(settings.feishu_channel_default_user_id),
        "external_relay_enabled": settings.feishu_external_relay_enabled,
        "external_relay_secret_configured": bool(settings.feishu_external_relay_secret),
        "external_group_count": len(get_channel_repository().list_external_groups()),
        "queue": get_channel_repository().status_counts(),
        "action_queue": get_channel_repository().action_status_counts(),
        "pending_bindings": (
            get_channel_repository().pending_bindings()
            if principal.role == "admin" else []
        ),
    }


def _validate_external_webhook_url(value: str) -> None:
    parsed = urlparse(value)
    allowed_hosts = {"open.feishu.cn", "open.larksuite.com"}
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() not in allowed_hosts
        or not parsed.path.startswith("/open-apis/bot/v2/hook/")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(status_code=422, detail="invalid_feishu_custom_bot_webhook")
