from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Conversation(BaseModel):
    id: str
    tenant_id: str = "tenant_unihr_demo"
    owner_user_id: str = "usr_unihr_admin"
    title: str
    preview: str = ""
    pinned: bool = False
    created_at: str
    updated_at: str


class MessageRecord(BaseModel):
    id: str
    conversation_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str
    trace: list[dict[str, Any]] = Field(default_factory=list)
    task_brief: dict[str, Any] = Field(default_factory=dict)


class AttachmentRecord(BaseModel):
    id: str
    conversation_id: str
    message_id: str
    display_name: str
    mime_type: str
    extension: str
    byte_size: int
    saved_path: str
    extracted_text: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ArtifactRecord(BaseModel):
    id: str
    conversation_id: str
    message_id: str
    kind: str
    display_name: str
    path: str
    mime_type: str
    byte_size: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AnswerFeedback(BaseModel):
    id: str
    conversation_id: str
    message_id: str
    user_id: str
    helpful: bool
    comment: str = ""
    test_status: Literal["unreviewed", "added_to_test", "resolved"] = "unreviewed"
    created_at: str
    updated_at: str


class AdminConversationSummary(Conversation):
    owner_username: str
    owner_display_name: str
    message_count: int = 0
    feedback_count: int = 0


class AdminFeedbackRecord(AnswerFeedback):
    owner_username: str
    owner_display_name: str
    conversation_title: str
    question: str
    answer: str


class FeedbackStats(BaseModel):
    total: int
    helpful: int
    not_helpful: int
    helpful_rate: float | None
    pending_test_review: int
    added_to_test: int
