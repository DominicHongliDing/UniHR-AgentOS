from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class HandoffEndpointKind(StrEnum):
    WORKFLOW_STEP = "workflow_step"
    SKILL = "skill"
    AGENT = "agent"


class TypedHandoff(BaseModel):
    """One versioned exchange contract for every graph seam.

    Large payloads remain outside graph state and are referenced through
    ``output_refs``/``artifact_refs``. ``payload`` is deliberately limited to
    small, JSON-safe control data needed by the next node.
    """

    contract_version: str = Field(default="unihr.handoff/v1", pattern=r"^unihr\.handoff/v\d+$")
    id: str = Field(default="", max_length=160)
    source_kind: HandoffEndpointKind
    source_id: str = Field(min_length=1, max_length=160)
    target_kind: HandoffEndpointKind
    target_id: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=2_000)
    run_refs: list[str] = Field(default_factory=list, max_length=50)
    output_refs: list[str] = Field(default_factory=list, max_length=200)
    artifact_refs: list[str] = Field(default_factory=list, max_length=200)
    provided_capabilities: list[str] = Field(default_factory=list, max_length=50)
    business_refs: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""

    @field_validator(
        "run_refs", "output_refs", "artifact_refs", "provided_capabilities",
        mode="after",
    )
    @classmethod
    def unique_refs(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    def targets(self, kind: HandoffEndpointKind | str, target_id: str) -> bool:
        return self.target_kind == HandoffEndpointKind(kind) and self.target_id == target_id
