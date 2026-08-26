from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.domains.conversations.contracts import AnswerFeedback
from app.domains.identity import Principal
from app.runtime.container import get_policy_evaluations, get_quality_operations, get_repository


router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackCommand(BaseModel):
    helpful: bool
    comment: str = Field(default="", max_length=1000)


@router.put("/messages/{message_id}", response_model=AnswerFeedback)
def rate_answer(
    message_id: str,
    command: FeedbackCommand,
    principal: Principal = Depends(get_current_principal),
) -> AnswerFeedback:
    try:
        repository = get_repository()
        feedback = repository.upsert_feedback(
            message_id, command.helpful, command.comment, principal,
        )
        if not command.helpful:
            messages = repository.list_messages(feedback.conversation_id, 200, principal)
            answer_index = next(
                (index for index, item in enumerate(messages) if item.id == message_id), None,
            )
            if answer_index is not None:
                answer = messages[answer_index]
                question = next(
                    (
                        item.content for item in reversed(messages[:answer_index])
                        if item.role == "user"
                    ),
                    "",
                )
                get_policy_evaluations().create_feedback_candidate(
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    feedback_id=feedback.id,
                    conversation_id=feedback.conversation_id,
                    message_id=message_id,
                    question=question,
                    answer=answer.content,
                    comment=command.comment,
                    trace=answer.trace,
                    redaction_terms=(principal.username, principal.display_name),
                )
                get_quality_operations().capture_feedback(
                    feedback_id=feedback.id,
                    source_message_id=message_id,
                    prompt=question,
                    correction=command.comment,
                    principal=principal,
                )
        return feedback
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="assistant_message_not_found") from exc
