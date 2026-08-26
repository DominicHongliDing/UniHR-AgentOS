from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .contracts import (
    AssertionResult,
    EvalCase,
    EvalCaseResult,
    EvaluationObservation,
    FailureAttribution,
)


_LAYER_ORDER = {
    "context": 0,
    "rewrite": 1,
    "intent": 2,
    "capability": 3,
    "skill_selection": 4,
    "input_gate": 5,
    "retrieval_tool": 6,
    "domain_harness": 7,
    "artifact_harness": 8,
    "human_gate": 9,
    "delivery": 10,
    "runtime": 11,
}


_CODE_LAYER = {
    "runtime_execution_failed": "runtime",
    "intent_mismatch": "intent",
    "turn_action_mismatch": "intent",
    "capability_mismatch": "capability",
    "retrieval_mode_mismatch": "retrieval_tool",
    "execution_mode_mismatch": "capability",
    "delivery_mode_mismatch": "delivery",
    "fallback_contract_broken": "capability",
    "required_skill_missing": "skill_selection",
    "forbidden_skill_selected": "skill_selection",
    "skill_semantic_missing": "skill_selection",
    "skill_rerank_missing": "skill_selection",
    "missing_graph_node": "runtime",
    "missing_trace_event": "retrieval_tool",
    "trace_event_failed": "retrieval_tool",
    "artifact_missing": "artifact_harness",
    "forbidden_artifact_created": "artifact_harness",
    "artifact_harness_failed": "artifact_harness",
    "answer_missing": "delivery",
}


class DeterministicEvaluator:
    """Evaluate public Workbench facts without asking a model to grade itself."""

    def evaluate(
        self,
        case: EvalCase,
        observation: EvaluationObservation,
        *,
        run_id: str,
    ) -> EvalCaseResult:
        expected = case.expectation
        assertions: list[AssertionResult] = []
        assertions.append(AssertionResult(
            dimension="runtime", code="runtime_execution_failed",
            passed=not observation.runtime_error, expected="", actual=observation.runtime_error,
            critical=True,
        ))
        self._equal(assertions, "intent", "intent_mismatch", expected.business_intent,
                    observation.business_intent)
        self._equal(assertions, "intent", "turn_action_mismatch", expected.turn_action,
                    observation.turn_action)
        self._equal(assertions, "capability", "capability_mismatch", expected.capability_id,
                    observation.capability_id)
        self._equal(assertions, "retrieval_tool", "retrieval_mode_mismatch",
                    expected.retrieval_mode, observation.retrieval_mode)
        self._equal(assertions, "capability", "execution_mode_mismatch",
                    expected.execution_mode, observation.execution_mode)
        self._equal(assertions, "delivery", "delivery_mode_mismatch",
                    expected.delivery_mode, observation.delivery_mode, critical=True)
        if expected.must_not_use_fallback:
            assertions.append(AssertionResult(
                dimension="capability", code="fallback_contract_broken",
                passed=not observation.routing_fallback_used, expected=False,
                actual=observation.routing_fallback_used,
            ))
        selected_skill_names = set(observation.selected_skill_names)
        for skill_name in expected.required_skill_names:
            assertions.append(AssertionResult(
                dimension="skill_selection", code="required_skill_missing",
                passed=skill_name in selected_skill_names, expected=skill_name,
                actual=observation.selected_skill_names, critical=True,
            ))
        for skill_name in expected.forbidden_skill_names:
            assertions.append(AssertionResult(
                dimension="skill_selection", code="forbidden_skill_selected",
                passed=skill_name not in selected_skill_names, expected=f"not:{skill_name}",
                actual=observation.selected_skill_names, critical=True,
            ))
        if expected.skill_semantic_required:
            assertions.append(AssertionResult(
                dimension="skill_selection", code="skill_semantic_missing",
                passed=observation.skill_semantic_used,
                expected=True, actual=observation.skill_semantic_used,
            ))
        if expected.skill_rerank_required:
            assertions.append(AssertionResult(
                dimension="skill_selection", code="skill_rerank_missing",
                passed=observation.skill_rerank_used,
                expected=True, actual=observation.skill_rerank_used,
            ))
        for node in expected.required_graph_nodes:
            assertions.append(AssertionResult(
                dimension="runtime", code="missing_graph_node",
                passed=node in observation.graph_path, expected=node,
                actual=observation.graph_path,
            ))
        for event in expected.required_trace_events:
            present = event in observation.trace_events
            assertions.append(AssertionResult(
                dimension="retrieval_tool", code="missing_trace_event",
                passed=present, expected=event, actual=observation.trace_events,
            ))
            if present:
                status = observation.trace_statuses.get(event, "unknown")
                assertions.append(AssertionResult(
                    dimension="retrieval_tool", code="trace_event_failed",
                    passed=status not in {"failed", "blocked", "unavailable"},
                    expected="completed_or_non_failure", actual=status,
                ))
        extensions = {Path(name).suffix.casefold() for name in observation.artifact_names}
        for extension in expected.required_artifact_extensions:
            assertions.append(AssertionResult(
                dimension="artifact_harness", code="artifact_missing",
                passed=extension.casefold() in extensions, expected=extension,
                actual=sorted(extensions), critical=True,
            ))
        for extension in expected.forbidden_artifact_extensions:
            assertions.append(AssertionResult(
                dimension="artifact_harness", code="forbidden_artifact_created",
                passed=extension.casefold() not in extensions, expected=f"not:{extension}",
                actual=sorted(extensions), critical=True,
            ))
        if expected.harness_required:
            assertions.append(AssertionResult(
                dimension="artifact_harness", code="artifact_harness_failed",
                passed=observation.artifact_harness_passed is True, expected=True,
                actual=observation.artifact_harness_passed, critical=True,
            ))
        if expected.answer_required:
            assertions.append(AssertionResult(
                dimension="delivery", code="answer_missing",
                passed=observation.answer_visible, expected=True,
                actual=observation.answer_visible,
            ))
        failures = [
            FailureAttribution(
                layer=_CODE_LAYER.get(item.code, item.dimension), code=item.code,
                summary=f"expected={item.expected!r}; actual={item.actual!r}",
                assertion_index=index,
            )
            for index, item in enumerate(assertions) if not item.passed
        ]
        failures.sort(key=lambda item: (_LAYER_ORDER.get(item.layer, 99), item.assertion_index))
        return EvalCaseResult(
            id=f"evalres_{uuid4().hex}", run_id=run_id, case_id=case.id,
            tenant_id=case.tenant_id, passed=not failures, criticality=case.criticality,
            assertions=assertions, failures=failures,
            root_failure=failures[0] if failures else None,
            observation=observation,
        )

    @staticmethod
    def _equal(
        assertions: list[AssertionResult], dimension: str, code: str,
        expected: str, actual: str, *, critical: bool = False,
    ) -> None:
        if expected:
            assertions.append(AssertionResult(
                dimension=dimension, code=code, passed=expected == actual,
                expected=expected, actual=actual, critical=critical,
            ))
