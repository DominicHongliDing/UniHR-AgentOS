from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SkillSource = Literal["system", "prompt", "conversation", "upload"]
SkillStatus = Literal["draft", "active", "disabled", "pending_approval"]
SkillVisibility = Literal["personal", "organization", "system"]
SkillApprovalStatus = Literal["not_required", "pending", "approved", "rejected"]
SkillResourceKind = Literal["scripts", "references", "assets"]
SkillResourceStatus = Literal["ready", "quarantined"]


class SkillDraft(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=600)
    instructions: str = Field(min_length=1, max_length=12_000)
    trigger_phrases: list[str] = Field(default_factory=list, max_length=16)
    capabilities: list[str] = Field(default_factory=list, max_length=16)
    source_type: SkillSource = "prompt"
    source_conversation_id: str | None = None
    enabled: bool = False
    status: SkillStatus = "draft"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillRecord(SkillDraft):
    id: str
    tenant_id: str = "tenant_unihr_demo"
    owner_user_id: str = "usr_unihr_admin"
    version: int = Field(ge=1)
    visibility: SkillVisibility = "personal"
    approval_status: SkillApprovalStatus = "not_required"
    approval_requested_by: str = ""
    approved_by: str = ""
    approved_at: str = ""
    approval_comment: str = ""
    created_at: str
    updated_at: str


class SkillVersionRecord(BaseModel):
    skill_id: str
    version: int = Field(ge=1)
    snapshot: SkillDraft
    change_type: str
    content_sha256: str
    created_by: str
    created_at: str


class SkillResourceRecord(BaseModel):
    id: str
    skill_id: str
    tenant_id: str
    owner_user_id: str
    kind: SkillResourceKind
    name: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    status: SkillResourceStatus
    scripts_executed: bool = False
    created_at: str


class SkillMatch(BaseModel):
    skill: SkillRecord
    score: float
    matched_trigger: str
