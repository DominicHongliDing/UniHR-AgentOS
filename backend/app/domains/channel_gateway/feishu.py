from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from app.domains.workplace_connectors.feishu_bot import FeishuBotClient

from .contracts import (
    ChannelDelivery,
    ChannelResource,
    IncomingChannelAction,
    IncomingChannelAttachment,
    IncomingChannelMessage,
)


class FeishuEventDecoder:
    """Translate Feishu event-schema details into the channel-neutral contract."""

    EVENT_TYPE = "im.message.receive_v1"
    ACTION_EVENT_TYPE = "card.action.trigger"

    def decode(self, payload: dict[str, Any]) -> IncomingChannelMessage | None:
        header = payload.get("header") or {}
        event = payload.get("event") or {}
        if header.get("event_type") != self.EVENT_TYPE:
            return None
        sender = event.get("sender") or {}
        if sender.get("sender_type") == "app":
            return None
        sender_id = sender.get("sender_id") or {}
        message = event.get("message") or {}
        message_type = str(message.get("message_type") or "")
        if message_type not in {"text", "file", "image"}:
            return None
        try:
            content = json.loads(message.get("content") or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
        attachments: list[IncomingChannelAttachment] = []
        if message_type == "text":
            text = str(content.get("text") or "")
        elif message_type == "file":
            key = str(content.get("file_key") or "").strip()
            name = str(content.get("file_name") or "飞书附件").strip()
            if not key:
                return None
            attachments.append(IncomingChannelAttachment(
                resource_key=key,
                display_name=name,
                resource_type="file",
                mime_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            ))
            text = f"我上传了附件《{name}》，请读取附件并判断可以如何继续处理。"
        else:
            key = str(content.get("image_key") or "").strip()
            if not key:
                return None
            name = f"飞书图片-{message.get('message_id') or 'image'}.png"
            attachments.append(IncomingChannelAttachment(
                resource_key=key,
                display_name=name,
                resource_type="image",
                mime_type="image/png",
            ))
            text = "我上传了一张图片，请读取图片内容并判断可以如何继续处理。"
        for mention in message.get("mentions") or []:
            key = str((mention or {}).get("key") or "")
            if key:
                text = text.replace(key, " ")
        text = " ".join(text.split()).strip()
        event_id = str(header.get("event_id") or "").strip()
        tenant_key = str(header.get("tenant_key") or "").strip()
        external_user_id = str(sender_id.get("open_id") or "").strip()
        chat_id = str(message.get("chat_id") or "").strip()
        message_id = str(message.get("message_id") or "").strip()
        chat_type = str(message.get("chat_type") or "group")
        mentions = list(message.get("mentions") or [])
        # A mention-only group message is a valid wake-up signal.  Keep the
        # empty text so the gateway can safely resolve the sender's immediately
        # preceding group message instead of dropping the event here.
        if not all((event_id, tenant_key, external_user_id, chat_id, message_id)):
            return None
        if not text and not mentions and not attachments:
            return None
        return IncomingChannelMessage(
            event_id=event_id,
            external_tenant_key=tenant_key,
            external_user_id=external_user_id,
            external_conversation_id=chat_id,
            external_message_id=message_id,
            text=text,
            chat_type=chat_type,
            received_at=str(header.get("create_time") or message.get("create_time") or ""),
            attachments=attachments,
            # A file/image event cannot carry an @ mention inside its content;
            # receiving that attachment is itself the explicit user action.
            explicitly_invoked=chat_type != "group" or bool(mentions) or bool(attachments),
        )

    def decode_action(self, payload: dict[str, Any]) -> IncomingChannelAction | None:
        header = payload.get("header") or {}
        if header.get("event_type") != self.ACTION_EVENT_TYPE:
            return None
        event = payload.get("event") or {}
        operator = event.get("operator") or {}
        action = event.get("action") or {}
        value = action.get("value") or {}
        context = event.get("context") or {}
        decision = str(value.get("action") or "").strip()
        run_id = str(value.get("run_id") or "").strip()
        event_id = str(header.get("event_id") or "").strip()
        tenant_key = str(operator.get("tenant_key") or header.get("tenant_key") or "").strip()
        open_id = str(operator.get("open_id") or "").strip()
        chat_id = str(context.get("open_chat_id") or "").strip()
        message_id = str(context.get("open_message_id") or "").strip()
        if decision not in {"approve", "reject"}:
            return None
        if not all((event_id, tenant_key, open_id, chat_id, message_id, run_id)):
            return None
        return IncomingChannelAction(
            event_id=event_id,
            external_tenant_key=tenant_key,
            external_user_id=open_id,
            external_conversation_id=chat_id,
            external_message_id=message_id,
            action=decision,
            run_id=run_id,
            comment=str(value.get("comment") or "")[:1000],
        )


class FeishuTextTransport:
    def __init__(self, client: FeishuBotClient) -> None:
        self.client = client

    def send_text(
        self, *, external_conversation_id: str, text: str
    ) -> ChannelDelivery:
        receipt = self.client.send_text(
            receive_id=external_conversation_id,
            receive_id_type="chat_id",
            text=text,
        )
        return ChannelDelivery(external_message_id=receipt.message_id)

    def start_processing(
        self, *, external_conversation_id: str, text: str,
    ) -> ChannelDelivery:
        receipt = self.client.send_card(
            receive_id=external_conversation_id,
            card=_progress_card("已接收", text, template="blue"),
        )
        return ChannelDelivery(external_message_id=receipt.message_id)

    def update_processing(
        self, *, external_message_id: str, title: str, detail: str,
    ) -> None:
        self.client.update_card(
            message_id=external_message_id,
            card=_progress_card(title, detail, template="blue"),
        )

    def finish_processing(
        self, *, external_conversation_id: str,
        external_message_id: str | None, text: str, status: str,
        run_id: str | None = None,
    ) -> ChannelDelivery:
        title = "等待人工复核" if status == "awaiting_review" else "已完成"
        template = "orange" if status == "awaiting_review" else "green"
        card = _progress_card(
            title, text, template=template,
            approval_run_id=run_id if status == "awaiting_review" else None,
        )
        if external_message_id:
            receipt = self.client.update_card(message_id=external_message_id, card=card)
        else:
            receipt = self.client.send_card(receive_id=external_conversation_id, card=card)
        return ChannelDelivery(external_message_id=receipt.message_id)

    def send_file(
        self, *, external_conversation_id: str, file_path: Path,
        caption: str | None = None, display_name: str | None = None,
    ) -> ChannelDelivery:
        receipt = self.client.send_file(
            receive_id=external_conversation_id,
            receive_id_type="chat_id",
            file_path=file_path,
            caption=caption,
            display_name=display_name,
        )
        return ChannelDelivery(external_message_id=receipt.message_id)


class FeishuResourceLoader:
    def __init__(self, client: FeishuBotClient) -> None:
        self.client = client

    def load(self, message: IncomingChannelMessage) -> list[ChannelResource]:
        return [
            ChannelResource(
                filename=attachment.display_name,
                mime_type=attachment.mime_type,
                content=self.client.download_message_resource(
                    message_id=message.external_message_id,
                    resource_key=attachment.resource_key,
                    resource_type=attachment.resource_type,
                ),
            )
            for attachment in message.attachments
        ]


def _progress_card(
    title: str, detail: str, *, template: str,
    approval_run_id: str | None = None,
) -> dict[str, Any]:
    safe_detail = detail.strip() or "UniHR 正在处理当前任务。"
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": safe_detail},
    ]
    if approval_run_id:
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "确认通过"},
                    "type": "primary",
                    "value": {"action": "approve", "run_id": approval_run_id},
                    "confirm": {
                        "title": {"tag": "plain_text", "content": "确认通过？"},
                        "text": {"tag": "plain_text", "content": "系统将恢复原任务并记录审批人。"},
                    },
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "退回修改"},
                    "type": "danger",
                    "value": {
                        "action": "reject", "run_id": approval_run_id,
                        "comment": "通过飞书卡片退回修改",
                    },
                },
            ],
        })
    elements.append({
        "tag": "note",
        "elements": [
            {"tag": "plain_text", "content": "同一任务会在 Web 工作台保留 Conversation 与 Run Trace"}
        ],
    })
    return {
        "config": {
            "wide_screen_mode": True,
            "enable_forward": True,
            "update_multi": True,
        },
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": f"UniHR · {title}"},
        },
        "elements": elements,
    }
