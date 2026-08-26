"""Replay one Feishu-shaped event through the real UniHR Workbench.

This is a local acceptance harness: it writes the Conversation and Run to the
normal stores but records outbound text locally instead of contacting Feishu.
"""

from __future__ import annotations

import argparse
import json
from uuid import uuid4
from pathlib import Path

from app.domains.channel_gateway import (
    ChannelBinding,
    ChannelContextPolicy,
    ChannelGateway,
    ChannelGatewayRepository,
    FeishuEventDecoder,
)
from app.domains.channel_gateway.contracts import ChannelDelivery
from app.runtime.container import (
    get_identity,
    get_repository,
    get_settings,
    get_workbench,
    reset_container,
)


class RecordingTransport:
    def __init__(self) -> None:
        self.text = ""
        self.status_updates: list[dict[str, str]] = []
        self.files: list[str] = []

    def send_text(self, *, external_conversation_id: str, text: str) -> ChannelDelivery:
        self.text = text
        return ChannelDelivery(external_message_id=f"replay-{uuid4().hex}")

    def start_processing(self, *, external_conversation_id: str, text: str) -> ChannelDelivery:
        self.status_updates.append({"stage": "received", "detail": text})
        return ChannelDelivery(external_message_id=f"replay-progress-{uuid4().hex}")

    def update_processing(self, *, external_message_id: str, title: str, detail: str) -> None:
        self.status_updates.append({"stage": title, "detail": detail})

    def finish_processing(self, *, external_conversation_id: str, external_message_id: str | None, text: str, status: str, run_id: str | None = None) -> ChannelDelivery:
        self.text = text
        self.status_updates.append({"stage": status, "detail": text})
        return ChannelDelivery(external_message_id=external_message_id or f"replay-{uuid4().hex}")

    def send_file(self, *, external_conversation_id: str, file_path: Path, caption: str | None = None, display_name: str | None = None) -> ChannelDelivery:
        self.files.append(display_name or file_path.name)
        return ChannelDelivery(external_message_id=f"replay-file-{uuid4().hex}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("--user-id", default="usr_unihr_demo")
    parser.add_argument("--external-user-id", default="ou_local_acceptance")
    parser.add_argument("--chat-id", default="oc_local_acceptance")
    parser.add_argument(
        "--context-message", action="append", default=[],
        help="Authorized earlier group message; repeat to build a short context window.",
    )
    args = parser.parse_args()
    settings = get_settings()
    assert settings.database_path is not None
    transport = RecordingTransport()
    gateway = ChannelGateway(
        repository=ChannelGatewayRepository(settings.database_path),
        identity=get_identity(),
        workbench=get_workbench(),
        transport=transport,
        decoder=FeishuEventDecoder(),
        default_tenant_id=settings.default_tenant_id,
        lease_seconds=settings.feishu_channel_lease_seconds,
        retry_limit=settings.feishu_channel_retry_limit,
        artifact_repository=get_repository(),
    )
    gateway.bind(
        ChannelBinding(
            external_tenant_key="tenant_local_acceptance",
            external_user_id=args.external_user_id,
            tenant_id=settings.default_tenant_id,
            user_id=args.user_id,
        )
    )
    context_ingress = []
    if args.context_message:
        gateway.configure_context(ChannelContextPolicy(
            external_tenant_key="tenant_local_acceptance",
            external_conversation_id=args.chat_id,
            enabled=True,
            max_messages=min(12, len(args.context_message)),
            retention_hours=24,
        ))
        for index, context_text in enumerate(args.context_message, start=1):
            context_payload = {
                "schema": "2.0",
                "header": {
                    "event_id": f"evt-context-{uuid4().hex}",
                    "event_type": "im.message.receive_v1",
                    "tenant_key": "tenant_local_acceptance",
                },
                "event": {
                    "sender": {
                        "sender_type": "user",
                        "sender_id": {"open_id": f"ou_group_member_{index}"},
                    },
                    "message": {
                        "message_id": f"om-context-{uuid4().hex}",
                        "chat_id": args.chat_id,
                        "chat_type": "group",
                        "message_type": "text",
                        "content": json.dumps({"text": context_text}, ensure_ascii=False),
                        "mentions": [],
                    },
                },
            }
            context_ingress.append(
                gateway.ingest_feishu(context_payload).model_dump()
            )
    event_id = f"evt-replay-{uuid4().hex}"
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "tenant_key": "tenant_local_acceptance",
        },
        "event": {
            "sender": {
                "sender_type": "user",
                "sender_id": {"open_id": args.external_user_id},
            },
            "message": {
                "message_id": f"om-{uuid4().hex}",
                "chat_id": args.chat_id,
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": f"@_user_1 {args.text}"}, ensure_ascii=False),
                "mentions": [{"key": "@_user_1"}],
            },
        },
    }
    ingress = gateway.ingest_feishu(payload)
    result = gateway.execute_next("local-acceptance")
    print(json.dumps({
        "ingress": ingress.model_dump(),
        "context_ingress": context_ingress,
        "result": result.model_dump() if result else None,
        "reply": transport.text,
        "status_updates": transport.status_updates,
        "files": transport.files,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        reset_container()
