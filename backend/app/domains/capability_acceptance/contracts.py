from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


CapabilityName = Literal[
    "policy", "document", "spreadsheet", "recruitment", "public_info", "skill",
]


class CapabilitySmokeCase(BaseModel):
    id: str
    capability: CapabilityName
    priority: Literal["P0", "P1", "P2"] = "P0"
    prompt: str = Field(min_length=1, max_length=200)
    task_mode: str
    expected_route: str
    required_graph_nodes: list[str] = Field(default_factory=list)
    required_trace_events: list[str] = Field(default_factory=list)
    required_artifact_extensions: list[str] = Field(default_factory=list)
    harness_required: bool = False


class CapabilityObservation(BaseModel):
    case_id: str
    observed_route: str
    graph_path: list[str] = Field(default_factory=list)
    trace_events: list[str] = Field(default_factory=list)
    trace_statuses: dict[str, str] = Field(default_factory=dict)
    artifact_names: list[str] = Field(default_factory=list)
    harness_passed: bool | None = None
    answer_visible: bool = False
    duration_ms: int = Field(default=0, ge=0)


class CapabilityCaseResult(BaseModel):
    case_id: str
    capability: CapabilityName
    passed: bool
    issues: list[str] = Field(default_factory=list)
    observed_route: str = ""
    duration_ms: int = 0


class CapabilityAcceptanceReport(BaseModel):
    dataset_version: str
    status: Literal["passed", "failed"]
    case_count: int
    passed_count: int
    pass_rate: float = Field(ge=0, le=1)
    results: list[CapabilityCaseResult] = Field(default_factory=list)
