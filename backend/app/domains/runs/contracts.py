from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.domains.assistant.contracts import BusinessIntent, ExecutionMode, TurnAction


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    WAITING_INPUT = "waiting_input"
    WAITING_REVIEW = "waiting_review"
    EXHAUSTED = "exhausted"
    FAILED = "failed"
    REJECTED = "rejected"


class RunPointer(BaseModel):
    run_id: str = Field(min_length=1, max_length=120)
    tenant_id: str = Field(min_length=1, max_length=120)
    owner_user_id: str = Field(min_length=1, max_length=120)


class WorkflowRunRequest(RunPointer):
    conversation_id: str = Field(min_length=1, max_length=120)
    business_intent: BusinessIntent
    turn_action: TurnAction
    execution_mode: ExecutionMode
    max_iterations: int = Field(default=2, ge=1, le=5)
    payload: dict[str, Any] = Field(default_factory=dict)


class OperationResult(BaseModel):
    answer: str = ""
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None


class OperationPort(Protocol):
    def invoke(self, request: WorkflowRunRequest) -> OperationResult: ...


class WorkflowRunResult(BaseModel):
    run_id: str
    conversation_id: str
    status: RunStatus
    business_intent: BusinessIntent
    turn_action: TurnAction
    execution_mode: ExecutionMode
    iteration: int
    max_iterations: int
    answer: str = ""
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    graph_path: list[str] = Field(default_factory=list)
    quality_issues: list[str] = Field(default_factory=list)
    requires_human_review: bool = False
    error_code: str | None = None
    approval_decision: bool | None = None
    approval_comment: str = ""
    checkpoint_thread_id: str


class RunRecord(BaseModel):
    id: str
    tenant_id: str
    owner_user_id: str
    conversation_id: str
    message_id: str | None = None
    status: RunStatus
    business_intent: BusinessIntent
    turn_action: TurnAction
    execution_mode: ExecutionMode
    iteration: int
    max_iterations: int
    graph_path: list[str] = Field(default_factory=list)
    quality_issues: list[str] = Field(default_factory=list)
    requires_human_review: bool = False
    checkpoint_thread_id: str = ""
    error_code: str | None = None
    approval_decision: bool | None = None
    approval_comment: str = ""
    created_at: str
    updated_at: str


class ApprovalQueueItem(RunRecord):
    conversation_title: str
    owner_username: str
    owner_display_name: str


class RunEventRecord(BaseModel):
    id: str
    run_id: str
    sequence: int
    event: str
    status: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class SkillNodeOperations(BaseModel):
    run_count: int = 0
    node_started: int = 0
    node_completed: int = 0
    node_failed: int = 0
    node_waiting_input: int = 0
    node_reused: int = 0
    recovery_started: int = 0
    recovery_completed: int = 0
    retry_success_rate: float | None = None
    reuse_rate: float | None = None
    average_duration_ms: float | None = None
    total_estimated_cost_cny: float = 0
