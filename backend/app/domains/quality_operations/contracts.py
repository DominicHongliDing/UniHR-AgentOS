from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Criticality = Literal["critical", "standard", "exploratory"]
EvalRunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
ReleaseStatus = Literal["passed", "blocked", "waiver_required"]


class EvalExpectation(BaseModel):
    capability_id: str = ""
    business_intent: str = ""
    turn_action: str = ""
    retrieval_mode: str = ""
    execution_mode: str = ""
    delivery_mode: str = ""
    required_graph_nodes: list[str] = Field(default_factory=list)
    required_trace_events: list[str] = Field(default_factory=list)
    required_artifact_extensions: list[str] = Field(default_factory=list)
    forbidden_artifact_extensions: list[str] = Field(default_factory=list)
    harness_required: bool = False
    answer_required: bool = True
    must_not_use_fallback: bool = False
    required_skill_names: list[str] = Field(default_factory=list)
    forbidden_skill_names: list[str] = Field(default_factory=list)
    skill_semantic_required: bool = False
    skill_rerank_required: bool = False


class EvalCase(BaseModel):
    id: str
    tenant_id: str
    suite_id: str
    suite_version: int = Field(default=1, ge=1)
    version: int = Field(default=1, ge=1)
    title: str
    prompt: str = Field(min_length=1, max_length=20_000)
    task_mode: str = "自动选择"
    active_context: str = ""
    has_active_result: bool = False
    attachment_fixtures: list[str] = Field(default_factory=list)
    expectation: EvalExpectation
    criticality: Criticality = "standard"
    tags: list[str] = Field(default_factory=list)
    source: str = "manual"
    enabled: bool = True
    created_at: str = ""


class EvalSuite(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str = ""
    domain: str = "cross_agent"
    version: int = Field(default=1, ge=1)
    frozen: bool = False
    case_ids: list[str] = Field(default_factory=list)
    created_at: str = ""


class QualityBudgetPolicy(BaseModel):
    """Versioned release budgets. Zero disables a limit instead of guessing one."""

    max_p95_duration_ms: int = Field(default=0, ge=0)
    max_average_cost_cny: float = Field(default=0, ge=0)
    max_total_cost_cny: float = Field(default=0, ge=0)
    min_pricing_coverage: float = Field(default=0, ge=0, le=1)
    enforcement: Literal["warning", "blocking"] = "warning"


class EvaluationSnapshot(BaseModel):
    build_version: str = "working-tree"
    router_policy: str = "task-intent-capability-registry"
    model_chain: list[str] = Field(default_factory=list)
    capability_versions: dict[str, str] = Field(default_factory=dict)
    skill_versions: dict[str, str] = Field(default_factory=dict)
    knowledge_snapshot: str = "current"
    budget_policy: QualityBudgetPolicy = Field(default_factory=QualityBudgetPolicy)


class EvaluationRequest(BaseModel):
    suite_id: str
    candidate_label: str = "working-tree"
    baseline_run_id: str | None = None
    case_ids: list[str] = Field(default_factory=list)


class EvalRun(BaseModel):
    id: str
    tenant_id: str
    suite_id: str
    suite_version: int
    candidate_label: str
    baseline_run_id: str | None = None
    status: EvalRunStatus = "queued"
    snapshot: EvaluationSnapshot = Field(default_factory=EvaluationSnapshot)
    case_count: int = 0
    passed_count: int = 0
    started_at: str = ""
    completed_at: str | None = None
    error: str = ""


class EvaluationObservation(BaseModel):
    capability_id: str = ""
    business_intent: str = ""
    turn_action: str = ""
    retrieval_mode: str = ""
    execution_mode: str = ""
    delivery_mode: str = ""
    routing_fallback_used: bool = False
    matched_layer: str = ""
    graph_path: list[str] = Field(default_factory=list)
    trace_events: list[str] = Field(default_factory=list)
    trace_statuses: dict[str, str] = Field(default_factory=dict)
    artifact_names: list[str] = Field(default_factory=list)
    artifact_harness_passed: bool | None = None
    answer_visible: bool = False
    answer_excerpt: str = ""
    duration_ms: int = Field(default=0, ge=0)
    model_used: str = ""
    estimated_cost_cny: float | None = Field(default=None, ge=0)
    cost_pricing_configured: bool | None = None
    model_call_count: int = Field(default=0, ge=0)
    raw_refs: dict[str, str] = Field(default_factory=dict)
    runtime_error: str = ""
    selected_skill_names: list[str] = Field(default_factory=list)
    skill_candidate_strategy: str = ""
    skill_semantic_used: bool = False
    skill_rerank_used: bool = False
    skill_selection_fallback_used: bool = False


class AssertionResult(BaseModel):
    dimension: str
    code: str
    passed: bool
    expected: Any = None
    actual: Any = None
    critical: bool = False


class FailureAttribution(BaseModel):
    layer: str
    code: str
    summary: str
    assertion_index: int = 0


class EvalCaseResult(BaseModel):
    id: str
    run_id: str
    case_id: str
    tenant_id: str
    passed: bool
    criticality: Criticality
    assertions: list[AssertionResult] = Field(default_factory=list)
    failures: list[FailureAttribution] = Field(default_factory=list)
    root_failure: FailureAttribution | None = None
    observation: EvaluationObservation
    created_at: str = ""


class QualityMetric(BaseModel):
    key: str
    label: str
    numerator: int
    denominator: int
    value: float | None = None
    baseline_value: float | None = None
    delta: float | None = None


class QualityReport(BaseModel):
    run: EvalRun
    results: list[EvalCaseResult] = Field(default_factory=list)
    metrics: list[QualityMetric] = Field(default_factory=list)
    root_failure_counts: dict[str, int] = Field(default_factory=dict)
    regressions: list[str] = Field(default_factory=list)
    p50_duration_ms: int = 0
    p95_duration_ms: int = 0
    total_estimated_cost_cny: float = 0
    average_estimated_cost_cny: float = 0
    priced_case_count: int = 0
    cost_observed_case_count: int = 0
    pricing_coverage: float = 1


class ReleaseBudgetCheck(BaseModel):
    code: str
    label: str
    actual: float
    limit: float
    unit: str
    passed: bool
    blocking: bool = False


class ReleaseDecision(BaseModel):
    run_id: str
    status: ReleaseStatus
    blocking_failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    passed_critical: int = 0
    total_critical: int = 0
    waiver_reason: str = ""
    budget_checks: list[ReleaseBudgetCheck] = Field(default_factory=list)
    decided_at: str = ""


class QualityStorageStatus(BaseModel):
    backend: Literal["postgresql", "sqlite"]
    production_ready: bool
    fallback_reason: str = ""


class QualityQueueStatus(BaseModel):
    queued: int = 0
    running: int = 0
    retryable: int = 0
    failed: int = 0


class QualityQueueJob(BaseModel):
    id: str
    run_id: str
    tenant_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    attempts: int = 0
    max_attempts: int = 3
    available_at: str
    leased_until: str = ""
    worker_id: str = ""
    principal: dict[str, Any] = Field(default_factory=dict)
    last_error: str = ""
    created_at: str
    updated_at: str


class FeedbackCandidate(BaseModel):
    id: str
    tenant_id: str
    feedback_id: str
    source_message_id: str
    scrubbed_prompt: str
    expected_correction: str = ""
    status: Literal["pending", "accepted", "rejected"] = "pending"
    created_at: str = ""
