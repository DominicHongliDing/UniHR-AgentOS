from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ImageEvidenceBlock(BaseModel):
    text: str = ""
    kind: Literal["text", "field", "table", "handwriting", "stamp", "other"] = "text"
    bbox: list[int] = Field(default_factory=lambda: [0, 0, 999, 999])
    model_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class ImageEvidencePage(BaseModel):
    source_name: str
    page: int = Field(ge=1)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    page_text: str = ""
    blocks: list[ImageEvidenceBlock] = Field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class ImageEvidenceResult(BaseModel):
    schema_version: str = "image_evidence_v1"
    pages: list[ImageEvidencePage] = Field(default_factory=list)
    extracted_text: str = ""
    source_count: int = 0
    page_count: int = 0
    block_count: int = 0
    review_block_count: int = 0
    truncated: bool = False
    payload_ref: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.extracted_text.strip())
