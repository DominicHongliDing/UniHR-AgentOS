"""Run Feishu's official long-connection receiver as a local sidecar.

The sidecar owns no Agent logic. It only forwards verified SDK events to the
local Channel Gateway HTTP seam, so WebSocket and webhook modes have identical
identity, deduplication, session and retry behavior.
"""

from __future__ import annotations

import json
import os
import sys

import httpx
import lark_oapi as lark
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTriggerResponse,
)


LOCAL_EVENT_URL = os.getenv(
    "UNIHR_FEISHU_LOCAL_EVENT_URL",
    "http://127.0.0.1:8088/api/v1/channels/feishu/events",
)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing_configuration:{name}")
    return value


def _on_message(event: object) -> None:
    payload = json.loads(lark.JSON.marshal(event))
    try:
        response = httpx.post(LOCAL_EVENT_URL, json=payload, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # Raising makes the SDK report the event callback failure. The gateway's
        # event id remains the authoritative deduplication key on redelivery.
        raise RuntimeError("local_channel_gateway_unavailable") from exc


def _on_card_action(event: object) -> object:
    payload = json.loads(lark.JSON.marshal(event))
    try:
        response = httpx.post(LOCAL_EVENT_URL, json=payload, timeout=10)
        response.raise_for_status()
        receipt = response.json()
    except httpx.HTTPError as exc:
        raise RuntimeError("local_channel_gateway_unavailable") from exc
    toast_type = "success" if receipt.get("status") in {"queued", "duplicate"} else "warning"
    toast_text = "审批操作已提交，UniHR 正在校验权限并恢复任务。"
    return P2CardActionTriggerResponse(
        {"toast": {"type": toast_type, "content": toast_text}}
    )


def main() -> int:
    app_id = _required("UNIHR_FEISHU_APP_ID")
    app_secret = _required("UNIHR_FEISHU_APP_SECRET")
    encrypt_key = os.getenv("UNIHR_FEISHU_CHANNEL_ENCRYPT_KEY", "")
    verification_token = os.getenv("UNIHR_FEISHU_CHANNEL_VERIFICATION_TOKEN", "")
    handler = (
        lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
        .register_p2_im_message_receive_v1(_on_message)
        .register_p2_card_action_trigger(_on_card_action)
        .build()
    )
    # INFO logs include the full WebSocket URL and its short-lived ticket.
    # Keep production logs at ERROR so connection credentials never land on disk.
    lark.ws.Client(
        app_id,
        app_secret,
        log_level=lark.LogLevel.ERROR,
        event_handler=handler,
    ).start()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
