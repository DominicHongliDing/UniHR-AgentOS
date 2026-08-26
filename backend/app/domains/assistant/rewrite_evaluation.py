from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .contracts import TurnInterpretation


class RewriteRouter(Protocol):
    def interpret(self, message: str, **kwargs: Any) -> TurnInterpretation: ...


class RewriteEvaluationCase(BaseModel):
    case_id: str
    title: str
    message: str
    task_mode: str = "自动选择"
    active_context: str = ""
    has_active_result: bool = False
    attachment_summaries: list[dict[str, Any]] = Field(default_factory=list)
    expected_intent: str
    expected_action: str
    expected_mode: str
    expected_terms: list[str] = Field(default_factory=list)


class RewriteEvaluationCaseResult(BaseModel):
    case_id: str
    title: str
    original_message: str
    resolved_request: str
    matched_layer: str
    expected_intent: str
    actual_intent: str
    expected_action: str
    actual_action: str
    expected_mode: str
    actual_mode: str
    expected_terms: list[str]
    missing_expected_terms: list[str]
    unexpected_hard_facts: list[str]
    route_passed: bool
    action_passed: bool
    mode_passed: bool
    context_passed: bool
    fidelity_passed: bool
    passed: bool


class RewriteEvaluationReport(BaseModel):
    status: str
    total: int
    passed: int
    route_accuracy: float
    action_accuracy: float
    mode_accuracy: float
    context_recall: float
    fidelity_rate: float
    model_routed_cases: int
    fallback_cases: int
    cases: list[RewriteEvaluationCaseResult]


def load_rewrite_evaluation_cases(path: Path) -> list[RewriteEvaluationCase]:
    return [RewriteEvaluationCase.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def evaluate_query_rewrite(router: RewriteRouter, cases: list[RewriteEvaluationCase]) -> RewriteEvaluationReport:
    def evaluate_case(case: RewriteEvaluationCase) -> RewriteEvaluationCaseResult:
        decision = router.interpret(
            case.message,
            task_mode=case.task_mode,
            active_context=case.active_context,
            has_active_result=case.has_active_result,
            attachment_summaries=case.attachment_summaries,
        )
        resolved = decision.resolved_request.strip() or case.message.strip()
        missing_terms = [
            term for term in case.expected_terms
            if not any(_contains_expected_term(resolved, option) for option in term.split("/"))
        ]
        source_facts = _hard_facts(f"{case.message}\n{case.active_context}")
        unexpected_facts = sorted(_hard_facts(resolved) - source_facts)
        route_passed = decision.business_intent.value == case.expected_intent
        action_passed = decision.turn_action.value in case.expected_action.split("/")
        mode_passed = decision.execution_mode.value == case.expected_mode
        context_passed = not missing_terms
        fidelity_passed = not unexpected_facts
        return RewriteEvaluationCaseResult(
            case_id=case.case_id,
            title=case.title,
            original_message=case.message,
            resolved_request=resolved,
            matched_layer=decision.matched_layer,
            expected_intent=case.expected_intent,
            actual_intent=decision.business_intent.value,
            expected_action=case.expected_action,
            actual_action=decision.turn_action.value,
            expected_mode=case.expected_mode,
            actual_mode=decision.execution_mode.value,
            expected_terms=case.expected_terms,
            missing_expected_terms=missing_terms,
            unexpected_hard_facts=unexpected_facts,
            route_passed=route_passed,
            action_passed=action_passed,
            mode_passed=mode_passed,
            context_passed=context_passed,
            fidelity_passed=fidelity_passed,
            passed=route_passed and action_passed and mode_passed and context_passed and fidelity_passed,
        )

    # The cases are independent. Running a small bounded batch keeps the admin
    # evaluation interactive without changing any routing or grading result.
    with ThreadPoolExecutor(max_workers=min(2, max(1, len(cases)))) as executor:
        results = list(executor.map(evaluate_case, cases))
    total = len(results)
    count = lambda field: sum(bool(getattr(item, field)) for item in results)
    passed = count("passed")
    fallback_cases = sum("fallback" in item.matched_layer for item in results)
    return RewriteEvaluationReport(
        status="passed" if passed == total else "needs_attention",
        total=total,
        passed=passed,
        route_accuracy=count("route_passed") / total if total else 0,
        action_accuracy=count("action_passed") / total if total else 0,
        mode_accuracy=count("mode_passed") / total if total else 0,
        context_recall=count("context_passed") / total if total else 0,
        fidelity_rate=count("fidelity_passed") / total if total else 0,
        model_routed_cases=total - fallback_cases,
        fallback_cases=fallback_cases,
        cases=results,
    )


_CHINESE_COUNTS = {"一": "1", "二": "2", "两": "2", "三": "3", "仨": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def _hard_facts(text: str) -> set[str]:
    facts = set(re.findall(r"20\d{2}年|\d+(?:\.\d+)?%|\d{1,2}月\d{1,2}日|\d{1,2}点(?:\d{1,2}分)?", text))
    for value, unit in re.findall(r"(\d+|[一二两三仨四五六七八九十])\s*(名|位|人|个)", text):
        # 名/位/人/个 are surface forms of the same people-count fact.
        # Canonicalizing the unit prevents "三个" -> "3人" from being
        # reported as an invented value.
        facts.add(f"count:{_CHINESE_COUNTS.get(value, value)}")
    selection_patterns = (
        r"(?:选出|筛选出|选择|保留|最终|先要|要|定).{0,16}?(\d+|[一二两三仨四五六七八九十])(?:\s*(?:名|位|人|份|个))?",
        r"(\d+|[一二两三仨四五六七八九十])\s*份简历",
    )
    for pattern in selection_patterns:
        for value in re.findall(pattern, text):
            facts.add(f"selection:{_CHINESE_COUNTS.get(value, value)}")
    return facts


def _contains_expected_term(text: str, expected: str) -> bool:
    return _normalize_count_expressions(expected).casefold() in _normalize_count_expressions(text).casefold()


def _normalize_count_expressions(text: str) -> str:
    def replace_count(match: re.Match[str]) -> str:
        value = _CHINESE_COUNTS.get(match.group(1), match.group(1))
        return f"<count:{value}>"

    return re.sub(r"(\d+|[一二两三仨四五六七八九十])\s*(?:名|位|人|个)", replace_count, text)
