from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.api.deps import require_permission
from app.domains.conversations.contracts import (
    AdminConversationSummary,
    AdminFeedbackRecord,
    AnswerFeedback,
    ArtifactRecord,
    AttachmentRecord,
    Conversation,
    FeedbackStats,
    MessageRecord,
)
from app.domains.identity import Principal, UserAccount
from app.domains.identity.contracts import ANSWER_HISTORY_MANAGE
from app.domains.assistant.product_capabilities import PRODUCT_CAPABILITY_FACTS
from app.domains.assistant.capability_registry import default_task_capability_registry
from app.domains.capability_operations import (
    CapabilityOperationsCenter,
    CapabilityOperationsSnapshot,
    RoutingGoldenReport,
)
from app.domains.assistant.product_help_evaluation import (
    ProductHelpRouteEvalReport,
    run_product_help_route_evaluation,
)
from app.domains.assistant.rewrite_evaluation import (
    RewriteEvaluationReport,
    evaluate_query_rewrite,
    load_rewrite_evaluation_cases,
)
from app.runtime.container import (
    get_conversation_lifecycle,
    get_identity,
    get_repository,
    get_run_repository,
    get_settings,
    get_workbench,
)


router = APIRouter(prefix="/admin", tags=["admin"])
admin_guard = require_permission(ANSWER_HISTORY_MANAGE)


class AdminConversationDetail(BaseModel):
    conversation: Conversation
    messages: list[MessageRecord]
    attachments: list[AttachmentRecord]
    artifacts: list[ArtifactRecord]
    feedback: list[AnswerFeedback]


class FeedbackStatusCommand(BaseModel):
    test_status: str = Field(pattern="^(unreviewed|added_to_test|resolved)$")


class ProductCapabilityView(BaseModel):
    capability_id: str
    status: str
    user_scope: str
    fact: str


@router.get("/product-capabilities", response_model=list[ProductCapabilityView])
def list_product_capabilities(
    principal: Principal = Depends(admin_guard),
) -> list[ProductCapabilityView]:
    return [ProductCapabilityView(**item.__dict__) for item in PRODUCT_CAPABILITY_FACTS]


@router.post("/evaluations/product-help-routing", response_model=ProductHelpRouteEvalReport)
def evaluate_product_help_routing(
    principal: Principal = Depends(admin_guard),
) -> ProductHelpRouteEvalReport:
    return run_product_help_route_evaluation()


@router.post("/evaluations/query-rewrite", response_model=RewriteEvaluationReport)
def evaluate_query_rewrite_generalization(
    principal: Principal = Depends(admin_guard),
) -> RewriteEvaluationReport:
    cases = load_rewrite_evaluation_cases(get_settings().data_dir / "query-rewrite-generalization.json")
    return evaluate_query_rewrite(get_workbench().router, cases)


@router.get("/capability-operations", response_model=CapabilityOperationsSnapshot)
def get_capability_operations(
    days: int = Query(default=30, ge=1, le=180),
    principal: Principal = Depends(admin_guard),
) -> CapabilityOperationsSnapshot:
    """Return one governed view of capability contracts and real run evidence."""

    runs_repository = get_run_repository()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    runs = runs_repository.list_recent_for_tenant(
        principal.tenant_id,
        since=since,
    )
    return CapabilityOperationsCenter(default_task_capability_registry()).snapshot(
        runs=runs,
        events_by_run=runs_repository.list_events_for_runs([item.id for item in runs]),
        feedback=get_repository().admin_list_feedback(principal, limit=500),
        window_days=days,
    )


@router.post(
    "/evaluations/capability-routing",
    response_model=RoutingGoldenReport,
)
def evaluate_capability_routing(
    principal: Principal = Depends(admin_guard),
) -> RoutingGoldenReport:
    """Run the model-first router against ordinary, adversarial product prompts."""

    return CapabilityOperationsCenter(
        default_task_capability_registry()
    ).evaluate_routing(get_workbench().router)


@router.get("/users", response_model=list[UserAccount])
def list_users(principal: Principal = Depends(admin_guard)) -> list[UserAccount]:
    return get_identity().list_users(principal.tenant_id)


@router.get("/history", response_model=list[AdminConversationSummary])
def list_answer_history(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(admin_guard),
) -> list[AdminConversationSummary]:
    return get_repository().admin_list_history(principal, q, limit)


@router.get("/history/{conversation_id}", response_model=AdminConversationDetail)
def get_answer_history(
    conversation_id: str,
    principal: Principal = Depends(admin_guard),
) -> AdminConversationDetail:
    repository = get_repository()
    try:
        conversation = repository.admin_get_conversation(conversation_id, principal)
        messages, attachments, artifacts, feedback = repository.admin_conversation_content(
            conversation_id, principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation_not_found") from exc
    return AdminConversationDetail(
        conversation=conversation, messages=messages, attachments=attachments,
        artifacts=artifacts, feedback=feedback,
    )


@router.delete("/history/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_answer_history(
    conversation_id: str,
    principal: Principal = Depends(admin_guard),
) -> Response:
    try:
        get_conversation_lifecycle().delete(conversation_id, principal, admin=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation_not_found") from exc
    except RuntimeError as exc:
        if str(exc) == "conversation_has_active_turn":
            raise HTTPException(status_code=409, detail="conversation_has_active_turn") from exc
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/feedback/stats", response_model=FeedbackStats)
def feedback_stats(principal: Principal = Depends(admin_guard)) -> FeedbackStats:
    return get_repository().admin_feedback_stats(principal)


@router.get("/feedback", response_model=list[AdminFeedbackRecord])
def list_feedback(
    helpful: bool | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    principal: Principal = Depends(admin_guard),
) -> list[AdminFeedbackRecord]:
    return get_repository().admin_list_feedback(principal, helpful, limit)


@router.patch("/feedback/{feedback_id}", response_model=AdminFeedbackRecord)
def update_feedback_status(
    feedback_id: str,
    command: FeedbackStatusCommand,
    principal: Principal = Depends(admin_guard),
) -> AdminFeedbackRecord:
    try:
        return get_repository().admin_update_feedback_status(
            feedback_id, command.test_status, principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="feedback_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/feedback-export.csv")
def export_feedback_for_testing(principal: Principal = Depends(admin_guard)) -> Response:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow([
        "feedback_id", "user", "conversation", "question", "answer", "helpful",
        "comment", "test_status", "updated_at",
    ])
    for item in get_repository().admin_list_feedback(principal, limit=500):
        writer.writerow([
            item.id, item.owner_username, item.conversation_title, item.question, item.answer,
            "helpful" if item.helpful else "not_helpful", item.comment, item.test_status, item.updated_at,
        ])
    content = "\ufeff" + stream.getvalue()
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=unihr-feedback-test-dataset.csv"},
    )
