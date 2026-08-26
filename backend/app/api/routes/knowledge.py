from __future__ import annotations

import json
import io
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal, require_permission
from app.domains.identity import KNOWLEDGE_MANAGE, Principal
from app.domains.knowledge import (
    BailianPolicyReranker,
    KnowledgeAccessContext,
    PolicyKnowledgeRetriever,
    PolicyRetrievalCase,
    PolicyRetrievalEvaluator,
    dataset_digest,
    SourceCatalog,
    KnowledgeIngestionJobRepository,
)
from app.models.gateway import ModelGatewayError
from app.domains.vision import VisionEvaluationCase, VisionEvidenceEvaluator
from app.runtime.container import (
    get_knowledge_index,
    get_policy_evaluations,
    get_repository,
    get_settings,
)


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class PublishVersionRequest(BaseModel):
    allowed_roles: list[str] = Field(default_factory=lambda: ["admin", "user"], max_length=4)


class QualityReviewRequest(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=1000)


class PolicyEvaluationCandidateReview(BaseModel):
    approved: bool
    expected_source_ids: list[str] = Field(default_factory=list, max_length=20)
    expected_no_answer: bool = False
    expected_conflict: bool = False
    forbidden_source_ids: list[str] = Field(default_factory=list, max_length=20)
    as_of_date: str | None = Field(default=None, max_length=10)
    notes: str = Field(default="", max_length=1000)


def _access(principal: Principal) -> KnowledgeAccessContext:
    return KnowledgeAccessContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        role=principal.role,
    )


@router.get("/catalog")
def knowledge_catalog(
    _principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    settings = get_settings()
    index_stats = get_knowledge_index().stats()
    if settings.knowledge_source_root is None:
        return {"configured": False, "file_count": 0, "total_bytes": 0, "by_disposition": {}, "by_collection": {}, **index_stats}
    snapshot = SourceCatalog().dry_run(settings.knowledge_source_root)
    return {
        "configured": snapshot.root_exists,
        "file_count": snapshot.file_count,
        "total_bytes": snapshot.total_bytes,
        "by_disposition": snapshot.count_by_disposition(),
        "by_collection": snapshot.count_by_collection(),
        **index_stats,
    }


def _job_repository() -> KnowledgeIngestionJobRepository:
    settings = get_settings()
    assert settings.database_path is not None
    return KnowledgeIngestionJobRepository(settings.database_path)


def _run_reindex_job(
    job_id: str, tenant_id: str, actor_id: str,
    only_relative_paths: tuple[str, ...] = (),
) -> None:
    settings = get_settings()
    repository = _job_repository()
    try:
        def progress(event: dict[str, object]) -> None:
            repository.progress(
                job_id, total=int(event.get("total") or 0),
                processed=int(event.get("processed") or 0),
                current_file=str(event.get("current_file") or ""),
                stage=str(event.get("stage") or "running"),
            )
        result = get_knowledge_index().index(
            settings.knowledge_source_root, tenant_id=tenant_id, actor_id=actor_id,
            auto_publish=False, progress_callback=progress,
            only_relative_paths=only_relative_paths,
        )
        repository.complete(job_id, result)
    except Exception as exc:
        repository.fail(job_id, type(exc).__name__)


@router.post("/upload", status_code=202)
async def upload_knowledge(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    """Store organization-owned policy files and index only the new uploads."""

    if not files:
        raise HTTPException(status_code=400, detail="knowledge_files_required")
    if len(files) > 30:
        raise HTTPException(status_code=400, detail="knowledge_file_limit_exceeded")

    settings = get_settings()
    root = settings.knowledge_source_root
    if root is None:
        raise HTTPException(status_code=400, detail="knowledge_source_not_configured")
    root = root.resolve()
    tenant_dir = (root / principal.tenant_id).resolve()
    if root not in tenant_dir.parents:
        raise HTTPException(status_code=400, detail="invalid_tenant_storage_path")

    allowed_extensions = {".pdf", ".docx", ".md", ".txt"}
    validated: list[tuple[str, bytes]] = []
    for item in files:
        original_name = Path(item.filename or "").name.strip()
        suffix = Path(original_name).suffix.casefold()
        if not original_name or suffix not in allowed_extensions:
            raise HTTPException(status_code=400, detail="unsupported_knowledge_file")
        content = await item.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="knowledge_file_too_large")
        validated.append((original_name, content))

    uploaded: list[str] = []
    tenant_dir.mkdir(parents=True, exist_ok=True)
    for original_name, content in validated:
        target = tenant_dir / original_name
        if target.exists():
            target = tenant_dir / f"{target.stem}-{uuid4().hex[:8]}{target.suffix}"
        target.write_bytes(content)
        uploaded.append(target.relative_to(root).as_posix())

    job = _job_repository().create(
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
    )
    background_tasks.add_task(
        _run_reindex_job,
        job.job_id,
        principal.tenant_id,
        principal.user_id,
        tuple(uploaded),
    )
    return {"uploaded": uploaded, "job": job.as_dict()}


@router.post("/reindex", status_code=202)
def reindex_knowledge(
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    settings = get_settings()
    if settings.knowledge_source_root is None:
        raise HTTPException(status_code=400, detail="knowledge_source_not_configured")
    job = _job_repository().create(tenant_id=principal.tenant_id, actor_id=principal.user_id)
    background_tasks.add_task(_run_reindex_job, job.job_id, principal.tenant_id, principal.user_id)
    return job.as_dict()


@router.get("/ingestion-jobs/latest")
def latest_ingestion_job(
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    job = _job_repository().latest(tenant_id=principal.tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="knowledge_ingestion_job_not_found")
    return job.as_dict()


@router.get("/ingestion-jobs/{job_id}")
def get_ingestion_job(
    job_id: str,
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    try:
        return _job_repository().get(job_id, tenant_id=principal.tenant_id).as_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@router.get("/ingestion-failures")
def list_ingestion_failures(
    _principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    return {"failures": get_knowledge_index().list_failures()}


@router.post("/ingestion-failures/{source_id}/retry", status_code=202)
def retry_ingestion_failure(
    source_id: str,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    try:
        relative_path = get_knowledge_index().prepare_failure_retry(source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    job = _job_repository().create(tenant_id=principal.tenant_id, actor_id=principal.user_id)
    background_tasks.add_task(
        _run_reindex_job, job.job_id, principal.tenant_id, principal.user_id, (relative_path,),
    )
    return {**job.as_dict(), "retry_source_id": source_id, "retry_relative_path": relative_path}


@router.get("/search")
def search_knowledge(
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=6, ge=1, le=12),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    try:
        hits = get_knowledge_index().search(q, limit=limit, access=_access(principal))
    except ModelGatewayError as exc:
        raise HTTPException(status_code=503, detail="embedding_model_unavailable") from exc
    return {
        "hits": [
            {
                "source_id": item.source_id,
                "title": item.title,
                "collection": item.collection,
                "text": item.text,
                "page_number": item.page_number,
                "section": item.section,
                "score": round(item.score, 6),
                "citation": item.citation,
                "retrieval_method": item.retrieval_method,
                "source_version": item.source_version,
                "publication_date": item.publication_date,
                "effective_date": item.effective_date,
                "expiry_date": item.expiry_date,
                "policy_status": item.policy_status,
                "authority_level": item.authority_level,
                "version_id": item.version_id,
                "publication_id": item.publication_id,
                "anchor_kind": item.anchor_kind,
                "anchor_start": item.anchor_start,
                "anchor_end": item.anchor_end,
            }
            for item in hits
        ]
    }


@router.get("/versions")
def list_knowledge_versions(
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    records = get_knowledge_index().list_versions(
        tenant_id=principal.tenant_id,
        include_unpublished=principal.can(KNOWLEDGE_MANAGE),
    )
    return {"versions": [item.model_dump(mode="json") for item in records]}


@router.post("/versions/{version_id}/publish")
def publish_knowledge_version(
    version_id: str,
    payload: PublishVersionRequest,
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    allowed_roles = tuple(role for role in payload.allowed_roles if role in {"admin", "user"})
    if not allowed_roles:
        raise HTTPException(status_code=400, detail="knowledge_acl_role_required")
    try:
        record = get_knowledge_index().publish_version(
            version_id,
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            allowed_roles=allowed_roles,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record.model_dump(mode="json")


@router.post("/versions/{version_id}/quality-review")
def review_knowledge_quality(
    version_id: str,
    payload: QualityReviewRequest,
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    try:
        record = get_knowledge_index().review_quality(
            version_id, actor_id=principal.user_id, approved=payload.approved,
            comment=payload.comment,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record.model_dump(mode="json")


@router.get("/versions/{version_id}/evidence")
def get_knowledge_evidence(
    version_id: str,
    _principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    try:
        return get_knowledge_index().evidence(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@router.get("/versions/{version_id}/pages/{page_number}/preview")
def get_knowledge_page_preview(
    version_id: str,
    page_number: int,
    _principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> Response:
    settings = get_settings()
    if settings.knowledge_source_root is None:
        raise HTTPException(status_code=404, detail="knowledge_source_not_configured")
    try:
        evidence = get_knowledge_index().evidence(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    root = settings.knowledge_source_root.resolve()
    source = (root / str(evidence["relative_path"])).resolve()
    if root not in source.parents or not source.is_file():
        raise HTTPException(status_code=404, detail="knowledge_source_preview_unavailable")
    suffix = source.suffix.casefold()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"} and page_number == 1:
        media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix, "image/png")
        return Response(source.read_bytes(), media_type=media)
    if suffix != ".pdf":
        raise HTTPException(status_code=404, detail="knowledge_source_preview_unavailable")
    try:
        import fitz
        document = fitz.open(source)
        if page_number < 1 or page_number > document.page_count:
            document.close()
            raise HTTPException(status_code=404, detail="knowledge_page_not_found")
        pixmap = document.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        content = pixmap.tobytes("png")
        document.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="knowledge_preview_render_failed") from exc
    return Response(content, media_type="image/png")


@router.post("/publications/{publication_id}/withdraw")
def withdraw_knowledge_publication(
    publication_id: str,
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, str]:
    try:
        get_knowledge_index().withdraw_publication(
            publication_id,
            tenant_id=principal.tenant_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    return {"status": "withdrawn"}


@router.post("/evaluations/run")
def run_knowledge_evaluation(
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    settings = get_settings()
    dataset_path = settings.data_dir / "policy-retrieval-golden.json"
    if not dataset_path.exists():
        raise HTTPException(status_code=404, detail="policy_eval_dataset_not_found")
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    payload.extend(get_policy_evaluations().approved_cases(tenant_id=principal.tenant_id))
    cases = [PolicyRetrievalCase(
        case_id=str(item["case_id"]),
        question=str(item["question"]),
        expected_sources=tuple(str(value) for value in item.get("expected_sources", [])),
        query_variants=tuple(str(value) for value in item.get("query_variants", [])),
        expected_no_answer=bool(item.get("expected_no_answer", False)),
        relevance_by_source={
            str(key): float(value)
            for key, value in dict(item.get("relevance_by_source") or {}).items()
        },
        forbidden_sources=tuple(str(value) for value in item.get("forbidden_sources", [])),
        as_of_date=(str(item["as_of_date"]) if item.get("as_of_date") else None),
        current_only=bool(item.get("current_only", False)),
        expected_conflict=bool(item.get("expected_conflict", False)),
        expected_source_ids=tuple(str(value) for value in item.get("expected_source_ids", [])),
        relevance_by_source_id={
            str(key): float(value)
            for key, value in dict(item.get("relevance_by_source_id") or {}).items()
        },
        forbidden_source_ids=tuple(str(value) for value in item.get("forbidden_source_ids", [])),
    ) for item in payload]
    retriever = PolicyKnowledgeRetriever(
        get_knowledge_index(),
        BailianPolicyReranker(settings),
    )
    started = perf_counter()
    try:
        report = PolicyRetrievalEvaluator(retriever).evaluate(
            cases,
            top_k=10,
            access=_access(principal),
        )
    except ModelGatewayError as exc:
        raise HTTPException(status_code=503, detail="policy_eval_model_unavailable") from exc
    response = {
        "case_count": report.case_count,
        "top_k": report.top_k,
        "hit_rate": round(report.hit_rate, 4),
        "recall_at_k": round(report.recall_at_k, 4),
        "mean_reciprocal_rank": round(report.mean_reciprocal_rank, 4),
        "no_answer_accuracy": round(report.no_answer_accuracy, 4),
        "ndcg_at_k": round(report.ndcg_at_k, 4),
        "citation_coverage": round(report.citation_coverage, 4),
        "effective_date_misuse_rate": round(report.effective_date_misuse_rate, 4),
        "conflict_evidence_recall": round(report.conflict_evidence_recall, 4),
        "temporal_guard_case_count": report.temporal_guard_case_count,
        "conflict_case_count": report.conflict_case_count,
        "cases": [{
            "case_id": item.case_id,
            "question": item.question,
            "hit": item.hit,
            "expected_no_answer": item.expected_no_answer,
            "matched_sources": item.matched_sources,
            "retrieved_sources": item.retrieved_sources,
            "reciprocal_rank": round(item.reciprocal_rank, 4),
            "ndcg_at_k": round(item.ndcg_at_k, 4),
            "citation_coverage": round(item.citation_coverage, 4),
            "retrieved_forbidden_sources": item.retrieved_forbidden_sources,
            "expected_conflict": item.expected_conflict,
            "conflict_detected": item.conflict_detected,
            "expected_source_ids": item.expected_source_ids,
            "retrieved_source_ids": item.retrieved_source_ids,
            "matched_source_ids": item.matched_source_ids,
            "retrieved_forbidden_source_ids": item.retrieved_forbidden_source_ids,
        } for item in report.results],
    }
    stored = get_policy_evaluations().save_evaluation(
        response,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        dataset_version=dataset_digest(payload),
        rerank_model=settings.rerank_model if settings.rerank_enabled else "local-policy-rerank-v1",
        duration_ms=round((perf_counter() - started) * 1000),
    )
    return stored


@router.get("/evaluations/history")
def policy_evaluation_history(
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> list[dict[str, object]]:
    return get_policy_evaluations().list_evaluations(
        tenant_id=principal.tenant_id, limit=limit,
    )


@router.get("/evaluations/candidates")
def policy_evaluation_candidates(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> list[dict[str, object]]:
    try:
        return get_policy_evaluations().list_candidates(
            tenant_id=principal.tenant_id, status=status, limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/evaluations/candidates/{candidate_id}/review")
def review_policy_evaluation_candidate(
    candidate_id: str,
    command: PolicyEvaluationCandidateReview,
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    try:
        candidate = get_policy_evaluations().review_candidate(
            candidate_id,
            tenant_id=principal.tenant_id,
            reviewer_id=principal.user_id,
            approved=command.approved,
            expected_source_ids=command.expected_source_ids,
            expected_no_answer=command.expected_no_answer,
            expected_conflict=command.expected_conflict,
            forbidden_source_ids=command.forbidden_source_ids,
            as_of_date=command.as_of_date,
            notes=command.notes,
        )
        get_repository().admin_update_feedback_status(
            str(candidate["feedback_id"]),
            "added_to_test" if command.approved else "resolved",
            principal,
        )
        return candidate
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="policy_eval_candidate_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/versions/compare")
def compare_knowledge_versions(
    left: str = Query(min_length=5, max_length=120),
    right: str = Query(min_length=5, max_length=120),
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    try:
        return get_knowledge_index().compare_versions(
            left,
            right,
            tenant_id=principal.tenant_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/evaluations/vision/run")
def run_vision_evaluation(
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    dataset_path = get_settings().data_dir / "vision-evidence-golden.json"
    if not dataset_path.exists():
        raise HTTPException(status_code=404, detail="vision_eval_dataset_not_found")
    cases = [VisionEvaluationCase.model_validate(item) for item in json.loads(
        dataset_path.read_text(encoding="utf-8")
    )]
    index = get_knowledge_index()
    evidence = index.visual_evidence_by_title([case.source_title for case in cases])
    report = VisionEvidenceEvaluator().evaluate(cases, evidence)
    payload: dict[str, object] = {
        "case_count": report.case_count,
        "expected_field_count": report.expected_field_count,
        "predicted_field_count": report.predicted_field_count,
        "matched_field_count": report.matched_field_count,
        "exact_match_count": report.exact_match_count,
        "review_expected_count": report.review_expected_count,
        "review_detected_count": report.review_detected_count,
        "false_safe_count": report.false_safe_count,
        "field_precision": round(report.field_precision, 4),
        "field_recall": round(report.field_recall, 4),
        "exact_match_rate": round(report.exact_match_rate, 4),
        "mean_bbox_iou": round(report.mean_bbox_iou, 4),
        "review_recall": round(report.review_recall, 4),
        "false_safe_rate": round(report.false_safe_rate, 4),
        "cases": list(report.cases),
    }
    payload["evaluation_id"] = index.save_visual_evaluation(
        payload, actor_id=principal.user_id, tenant_id=principal.tenant_id,
    )
    return payload


@router.get("/evaluations/vision/latest")
def latest_vision_evaluation(
    principal: Principal = Depends(require_permission(KNOWLEDGE_MANAGE)),
) -> dict[str, object]:
    report = get_knowledge_index().latest_visual_evaluation(tenant_id=principal.tenant_id)
    if report is None:
        raise HTTPException(status_code=404, detail="vision_eval_not_run")
    return report
