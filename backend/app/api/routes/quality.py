from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import require_permission
from app.domains.identity import Principal
from app.domains.runs import RunRepository, SkillNodeOperations
from app.domains.identity.contracts import ANSWER_HISTORY_MANAGE
from app.domains.quality_operations import (
    EvalCase,
    EvalRun,
    EvalSuite,
    EvaluationRequest,
    FeedbackCandidate,
    QualityQueueStatus,
    QualityReport,
    QualityStorageStatus,
    ReleaseDecision,
)
from app.runtime.container import get_quality_operations, get_run_repository


router = APIRouter(prefix="/admin/quality", tags=["quality"])
admin_guard = require_permission(ANSWER_HISTORY_MANAGE)


class QualityOverview(BaseModel):
    suites: list[EvalSuite] = Field(default_factory=list)
    runs: list[EvalRun] = Field(default_factory=list)
    latest_root_failures: dict[str, int] = Field(default_factory=dict)
    feedback_candidates: list[FeedbackCandidate] = Field(default_factory=list)
    storage: QualityStorageStatus
    queue: QualityQueueStatus
    skill_nodes: SkillNodeOperations = Field(default_factory=SkillNodeOperations)


class FeedbackCandidateReview(BaseModel):
    accepted: bool
    title: str = Field(default="用户反馈回归", max_length=200)
    expectation: dict[str, object] = Field(default_factory=dict)


@router.get("", response_model=QualityOverview)
def quality_overview(
    principal: Principal = Depends(admin_guard),
    runs: RunRepository = Depends(get_run_repository),
) -> QualityOverview:
    return QualityOverview.model_validate({
        **get_quality_operations().overview(principal),
        "skill_nodes": runs.skill_node_operations(principal.tenant_id).model_dump(mode="json"),
    })


@router.get("/suites", response_model=list[EvalSuite])
def list_quality_suites(
    principal: Principal = Depends(admin_guard),
) -> list[EvalSuite]:
    module = get_quality_operations()
    module.overview(principal)
    return module.repository.list_suites(principal.tenant_id)


@router.get("/suites/{suite_id}/cases", response_model=list[EvalCase])
def list_quality_cases(
    suite_id: str,
    principal: Principal = Depends(admin_guard),
) -> list[EvalCase]:
    module = get_quality_operations()
    try:
        suite = module.repository.get_suite(suite_id, principal.tenant_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="quality_suite_not_found") from exc
    return module.repository.list_cases(suite.id, principal.tenant_id, suite.version)


@router.post("/runs", response_model=EvalRun, status_code=status.HTTP_202_ACCEPTED)
def start_quality_run(
    request: EvaluationRequest,
    principal: Principal = Depends(admin_guard),
) -> EvalRun:
    try:
        run = get_quality_operations().prepare(request, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="quality_suite_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return run


@router.get("/runs", response_model=list[EvalRun])
def list_quality_runs(
    principal: Principal = Depends(admin_guard),
) -> list[EvalRun]:
    return get_quality_operations().repository.list_runs(principal.tenant_id)


@router.get("/runs/{run_id}", response_model=QualityReport)
def get_quality_run(
    run_id: str,
    principal: Principal = Depends(admin_guard),
) -> QualityReport:
    try:
        return get_quality_operations().report(run_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="quality_run_not_found") from exc


@router.post("/runs/{run_id}/cancel", response_model=EvalRun)
def cancel_quality_run(
    run_id: str,
    principal: Principal = Depends(admin_guard),
) -> EvalRun:
    try:
        return get_quality_operations().repository.cancel_run(run_id, principal.tenant_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="quality_run_not_found") from exc


@router.post("/runs/{run_id}/release-decision", response_model=ReleaseDecision)
def assess_quality_run(
    run_id: str,
    principal: Principal = Depends(admin_guard),
) -> ReleaseDecision:
    try:
        return get_quality_operations().assess(run_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="quality_run_not_found") from exc


@router.post("/feedback-candidates/{candidate_id}/review", response_model=FeedbackCandidate)
def review_feedback_candidate(
    candidate_id: str,
    command: FeedbackCandidateReview,
    principal: Principal = Depends(admin_guard),
) -> FeedbackCandidate:
    try:
        return get_quality_operations().review_feedback(
            candidate_id, accepted=command.accepted, title=command.title,
            expectation=command.expectation, principal=principal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="feedback_candidate_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
