from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class IncomingChannelAttachment(BaseModel):
    resource_key: str
    display_name: str
    resource_type: Literal["file", "image"] = "file"
    mime_type: str = "application/octet-stream"


class IncomingChannelMessage(BaseModel):
    provider: Literal["feishu", "feishu_external"] = "feishu"
    event_id: str
    external_tenant_key: str
    external_user_id: str
    external_conversation_id: str
    external_message_id: str
    text: str
    chat_type: str = "group"
    received_at: str = ""
    attachments: list[IncomingChannelAttachment] = Field(default_factory=list)
    explicitly_invoked: bool = True
    access_scope: Literal["internal", "external_public"] = "internal"


class IncomingChannelAction(BaseModel):
    provider: Literal["feishu"] = "feishu"
    event_id: str
    external_tenant_key: str
    external_user_id: str
    external_conversation_id: str
    external_message_id: str
    action: Literal["approve", "reject"]
    run_id: str
    comment: str = ""


class FeishuExternalGroupConfig(BaseModel):
    """Governed mapping between an external Feishu group and a UniHR owner."""

    group_id: str = Field(min_length=3, max_length=160)
    display_name: str = Field(min_length=1, max_length=160)
    owner_user_id: str = Field(min_length=1, max_length=160)
    # Published app-bot groups do not need a custom webhook.  The webhook is
    # only required when a Feishu Integration relay is used for transport.
    webhook_url: str = Field(default="", max_length=1000)
    webhook_secret: str = Field(default="", max_length=500)
    enabled: bool = True


class FeishuExternalGroupView(BaseModel):
    group_id: str
    display_name: str
    owner_user_id: str
    enabled: bool = True
    webhook_configured: bool = True
    webhook_secret_configured: bool = False


class FeishuExternalRelayEvent(BaseModel):
    """Channel-neutral payload emitted by Feishu Integration Platform."""

    group_id: str = Field(min_length=3, max_length=160)
    message_id: str = Field(min_length=3, max_length=200)
    sender_id: str = Field(min_length=1, max_length=200)
    sender_name: str = Field(default="外部群成员", max_length=160)
    text: str = Field(min_length=1, max_length=20_000)
    created_at: str = ""


class ChannelBinding(BaseModel):
    provider: str = "feishu"
    external_tenant_key: str
    external_user_id: str
    tenant_id: str
    user_id: str
    active: bool = True


class ChannelContextPolicy(BaseModel):
    provider: str = "feishu"
    external_tenant_key: str
    external_conversation_id: str
    enabled: bool = False
    max_messages: int = Field(default=12, ge=1, le=50)
    retention_hours: int = Field(default=72, ge=1, le=720)


class ChannelIngressReceipt(BaseModel):
    event_id: str
    status: Literal["queued", "duplicate", "ignored"]
    reason: str = ""


class ChannelProcessResult(BaseModel):
    event_id: str
    status: Literal["delivered", "ready_to_deliver", "failed", "unbound", "denied"]
    internal_conversation_id: str | None = None
    internal_run_id: str | None = None
    external_message_id: str | None = None
    error_code: str = ""


class ChannelDelivery(BaseModel):
    external_message_id: str


class ChannelTransport(Protocol):
    def send_text(
        self, *, external_conversation_id: str, text: str
    ) -> ChannelDelivery: ...

    def start_processing(
        self, *, external_conversation_id: str, text: str
    ) -> ChannelDelivery: ...

    def update_processing(
        self, *, external_message_id: str, title: str, detail: str
    ) -> None: ...

    def finish_processing(
        self, *, external_conversation_id: str,
        external_message_id: str | None, text: str, status: str,
        run_id: str | None = None,
    ) -> ChannelDelivery: ...

    def send_file(
        self, *, external_conversation_id: str, file_path: Path,
        caption: str | None = None, display_name: str | None = None,
    ) -> ChannelDelivery: ...


class ChannelResource(BaseModel):
    filename: str
    mime_type: str
    content: bytes


class ChannelResourceLoader(Protocol):
    def load(self, message: IncomingChannelMessage) -> list[ChannelResource]: ...


class WorkbenchPort(Protocol):
    def handle_turn(self, **kwargs: object) -> object: ...


class ChannelApprovalHandler(Protocol):
    def decide(
        self, *, run_id: str, principal: object,
        approved: bool, comment: str = "",
    ) -> object: ...
