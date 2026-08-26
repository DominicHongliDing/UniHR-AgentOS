from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.domains.conversations.contracts import AttachmentRecord
from app.domains.files.legacy_office import LegacyOfficeConversionError, LegacyOfficeConverter
from app.domains.vision import VisionEvidenceExtractor


@dataclass(frozen=True)
class IngestedChunk:
    text: str
    page_number: int | None = None
    section: str = ""
    anchor_kind: str = "section"
    anchor_start: int | None = None
    anchor_end: int | None = None
    bbox: tuple[int, int, int, int] | None = None
    model_confidence: float | None = None
    needs_review: bool = False
    evidence_kind: str = "text"


@dataclass(frozen=True)
class IngestionQualityReport:
    status: str
    score: int
    extractor: str
    page_count: int = 0
    evidence_block_count: int = 0
    review_block_count: int = 0
    review_page_count: int = 0
    issues: tuple[str, ...] = ()
    checks: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status, "score": self.score, "extractor": self.extractor,
            "page_count": self.page_count, "evidence_block_count": self.evidence_block_count,
            "review_block_count": self.review_block_count,
            "review_page_count": self.review_page_count, "issues": list(self.issues),
            "checks": self.checks,
        }


@dataclass(frozen=True)
class IngestionResult:
    chunks: tuple[IngestedChunk, ...]
    quality: IngestionQualityReport


class KnowledgeDocumentExtractor:
    """Extract knowledge text and retain pixel-level provenance for scanned sources."""

    def __init__(
        self,
        *,
        vision: VisionEvidenceExtractor | None = None,
        legacy_converter: LegacyOfficeConverter | None = None,
    ) -> None:
        self.vision = vision
        self.legacy_converter = legacy_converter

    def extract(self, path: Path) -> IngestionResult:
        suffix = path.suffix.casefold()
        converted: Path | None = None
        if suffix == ".doc":
            if self.legacy_converter is None:
                return self._failed("legacy_converter_unavailable")
            try:
                converted = self.legacy_converter.convert(path).path
            except LegacyOfficeConversionError:
                return self._failed("legacy_office_conversion_failed")
            path, suffix = converted, ".docx"
        try:
            if suffix == ".pdf":
                return self._pdf(path)
            if suffix == ".docx":
                return self._docx(path)
            if suffix in {".md", ".txt"}:
                return self._plain(path)
            if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
                return self._vision(path, low_text_pages=None)
            return self._failed("unsupported_format")
        finally:
            if converted is not None:
                converted.unlink(missing_ok=True)

    def _pdf(self, path: Path) -> IngestionResult:
        reader = PdfReader(str(path))
        chunks: list[IngestedChunk] = []
        low_text_pages: list[int] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if len(text) < 30:
                low_text_pages.append(page_number)
            else:
                chunks.extend(self._split(text, page_number=page_number))
        if low_text_pages:
            visual = self._vision(path, low_text_pages=low_text_pages)
            chunks.extend(visual.chunks)
            if visual.quality.status == "failed" and not chunks:
                return visual
            issues = list(visual.quality.issues)
            if len(low_text_pages) < len(reader.pages):
                issues.append("mixed_text_and_scanned_pages")
            status = "review_required" if visual.quality.status != "ready" else "ready"
            score = min(100, visual.quality.score if not chunks else max(visual.quality.score, 70))
            return IngestionResult(tuple(chunks), IngestionQualityReport(
                status=status, score=score, extractor="pdf_text+vision",
                page_count=len(reader.pages),
                evidence_block_count=visual.quality.evidence_block_count,
                review_block_count=visual.quality.review_block_count,
                review_page_count=visual.quality.review_page_count,
                issues=tuple(dict.fromkeys(issues)), checks=visual.quality.checks,
            ))
        return self._ready(chunks, "pdf_text", page_count=len(reader.pages))

    def _vision(self, path: Path, low_text_pages: list[int] | None) -> IngestionResult:
        if self.vision is None:
            return self._failed("vision_extractor_unavailable")
        attachment = AttachmentRecord(
            id="knowledge-ingestion", conversation_id="knowledge", message_id="knowledge",
            display_name=path.name, mime_type=self._mime(path), extension=path.suffix.casefold(),
            byte_size=path.stat().st_size, saved_path=str(path), extracted_text="",
            summary={"requires_vision": True, "low_text_pages": low_text_pages or []},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        result = self.vision.extract([attachment])
        chunks = tuple(
            IngestedChunk(
                text=block.text, page_number=page.page, section="OCR 证据",
                anchor_kind="bbox", bbox=tuple(block.bbox),
                model_confidence=block.model_confidence, needs_review=block.needs_review,
                evidence_kind=block.kind,
            )
            for page in result.pages for block in page.blocks if block.text.strip()
        )
        review_pages = sum(1 for page in result.pages if page.needs_review)
        issues: list[str] = []
        if not chunks:
            issues.append("no_localized_evidence")
        if result.review_block_count:
            issues.append("evidence_requires_human_review")
        if result.truncated:
            issues.append("page_limit_truncated")
        checks = {
            "text_extracted": bool(result.extracted_text.strip()),
            "evidence_localized": bool(chunks),
            "all_pages_processed": not result.truncated,
            "no_review_flags": result.review_block_count == 0 and review_pages == 0,
        }
        if not chunks:
            status, score = "failed", 0
        elif all(checks.values()):
            status, score = "ready", 100
        else:
            status = "review_required"
            score = round(100 * sum(checks.values()) / len(checks))
        return IngestionResult(chunks, IngestionQualityReport(
            status=status, score=score, extractor="vision_ocr",
            page_count=result.page_count, evidence_block_count=result.block_count,
            review_block_count=result.review_block_count, review_page_count=review_pages,
            issues=tuple(issues), checks=checks,
        ))

    def _docx(self, path: Path) -> IngestionResult:
        text = "\n".join(p.text for p in Document(path).paragraphs if p.text.strip())
        return self._ready(self._split(text), "docx") if text else self._failed("empty_document")

    def _plain(self, path: Path) -> IngestionResult:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        return self._ready(self._split(text), "plain_text") if text else self._failed("empty_document")

    @staticmethod
    def _split(text: str, *, page_number: int | None = None) -> list[IngestedChunk]:
        parts = [part.strip() for part in text.split("\n\n") if part.strip()]
        return [IngestedChunk(part, page_number=page_number) for part in parts]

    @staticmethod
    def _ready(chunks: list[IngestedChunk], extractor: str, page_count: int = 0) -> IngestionResult:
        return IngestionResult(tuple(chunks), IngestionQualityReport(
            status="ready", score=100, extractor=extractor, page_count=page_count,
            checks={"text_extracted": bool(chunks), "evidence_localized": True},
        ))

    @staticmethod
    def _failed(issue: str) -> IngestionResult:
        return IngestionResult((), IngestionQualityReport(
            status="failed", score=0, extractor="none", issues=(issue,),
            checks={"text_extracted": False},
        ))

    @staticmethod
    def _mime(path: Path) -> str:
        return {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}.get(path.suffix.casefold(), "image/png")
