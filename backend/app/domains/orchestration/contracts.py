from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator

from app.domains.handoffs import HandoffEndpointKind, TypedHandoff


class WorkflowAgent(StrEnum):
    PLANNER = "planner"
    RECRUITMENT = "recruitment"
    HUMAN_REVIEW = "human_review"
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    PUBLIC_INFO = "public_info"
    POLICY = "policy"


class WorkflowStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


class WorkflowCaseStatus(StrEnum):
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStep(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    sequence: int = Field(ge=1, le=30)
    title: str = Field(min_length=1, max_length=160)
    agent: WorkflowAgent
    instruction: str = Field(default="", max_length=4_000)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    requires_human_review: bool = False
    optional: bool = False
    required_artifact_kinds: list[str] = Field(default_factory=list, max_length=8)
    status: WorkflowStepStatus = WorkflowStepStatus.PENDING
    run_ids: list[str] = Field(default_factory=list, max_length=30)
    artifact_ids: list[str] = Field(default_factory=list, max_length=100)
    summary: str = Field(default="", max_length=4_000)
    started_at: str = ""
    completed_at: str = ""
    error_code: str = ""


class WorkflowPlan(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=4_000)
    rationale: str = Field(default="", max_length=2_000)
    source: str = Field(default="governed_fallback", max_length=80)
    steps: list[WorkflowStep] = Field(min_length=3, max_length=30)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowPlan":
        ids = [item.id for item in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow_step_ids_must_be_unique")
        known: set[str] = set()
        for expected_sequence, step in enumerate(self.steps, start=1):
            if step.sequence != expected_sequence:
                raise ValueError("workflow_steps_must_be_sequential")
            if any(parent not in known for parent in step.depends_on):
                raise ValueError("workflow_dependency_must_precede_step")
            known.add(step.id)
        if not any(item.agent == WorkflowAgent.HUMAN_REVIEW for item in self.steps):
            raise ValueError("workflow_human_gate_required")
        execution_agents = {
            item.agent for item in self.steps
            if item.agent not in {WorkflowAgent.PLANNER, WorkflowAgent.HUMAN_REVIEW}
        }
        if len(execution_agents) < 2:
            raise ValueError("workflow_requires_multiple_agents")
        return self


class WorkflowHandoff(TypedHandoff):
    """Workflow compatibility view over the shared typed handoff contract."""

    source_kind: HandoffEndpointKind = HandoffEndpointKind.WORKFLOW_STEP
    target_kind: HandoffEndpointKind = HandoffEndpointKind.WORKFLOW_STEP

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        normalized.setdefault("source_id", normalized.get("from_step_id"))
        normalized.setdefault("target_id", normalized.get("to_step_id"))
        normalized.setdefault("run_refs", normalized.get("run_ids") or [])
        normalized.setdefault("artifact_refs", normalized.get("artifact_ids") or [])
        recruitment_case_id = str(normalized.get("recruitment_case_id") or "")
        business_refs = dict(normalized.get("business_refs") or {})
        if recruitment_case_id:
            business_refs.setdefault("recruitment_case_id", recruitment_case_id)
        normalized["business_refs"] = business_refs
        return normalized

    @computed_field
    @property
    def from_step_id(self) -> str:
        return self.source_id

    @computed_field
    @property
    def to_step_id(self) -> str:
        return self.target_id

    @computed_field
    @property
    def run_ids(self) -> list[str]:
        return self.run_refs

    @computed_field
    @property
    def artifact_ids(self) -> list[str]:
        return self.artifact_refs

    @computed_field
    @property
    def recruitment_case_id(self) -> str:
        return self.business_refs.get("recruitment_case_id", "")


class WorkflowCase(BaseModel):
    id: str
    tenant_id: str
    owner_user_id: str
    conversation_id: str
    title: str
    objective: str
    rationale: str = ""
    planner_source: str = ""
    status: WorkflowCaseStatus
    current_step_id: str = ""
    recruitment_case_id: str = ""
    steps: list[WorkflowStep] = Field(default_factory=list)
    handoffs: list[WorkflowHandoff] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    decision_log: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


class WorkflowCaseAction(BaseModel):
    action: str = Field(pattern="^(approve|revise|retry|skip|cancel)$")
    comment: str = Field(default="", max_length=2_000)
    selected_candidate_ids: list[str] = Field(default_factory=list, max_length=30)
