from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domains.assistant.contracts import TurnInterpretation
from app.domains.conversations.contracts import Conversation, MessageRecord
from app.domains.orchestration import WorkflowCase


class AttachmentView(BaseModel):
    id: str
    name: str
    mime_type: str
    byte_size: int
    kind: str
    summary: dict[str, Any] = Field(default_factory=dict)


class ArtifactView(BaseModel):
    id: str
    kind: str
    name: str
    mime_type: str
    byte_size: int
    download_url: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunView(BaseModel):
    id: str
    status: str
    iteration: int
    max_iterations: int
    graph_path: list[str] = Field(default_factory=list)
    quality_issues: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


class WorkbenchTurnResponse(BaseModel):
    conversation: Conversation
    user_message: MessageRecord
    assistant_message: MessageRecord
    interpretation: TurnInterpretation
    attachments: list[AttachmentView] = Field(default_factory=list)
    artifacts: list[ArtifactView] = Field(default_factory=list)
    run: RunView
    context_uses: list[dict[str, str]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    model_status: str
    workflow_case: WorkflowCase | None = None
