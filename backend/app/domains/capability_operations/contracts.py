from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CapabilityOperationsSummary(BaseModel):
    registered_capabilities: int = 0
    active_capabilities: int = 0
    observed_capabilities: int = 0
    total_runs: int = 0
    completed_runs: int = 0
    completion_rate: float | None = None
    fallback_runs: int = 0
    fallback_rate: float | None = None
    feedback_count: int = 0
    helpful_rate: float | None = None
    capabilities_needing_attention: int = 0


class CapabilityOperationsCard(BaseModel):
    capability_id: str
    label: str
    intent: str
    route: str
    description: str
    version: str
    lifecycle: str
    risk_level: str
    owner: str
    input_contract: str
    output_contract: str
    human_review_policy: str
    tools: list[str] = Field(default_factory=list)
    retrieval_mode: str
    requires_freshness: bool = False
    run_count: int = 0
    completed_runs: int = 0
    completion_rate: float | None = None
    fallback_runs: int = 0
    feedback_count: int = 0
    helpful_rate: float | None = None
    quality_issue_count: int = 0
    health: Literal["healthy", "watch", "no_data"] = "no_data"


class CapabilityOperationsSnapshot(BaseModel):
    generated_at: str
    window_days: int
    summary: CapabilityOperationsSummary
    capabilities: list[CapabilityOperationsCard] = Field(default_factory=list)


class RoutingGoldenCaseResult(BaseModel):
    case_id: str
    title: str
    prompt: str
    expected_capability_id: str
    actual_capability_id: str
    expected_action: str | None = None
    actual_action: str
    expected_retrieval_mode: str | None = None
    actual_retrieval_mode: str
    expected_execution_mode: str | None = None
    execution_mode: str
    expected_delivery_mode: str | None = None
    actual_delivery_mode: str
    matched_layer: str
    fallback_used: bool = False
    passed: bool
    issues: list[str] = Field(default_factory=list)


class RoutingGoldenReport(BaseModel):
    dataset_version: str
    status: Literal["passed", "needs_attention"]
    total: int
    passed: int
    accuracy: float
    model_routed_cases: int
    fallback_cases: int
    results: list[RoutingGoldenCaseResult] = Field(default_factory=list)
