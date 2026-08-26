from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from uuid import uuid4

from .contracts import ChannelDelivery, ChannelTransport
from .repository import ChannelGatewayRepository


EXTERNAL_TARGET_PREFIX = "external:"


class FeishuExternalWebhookTransport:
    """Deliver public-mode answers through an external-group custom bot."""

    def __init__(
        self, repository: ChannelGatewayRepository, *, timeout_seconds: float = 10.0,
    ) -> None:
        self.repository = repository
        self.timeout_seconds = timeout_seconds

    def send_text(
        self, *, external_conversation_id: str, text: str,
    ) -> ChannelDelivery:
        self._send(external_conversation_id, text)
        return ChannelDelivery(external_message_id=f"external_{uuid4().hex}")

    def start_processing(
        self, *, external_conversation_id: str, text: str,
    ) -> ChannelDelivery:
        self._send(external_conversation_id, f"UniHR 已接收：{text}")
        return ChannelDelivery(external_message_id=f"external_progress_{uuid4().hex}")

    def update_processing(
        self, *, external_message_id: str, title: str, detail: str,
    ) -> None:
        # External custom-bot webhooks cannot mutate an existing message.
        return None

    def finish_processing(
        self, *, external_conversation_id: str,
        external_message_id: str | None, text: str, status: str,
        run_id: str | None = None,
    ) -> ChannelDelivery:
        heading = "UniHR · 等待内部复核" if status == "awaiting_review" else "UniHR · 已完成"
        self._send(external_conversation_id, f"{heading}\n\n{text}")
        return ChannelDelivery(external_message_id=f"external_result_{uuid4().hex}")

    def send_file(
        self, *, external_conversation_id: str, file_path: Path,
        caption: str | None = None, display_name: str | None = None,
    ) -> ChannelDelivery:
        raise RuntimeError("external_group_file_delivery_not_supported")

    def _send(self, target: str, text: str) -> None:
        group_id = _group_id(target)
        config = self.repository.external_group(group_id)
        if config is None:
            raise RuntimeError("external_group_not_configured")
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": text[:18_000]},
        }
        secret = str(config.get("webhook_secret") or "")
        if secret:
            timestamp = str(int(time.time()))
            sign_key = f"{timestamp}\n{secret}".encode("utf-8")
            signature = base64.b64encode(
                hmac.new(sign_key, digestmod=hashlib.sha256).digest()
            ).decode("ascii")
            payload.update({"timestamp": timestamp, "sign": signature})
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            str(config["webhook_url"]), data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            response_body = response.read(64_000)
        try:
            result = json.loads(response_body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("external_webhook_invalid_response") from exc
        code = result.get("code", result.get("StatusCode", 0))
        if code not in {0, "0", None}:
            raise RuntimeError("external_webhook_delivery_failed")


class RoutedFeishuTransport:
    """Keep internal app-bot and external custom-bot delivery behind one port."""

    def __init__(
        self, internal: ChannelTransport, external: ChannelTransport,
    ) -> None:
        self.internal = internal
        self.external = external

    def _for(self, target: str) -> ChannelTransport:
        return self.external if target.startswith(EXTERNAL_TARGET_PREFIX) else self.internal

    def send_text(self, *, external_conversation_id: str, text: str) -> ChannelDelivery:
        return self._for(external_conversation_id).send_text(
            external_conversation_id=external_conversation_id, text=text,
        )

    def start_processing(self, *, external_conversation_id: str, text: str) -> ChannelDelivery:
        return self._for(external_conversation_id).start_processing(
            external_conversation_id=external_conversation_id, text=text,
        )

    def update_processing(
        self, *, external_message_id: str, title: str, detail: str,
    ) -> None:
        if external_message_id.startswith("external_"):
            return self.external.update_processing(
                external_message_id=external_message_id, title=title, detail=detail,
            )
        return self.internal.update_processing(
            external_message_id=external_message_id, title=title, detail=detail,
        )

    def finish_processing(
        self, *, external_conversation_id: str,
        external_message_id: str | None, text: str, status: str,
        run_id: str | None = None,
    ) -> ChannelDelivery:
        return self._for(external_conversation_id).finish_processing(
            external_conversation_id=external_conversation_id,
            external_message_id=external_message_id, text=text,
            status=status, run_id=run_id,
        )

    def send_file(
        self, *, external_conversation_id: str, file_path: Path,
        caption: str | None = None, display_name: str | None = None,
    ) -> ChannelDelivery:
        return self._for(external_conversation_id).send_file(
            external_conversation_id=external_conversation_id, file_path=file_path,
            caption=caption, display_name=display_name,
        )


def _group_id(target: str) -> str:
    if not target.startswith(EXTERNAL_TARGET_PREFIX):
        raise RuntimeError("invalid_external_group_target")
    value = target[len(EXTERNAL_TARGET_PREFIX):].strip()
    if not value:
        raise RuntimeError("invalid_external_group_target")
    return value
