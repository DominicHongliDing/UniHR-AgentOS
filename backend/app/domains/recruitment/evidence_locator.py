from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

from app.domains.conversations.contracts import AttachmentRecord

from .contracts import EvidenceLocator, RecruitmentReport


_MISSING_EVIDENCE = re.compile(r"(?:未找到|证据不足|待补充|模型未返回)")
_MARKER = re.compile(
    r"\[(?:第\s*(?P<page>\d+)\s*页|段落\s*(?P<paragraph>\d+)|"
    r"表格\s*(?P<table>\d+)|Sheet:\s*(?P<sheet>[^\]]+))\]"
)
_UNIT = re.compile(r"[^\n。！？；;]{4,}[。！？；;]?|[^\n]{4,}")


@dataclass(frozen=True)
class EvidenceLocalizationResult:
    report: RecruitmentReport
    located: int
    review_required: int
    unlocated: int


@dataclass(frozen=True)
class _EvidenceSegment:
    source_id: str
    source_name: str
    text: str
    anchor_kind: str
    page_number: int | None = None
    paragraph_number: int | None = None
    table_number: int | None = None
    sheet_name: str = ""
    bbox: tuple[int, ...] = ()
    start_char: int = 0
    model_confidence: float | None = None
    needs_review: bool = False
    review_reasons: tuple[str, ...] = ()


class RecruitmentEvidenceLocator:
    """Localize model evidence without exposing parser-specific complexity to callers."""

    def localize(
        self,
        report: RecruitmentReport,
        sources: Iterable[AttachmentRecord],
        visual_evidence: Iterable[dict[str, Any]] = (),
    ) -> EvidenceLocalizationResult:
        source_list = list(sources)
        source_ids = {item.display_name: item.id for item in source_list}
        segments: dict[str, list[_EvidenceSegment]] = {
            item.display_name: self._native_segments(item) for item in source_list
        }
        for raw in visual_evidence:
            segment = self._visual_segment(raw, source_ids)
            if segment is not None:
                segments.setdefault(segment.source_name, []).insert(0, segment)

        located = 0
        review_required = 0
        unlocated = 0
        candidates = []
        for candidate in report.candidates:
            rows = []
            for row in candidate.criterion_scores:
                locator = self._locate_one(
                    row.evidence,
                    candidate.source_name,
                    segments.get(candidate.source_name, []),
                    source_ids.get(candidate.source_name, ""),
                )
                if locator.status == "located":
                    located += 1
                elif locator.status == "review_required":
                    review_required += 1
                else:
                    unlocated += 1
                rows.append(row.model_copy(update={"locator": locator}))
            candidates.append(candidate.model_copy(update={"criterion_scores": rows}))
        return EvidenceLocalizationResult(
            report=report.model_copy(update={"candidates": candidates}),
            located=located,
            review_required=review_required,
            unlocated=unlocated,
        )

    def _locate_one(
        self,
        evidence: str,
        source_name: str,
        segments: list[_EvidenceSegment],
        source_id: str,
    ) -> EvidenceLocator:
        if not evidence.strip() or _MISSING_EVIDENCE.search(evidence):
            return EvidenceLocator(
                source_id=source_id,
                source_name=source_name,
                review_reasons=["evidence_missing"],
            )
        best: _EvidenceSegment | None = None
        best_score = 0.0
        for segment in segments:
            score = _text_match_score(evidence, segment.text)
            if score > best_score:
                best = segment
                best_score = score
        if best is None or best_score < 0.46:
            return EvidenceLocator(
                source_id=source_id,
                source_name=source_name,
                match_score=round(best_score, 4),
                review_reasons=["source_text_not_matched"],
            )

        reasons = list(best.review_reasons)
        if best_score < 0.62:
            reasons.append("weak_text_match")
        if best.needs_review:
            reasons.append("source_block_requires_review")
        status = "review_required" if reasons else "located"
        return EvidenceLocator(
            status=status,
            anchor_kind=best.anchor_kind,  # type: ignore[arg-type]
            source_id=best.source_id,
            source_name=best.source_name,
            page_number=best.page_number,
            paragraph_number=best.paragraph_number,
            table_number=best.table_number,
            sheet_name=best.sheet_name,
            bbox=list(best.bbox),
            quote=best.text.strip()[:300],
            match_score=round(best_score, 4),
            model_confidence=best.model_confidence,
            review_reasons=list(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _native_segments(source: AttachmentRecord) -> list[_EvidenceSegment]:
        text = source.extracted_text
        matches = list(_MARKER.finditer(text))
        segments: list[_EvidenceSegment] = []
        if not matches:
            return _split_units(
                source.id, source.display_name, text, anchor_kind="text", start_offset=0,
            )
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            anchor_kind = (
                "page" if match.group("page") else
                "paragraph" if match.group("paragraph") else
                "table" if match.group("table") else "sheet"
            )
            segments.extend(_split_units(
                source.id,
                source.display_name,
                text[start:end],
                anchor_kind=anchor_kind,
                start_offset=start,
                page_number=int(match.group("page")) if match.group("page") else None,
                paragraph_number=int(match.group("paragraph")) if match.group("paragraph") else None,
                table_number=int(match.group("table")) if match.group("table") else None,
                sheet_name=(match.group("sheet") or "").strip(),
            ))
        return segments

    @staticmethod
    def _visual_segment(
        raw: dict[str, Any], source_ids: dict[str, str],
    ) -> _EvidenceSegment | None:
        source_name = str(raw.get("source_name") or "").strip()
        text = str(raw.get("text") or "").strip()
        if not source_name or not text:
            return None
        bbox_raw = raw.get("bbox") if isinstance(raw.get("bbox"), list) else []
        bbox = tuple(int(value) for value in bbox_raw[:4]) if len(bbox_raw) >= 4 else ()
        confidence = raw.get("model_confidence")
        try:
            model_confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            model_confidence = None
        return _EvidenceSegment(
            source_id=source_ids.get(source_name, ""),
            source_name=source_name,
            text=text,
            anchor_kind="bbox" if len(bbox) == 4 else "page",
            page_number=max(1, int(raw.get("page") or 1)),
            bbox=bbox,
            model_confidence=model_confidence,
            needs_review=bool(raw.get("needs_review")),
            review_reasons=tuple(str(item) for item in raw.get("review_reasons") or []),
        )


def _split_units(
    source_id: str,
    source_name: str,
    text: str,
    *,
    anchor_kind: str,
    start_offset: int,
    page_number: int | None = None,
    paragraph_number: int | None = None,
    table_number: int | None = None,
    sheet_name: str = "",
) -> list[_EvidenceSegment]:
    units = list(_UNIT.finditer(text))
    if not units and text.strip():
        units = [re.match(r"[\s\S]+", text)]  # type: ignore[list-item]
    return [
        _EvidenceSegment(
            source_id=source_id,
            source_name=source_name,
            text=match.group(0).strip(),
            anchor_kind=anchor_kind,
            page_number=page_number,
            paragraph_number=paragraph_number,
            table_number=table_number,
            sheet_name=sheet_name,
            start_char=start_offset + match.start(),
        )
        for match in units
        if match is not None and match.group(0).strip()
    ]


def _text_match_score(left: str, right: str) -> float:
    left_norm = _normalize(left)
    right_norm = _normalize(right)
    if not left_norm or not right_norm:
        return 0.0
    shorter = min(len(left_norm), len(right_norm))
    if shorter >= 6 and (left_norm in right_norm or right_norm in left_norm):
        return min(1.0, 0.88 + shorter / max(len(left_norm), len(right_norm)) * 0.12)
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_pairs = _ngrams(left_norm, 2)
    right_pairs = _ngrams(right_norm, 2)
    dice = 2 * len(left_pairs & right_pairs) / max(1, len(left_pairs) + len(right_pairs))
    return max(sequence, dice)


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value).casefold()


def _ngrams(value: str, size: int) -> set[str]:
    if len(value) <= size:
        return {value}
    return {value[index:index + size] for index in range(len(value) - size + 1)}
