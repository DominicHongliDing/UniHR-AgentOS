from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class TaskKind(StrEnum):
    GENERAL = "general"
    RECRUITMENT = "recruitment"
    POLICY = "policy"
    SPREADSHEET = "spreadsheet"
    DOCUMENT = "document"
    IMAGE = "image"

    @classmethod
    def _missing_(cls, value: object):
        if value == "annual_adjustment":
            return cls.SPREADSHEET
        return None


class DocumentStyleSpec(BaseModel):
    preset: str = Field(default="official", max_length=40)
    body_font: str = Field(default="仿宋_GB2312", max_length=40)
    body_size_pt: float = Field(default=16, ge=10.5, le=22)
    title_font: str = Field(default="方正小标宋简体", max_length=40)
    title_size_pt: float = Field(default=22, ge=14, le=30)
    body_alignment: str = Field(default="justify", pattern="^(left|center|right|justify)$")
    title_alignment: str = Field(default="center", pattern="^(left|center|right)$")
    line_spacing: float = Field(default=1.5, ge=1, le=3)
    first_line_indent_cm: float = Field(default=0.74, ge=0, le=2)
    top_margin_cm: float = Field(default=3.0, ge=1, le=5)
    bottom_margin_cm: float = Field(default=2.8, ge=1, le=5)
    left_margin_cm: float = Field(default=2.8, ge=1, le=5)
    right_margin_cm: float = Field(default=2.6, ge=1, le=5)
    footer_text: str = Field(default="UniHR AgentOS · AI 草稿，请人工复核", max_length=120)


class SpreadsheetStyleSpec(BaseModel):
    preset: str = Field(default="professional_blue", max_length=40)
    target_sheet: str = Field(default="", max_length=80)
    header_row: int = Field(default=1, ge=1, le=200)
    header_font: str = Field(default="微软雅黑", max_length=40)
    body_font: str = Field(default="微软雅黑", max_length=40)
    header_fill: str = Field(default="2563EB", pattern="^[0-9A-Fa-f]{6}$")
    header_text_color: str = Field(default="FFFFFF", pattern="^[0-9A-Fa-f]{6}$")
    horizontal_alignment: str = Field(default="left", pattern="^(left|center|right|general)$")
    vertical_alignment: str = Field(default="center", pattern="^(top|center|bottom)$")
    freeze_header: bool = True
    enable_filter: bool = True
    preserve_existing_style: bool = True
    auto_width: bool = True


class ArtifactStyleSpec(BaseModel):
    document: DocumentStyleSpec | None = None
    spreadsheet: SpreadsheetStyleSpec | None = None


class TaskBrief(BaseModel):
    version: str = Field(default="1.0", pattern="^1\\.[0-9]+$")
    kind: TaskKind = TaskKind.GENERAL
    objective: str = Field(default="", max_length=4_000)
    constraints: list[str] = Field(default_factory=list, max_length=40)
    deliverables: list[str] = Field(default_factory=list, max_length=20)
    selected_skill_ids: list[str] = Field(default_factory=list, max_length=5)
    approval_required: bool = False
    mode_spec: dict[str, Any] = Field(default_factory=dict)
    artifact_style: ArtifactStyleSpec = Field(default_factory=ArtifactStyleSpec)

    @field_validator("objective")
    @classmethod
    def clean_objective(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("constraints", "deliverables")
    @classmethod
    def clean_string_list(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = " ".join(str(value).split())[:500]
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned

    @field_validator("selected_skill_ids")
    @classmethod
    def clean_skill_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip()[:100] for item in values if item.strip()))[:5]
