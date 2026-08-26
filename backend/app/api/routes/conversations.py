from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.domains.conversations.contracts import (
    ArtifactRecord,
    AttachmentRecord,
    Conversation,
    MessageRecord,
    AnswerFeedback,
)
from app.domains.identity import Principal
from app.domains.orchestration import WorkflowCase
from app.domains.runs import RunRecord, RunRepository
from app.domains.conversations.repository import ConversationRepository
from app.runtime.container import get_conversation_lifecycle, get_repository, get_run_repository, get_workbench


router = APIRouter(prefix="/conversations", tags=["conversations"])


class CreateConversation(BaseModel):
    title: str = Field(default="新对话", max_length=80)


class UpdateConversation(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    pinned: bool | None = None


class ConversationDetail(BaseModel):
    conversation: Conversation
    messages: list[MessageRecord]
    attachments: list[AttachmentRecord]
    artifacts: list[ArtifactRecord]
    feedback: list[AnswerFeedback]
    runs: list[RunRecord] = Field(default_factory=list)
    workflow_cases: list[WorkflowCase] = Field(default_factory=list)


@router.get("", response_model=list[Conversation])
def list_conversations(
    repository: ConversationRepository = Depends(get_repository),
    principal: Principal = Depends(get_current_principal),
) -> list[Conversation]:
    return repository.list_conversations(principal)


@router.post("", response_model=Conversation, status_code=status.HTTP_201_CREATED)
def create_conversation(
    command: CreateConversation,
    repository: ConversationRepository = Depends(get_repository),
    principal: Principal = Depends(get_current_principal),
) -> Conversation:
    return repository.create_conversation(command.title, principal)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    repository: ConversationRepository = Depends(get_repository),
    runs: RunRepository = Depends(get_run_repository),
    principal: Principal = Depends(get_current_principal),
) -> ConversationDetail:
    try:
        return ConversationDetail(
            conversation=repository.get_conversation(conversation_id, principal),
            messages=repository.list_messages(conversation_id, principal=principal),
            attachments=repository.list_attachments(conversation_id, principal),
            artifacts=repository.list_artifacts(conversation_id, principal),
            feedback=repository.list_feedback(conversation_id, principal),
            runs=runs.list_for_conversation(
                conversation_id,
                tenant_id=principal.tenant_id,
                owner_user_id=principal.user_id,
            ),
            workflow_cases=get_workbench().workflow_cases.list_for_conversation(
                conversation_id, principal,
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation_not_found") from exc


@router.patch("/{conversation_id}", response_model=Conversation)
def update_conversation(
    conversation_id: str,
    command: UpdateConversation,
    repository: ConversationRepository = Depends(get_repository),
    principal: Principal = Depends(get_current_principal),
) -> Conversation:
    try:
        return repository.update_conversation(
            conversation_id, title=command.title, pinned=command.pinned, principal=principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation_not_found") from exc


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    principal: Principal = Depends(get_current_principal),
) -> Response:
    try:
        get_conversation_lifecycle().delete(conversation_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation_not_found") from exc
    except RuntimeError as exc:
        if str(exc) == "conversation_has_active_turn":
            raise HTTPException(status_code=409, detail="conversation_has_active_turn") from exc
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)
