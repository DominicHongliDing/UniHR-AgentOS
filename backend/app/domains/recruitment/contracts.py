from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domains.conversations.contracts import AttachmentRecord


class ScreeningCriterion(BaseModel):
    id: str
    label: str
    weight: float = Field(ge=0, le=100)
    requirement: str = ""


class RecruitmentRequest(BaseModel):
    job_requirements: str = Field(min_length=1, max_length=20_000)
    attachments: list[AttachmentRecord] = Field(default_factory=list, max_length=30)
    job_title: str = Field(default="", max_length=120)
    must_haves: str = Field(default="", max_length=8_000)
    nice_to_haves: str = Field(default="", max_length=8_000)
    selection_count: int = Field(default=3, ge=1, le=30)
    skill_instructions: str = Field(default="", max_length=8_000)
    map_batch_size: int = Field(default=8, ge=1, le=12)


class ResumeSourceProfile(BaseModel):
    source_id: str
    source_name: str
    content_fingerprint: str
    extracted_chars: int = Field(ge=0)
    parse_status: str = Field(default="parsed", pattern="^(parsed|limited|empty)$")
    duplicate_of: str = ""
    version_group_id: str = ""
    version_status: Literal[
        "standalone", "representative", "superseded", "review_required"
    ] = "standalone"
    superseded_by: str = ""
    semantic_similarity: float = Field(default=0, ge=0, le=1)
    identity_basis: str = ""


class DuplicateSourceGroup(BaseModel):
    representative_source: str
    duplicate_sources: list[str] = Field(default_factory=list)


class ResumeVersionGroup(BaseModel):
    group_id: str
    representative_source: str = ""
    version_sources: list[str] = Field(default_factory=list)
    status: Literal["merged", "review_required"]
    identity_basis: str
    similarities: dict[str, float] = Field(default_factory=dict)
    selection_reason: str


class RubricSnapshot(BaseModel):
    version: str = "1.0"
    rubric_hash: str
    criteria: list[ScreeningCriterion]
    locked: bool = True


EvidenceLocationStatus = Literal["located", "review_required", "unlocated"]
EvidenceAnchorKind = Literal["bbox", "page", "paragraph", "table", "sheet", "text", "unlocated"]


class EvidenceLocator(BaseModel):
    """A reviewable pointer back to the candidate's original source material."""

    status: EvidenceLocationStatus = "unlocated"
    anchor_kind: EvidenceAnchorKind = "unlocated"
    source_id: str = ""
    source_name: str = ""
    page_number: int | None = Field(default=None, ge=1)
    paragraph_number: int | None = Field(default=None, ge=1)
    table_number: int | None = Field(default=None, ge=1)
    sheet_name: str = ""
    bbox: list[int] = Field(default_factory=list, min_length=0, max_length=4)
    quote: str = ""
    match_score: float = Field(default=0, ge=0, le=1)
    model_confidence: float | None = Field(default=None, ge=0, le=1)
    review_reasons: list[str] = Field(default_factory=list)


class CriterionEvidence(BaseModel):
    criterion_id: str
    criterion_label: str
    score: float = Field(ge=0, le=100)
    weight: float = Field(ge=0, le=100)
    evidence: str
    confidence: str = "medium"
    locator: EvidenceLocator | None = None


class CandidateEvaluation(BaseModel):
    candidate_name: str
    source_name: str
    total_score: float = Field(ge=0, le=100)
    criterion_scores: list[CriterionEvidence] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    verification_questions: list[str] = Field(default_factory=list)
    recommendation: str
    evidence_coverage: float = Field(default=0, ge=0, le=1)


class RecruitmentHarnessReport(BaseModel):
    status: str = Field(pattern="^(passed|warning|failed)$")
    score: float = Field(ge=0, le=100)
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class RecruitmentCapacityReport(BaseModel):
    """Repeatable capacity/stability evidence for the real recruitment Graph."""

    dataset_version: str
    execution_mode: Literal["deterministic_graph_capacity"]
    status: Literal["passed", "warning", "failed"]
    source_count: int = Field(ge=0)
    unique_source_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    run_count: int = Field(ge=1)
    batch_count: int = Field(ge=0)
    model_call_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    source_coverage: float = Field(ge=0, le=1)
    evidence_coverage: float = Field(ge=0, le=1)
    ranking_consistent: bool
    payload_offload_verified: bool
    artifact_verified: bool
    formula_error_count: int = Field(ge=0)
    graph_path: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    generated_at: str


class RecruitmentCaseSnapshot(BaseModel):
    case_id: str
    rubric_hash: str
    source_count: int = Field(ge=0)
    unique_source_count: int = Field(ge=0)
    duplicate_source_count: int = Field(ge=0)
    selection_count: int = Field(ge=1)
    graph_path: list[str] = Field(default_factory=list)


class RecruitmentReport(BaseModel):
    job_title: str
    job_requirements: str
    criteria: list[ScreeningCriterion]
    candidates: list[CandidateEvaluation]
    fairness_note: str
    generated_at: str
    rubric: RubricSnapshot | None = None
    source_profiles: list[ResumeSourceProfile] = Field(default_factory=list)
    duplicate_groups: list[DuplicateSourceGroup] = Field(default_factory=list)
    version_groups: list[ResumeVersionGroup] = Field(default_factory=list)
    harness: RecruitmentHarnessReport | None = None
    case: RecruitmentCaseSnapshot | None = None


CandidateReviewStatus = Literal["pending", "shortlisted", "hold", "not_advanced"]
InterviewOutcome = Literal["not_scheduled", "scheduled", "passed", "hold", "not_advanced"]
InterviewMode = Literal["onsite", "online", "phone"]
InterviewSessionStatus = Literal["scheduled", "completed", "cancelled"]
InterviewRecommendation = Literal["strong_yes", "yes", "hold", "no", "strong_no"]
RecruitmentCaseStatus = Literal["reviewing", "interviewing", "finalized"]
RecruitmentCaseActionType = Literal[
    "review_candidate", "record_interview", "finalize", "reopen",
    "rename_case", "move_case", "archive_case", "restore_case",
    "schedule_interview", "cancel_interview", "submit_interview_feedback",
]
RecruitmentPackageType = Literal["interview_evaluation", "final_decision"]
RecruitmentPackageFormat = Literal["docx", "xlsx"]


class InterviewNote(BaseModel):
    id: str
    note: str = Field(min_length=1, max_length=4_000)
    outcome: InterviewOutcome
    recorded_by: str
    recorded_at: str


class InterviewPanelMember(BaseModel):
    user_id: str
    display_name: str


class InterviewFeedback(BaseModel):
    id: str
    session_id: str
    candidate_id: str
    interviewer_user_id: str
    interviewer_name: str
    recommendation: InterviewRecommendation
    dimension_scores: dict[str, int] = Field(default_factory=dict)
    evidence_note: str = Field(min_length=1, max_length=4_000)
    risk_note: str = Field(default="", max_length=4_000)
    submitted_at: str
    updated_at: str


class InterviewSession(BaseModel):
    id: str
    candidate_id: str
    title: str = Field(min_length=1, max_length=160)
    start_at: str
    end_at: str
    timezone: str = Field(default="Asia/Shanghai", max_length=80)
    mode: InterviewMode = "online"
    location: str = Field(default="", max_length=500)
    meeting_url: str = Field(default="", max_length=1_000)
    interviewers: list[InterviewPanelMember] = Field(default_factory=list, min_length=1, max_length=12)
    focus_dimensions: list[str] = Field(default_factory=list, min_length=1, max_length=12)
    status: InterviewSessionStatus = "scheduled"
    feedback: list[InterviewFeedback] = Field(default_factory=list)
    created_by: str
    created_at: str
    updated_at: str
    cancelled_by: str = ""
    cancelled_at: str = ""


class CandidateReviewRecord(BaseModel):
    candidate_id: str
    candidate_name: str
    source_name: str
    rank: int = Field(ge=1)
    ai_score: float = Field(ge=0, le=100)
    ai_recommendation: str
    evidence_coverage: float = Field(ge=0, le=1)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    verification_questions: list[str] = Field(default_factory=list)
    evidence_locations: list[dict[str, Any]] = Field(default_factory=list)
    review_status: CandidateReviewStatus = "pending"
    reviewer_note: str = Field(default="", max_length=4_000)
    reviewed_by: str = ""
    reviewed_at: str = ""
    interview_outcome: InterviewOutcome = "not_scheduled"
    interview_notes: list[InterviewNote] = Field(default_factory=list)


class RecruitmentCaseRecord(BaseModel):
    id: str
    tenant_id: str
    owner_user_id: str
    conversation_id: str
    run_id: str
    artifact_id: str
    job_title: str
    case_name: str
    folder_id: str = ""
    archived_at: str = ""
    rubric_hash: str
    selection_count: int = Field(ge=1)
    status: RecruitmentCaseStatus = "reviewing"
    candidates: list[CandidateReviewRecord] = Field(default_factory=list)
    collaborator_user_ids: list[str] = Field(default_factory=list)
    interview_sessions: list[InterviewSession] = Field(default_factory=list)
    final_candidate_ids: list[str] = Field(default_factory=list)
    final_rationale: str = Field(default="", max_length=8_000)
    finalized_by: str = ""
    finalized_at: str = ""
    created_at: str
    updated_at: str


class RecruitmentPackageCreate(BaseModel):
    package_type: RecruitmentPackageType


class RecruitmentPackagePreview(BaseModel):
    case_id: str
    package_type: RecruitmentPackageType
    title: str
    can_generate: bool
    blockers: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    candidate_count: int = Field(ge=0)
    interview_session_count: int = Field(ge=0)
    feedback_count: int = Field(ge=0)
    final_candidate_count: int = Field(ge=0)


class RecruitmentPackageFile(BaseModel):
    format: RecruitmentPackageFormat
    display_name: str
    byte_size: int = Field(ge=0)
    artifact_id: str = ""


class RecruitmentPackageRecord(BaseModel):
    id: str
    case_id: str
    tenant_id: str
    owner_user_id: str
    package_type: RecruitmentPackageType
    title: str
    files: list[RecruitmentPackageFile] = Field(default_factory=list)
    harness: dict[str, Any] = Field(default_factory=dict)
    created_by: str
    created_at: str


class RecruitmentCaseCreate(BaseModel):
    case_id: str
    tenant_id: str
    owner_user_id: str
    conversation_id: str
    run_id: str
    artifact_id: str
    job_title: str
    rubric_hash: str
    selection_count: int = Field(ge=1)
    candidates: list[CandidateReviewRecord]


class RecruitmentCaseAction(BaseModel):
    action: RecruitmentCaseActionType
    candidate_id: str = ""
    review_status: CandidateReviewStatus | None = None
    reviewer_note: str = Field(default="", max_length=4_000)
    interview_outcome: InterviewOutcome | None = None
    interview_note: str = Field(default="", max_length=4_000)
    final_candidate_ids: list[str] = Field(default_factory=list, max_length=30)
    final_rationale: str = Field(default="", max_length=8_000)
    case_name: str = Field(default="", max_length=160)
    folder_id: str = Field(default="", max_length=120)
    interview_session_id: str = Field(default="", max_length=120)
    interview_title: str = Field(default="", max_length=160)
    interview_start_at: str = Field(default="", max_length=80)
    interview_end_at: str = Field(default="", max_length=80)
    interview_timezone: str = Field(default="Asia/Shanghai", max_length=80)
    interview_mode: InterviewMode | None = None
    interview_location: str = Field(default="", max_length=500)
    interview_meeting_url: str = Field(default="", max_length=1_000)
    interviewer_ids: list[str] = Field(default_factory=list, max_length=12)
    focus_dimensions: list[str] = Field(default_factory=list, max_length=12)
    recommendation: InterviewRecommendation | None = None
    dimension_scores: dict[str, int] = Field(default_factory=dict)
    evidence_note: str = Field(default="", max_length=4_000)
    risk_note: str = Field(default="", max_length=4_000)


class RecruitmentInterviewerRecord(BaseModel):
    user_id: str
    display_name: str
    role: str


class RecruitmentFolderRecord(BaseModel):
    id: str
    tenant_id: str
    owner_user_id: str
    name: str
    created_at: str
    updated_at: str


class RecruitmentFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class RecruitmentFolderUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
