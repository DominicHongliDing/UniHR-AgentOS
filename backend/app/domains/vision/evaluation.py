from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from pydantic import BaseModel, Field


class VisionGoldenField(BaseModel):
    field_id: str
    expected_text: str
    page: int = Field(ge=1)
    bbox: list[int] = Field(min_length=4, max_length=4)
    should_review: bool = False


class VisionEvaluationCase(BaseModel):
    case_id: str
    source_title: str
    fields: list[VisionGoldenField]


@dataclass(frozen=True)
class VisionEvaluationReport:
    case_count: int
    expected_field_count: int
    predicted_field_count: int
    matched_field_count: int
    exact_match_count: int
    review_expected_count: int
    review_detected_count: int
    false_safe_count: int
    field_precision: float
    field_recall: float
    exact_match_rate: float
    mean_bbox_iou: float
    review_recall: float
    false_safe_rate: float
    cases: tuple[dict[str, object], ...]


class VisionEvidenceEvaluator:
    """Deterministic evaluator for persisted OCR evidence, not model self-grading."""

    def evaluate(
        self,
        cases: Iterable[VisionEvaluationCase],
        evidence_by_title: dict[str, list[dict[str, object]]],
    ) -> VisionEvaluationReport:
        case_results: list[dict[str, object]] = []
        expected_total = predicted_total = matched_total = exact_total = 0
        review_expected = review_detected = false_safe = 0
        ious: list[float] = []
        case_list = list(cases)
        for case in case_list:
            predictions = evidence_by_title.get(case.source_title, [])
            predicted_total += len(predictions)
            expected_total += len(case.fields)
            field_results: list[dict[str, object]] = []
            used: set[int] = set()
            for field in case.fields:
                if field.should_review:
                    review_expected += 1
                best_index, best_score = self._best_prediction(field, predictions, used)
                prediction = predictions[best_index] if best_index is not None else None
                matched = prediction is not None and best_score > 0
                if matched and best_index is not None:
                    used.add(best_index)
                    matched_total += 1
                predicted_text = str(prediction.get("text") or "") if prediction else ""
                exact = matched and _normalize(field.expected_text) == _normalize(predicted_text)
                if exact:
                    exact_total += 1
                iou = _bbox_iou(field.bbox, prediction.get("bbox") if prediction else None)
                if matched:
                    ious.append(iou)
                predicted_review = bool(prediction and prediction.get("needs_review"))
                if field.should_review and predicted_review:
                    review_detected += 1
                if field.should_review and not predicted_review:
                    false_safe += 1
                field_results.append({
                    "field_id": field.field_id, "expected_text": field.expected_text,
                    "predicted_text": predicted_text, "matched": matched, "exact": exact,
                    "bbox_iou": round(iou, 4), "should_review": field.should_review,
                    "predicted_review": predicted_review,
                })
            case_results.append({
                "case_id": case.case_id, "source_title": case.source_title,
                "passed": all(item["matched"] for item in field_results)
                          and not any(item["should_review"] and not item["predicted_review"] for item in field_results),
                "fields": field_results,
            })
        return VisionEvaluationReport(
            case_count=len(case_list), expected_field_count=expected_total,
            predicted_field_count=predicted_total, matched_field_count=matched_total,
            exact_match_count=exact_total, review_expected_count=review_expected,
            review_detected_count=review_detected, false_safe_count=false_safe,
            field_precision=_ratio(matched_total, predicted_total),
            field_recall=_ratio(matched_total, expected_total),
            exact_match_rate=_ratio(exact_total, expected_total),
            mean_bbox_iou=sum(ious) / len(ious) if ious else 0.0,
            review_recall=_ratio(review_detected, review_expected),
            false_safe_rate=_ratio(false_safe, review_expected),
            cases=tuple(case_results),
        )

    @staticmethod
    def _best_prediction(
        field: VisionGoldenField,
        predictions: list[dict[str, object]],
        used: set[int],
    ) -> tuple[int | None, float]:
        expected = _normalize(field.expected_text)
        best: tuple[int | None, float] = (None, 0.0)
        for index, prediction in enumerate(predictions):
            if index in used or int(prediction.get("page") or 0) != field.page:
                continue
            actual = _normalize(str(prediction.get("text") or ""))
            if not actual:
                continue
            if expected == actual:
                score = 1.0
            elif expected in actual or actual in expected:
                score = min(len(expected), len(actual)) / max(len(expected), len(actual))
            else:
                expected_tokens = set(re.findall(r"[\w?]+", expected))
                actual_tokens = set(re.findall(r"[\w?]+", actual))
                score = len(expected_tokens & actual_tokens) / max(1, len(expected_tokens | actual_tokens))
            if score > best[1]:
                best = (index, score)
        return best


def _normalize(value: str) -> str:
    return re.sub(r"[\s：:，,。.]", "", value.casefold())


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def _bbox_iou(expected: list[int], actual: object) -> float:
    if not isinstance(actual, list) or len(actual) != 4:
        return 0.0
    try:
        ax0, ay0, ax1, ay1 = [float(value) for value in expected]
        bx0, by0, bx1, by1 = [float(value) for value in actual]
    except (TypeError, ValueError):
        return 0.0
    intersection = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0
