from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .catalog import default_capability_smoke_cases
from .contracts import (
    CapabilityAcceptanceReport,
    CapabilityCaseResult,
    CapabilityObservation,
    CapabilitySmokeCase,
)


_DATASET_VERSION = "unihr-cross-agent-smoke-v1"


class CapabilityAcceptanceEvaluator:
    """Validate all user-visible capabilities through one release-smoke interface."""

    def __init__(self, cases: Sequence[CapabilitySmokeCase] | None = None) -> None:
        self.cases = list(cases or default_capability_smoke_cases())

    def evaluate(
        self, observations: Sequence[CapabilityObservation]
    ) -> CapabilityAcceptanceReport:
        observed_by_id = {item.case_id: item for item in observations}
        results = []
        for case in self.cases:
            observation = observed_by_id.get(case.id)
            issues = self._issues(case, observation)
            results.append(CapabilityCaseResult(
                case_id=case.id,
                capability=case.capability,
                passed=not issues,
                issues=issues,
                observed_route=observation.observed_route if observation else "",
                duration_ms=observation.duration_ms if observation else 0,
            ))
        passed_count = sum(item.passed for item in results)
        return CapabilityAcceptanceReport(
            dataset_version=_DATASET_VERSION,
            status="passed" if passed_count == len(results) else "failed",
            case_count=len(results),
            passed_count=passed_count,
            pass_rate=passed_count / len(results) if results else 0.0,
            results=results,
        )

    @staticmethod
    def _issues(
        case: CapabilitySmokeCase, observation: CapabilityObservation | None
    ) -> list[str]:
        if observation is None:
            return ["observation_missing"]
        issues = []
        if observation.observed_route != case.expected_route:
            issues.append(
                f"route_mismatch:{case.expected_route}!={observation.observed_route}"
            )
        for node in case.required_graph_nodes:
            if node not in observation.graph_path:
                issues.append(f"missing_graph_node:{node}")
        for event in case.required_trace_events:
            if event not in observation.trace_events:
                issues.append(f"missing_trace_event:{event}")
            elif observation.trace_statuses.get(event) in {"failed", "blocked", "unavailable"}:
                issues.append(
                    f"trace_event_not_completed:{event}:{observation.trace_statuses[event]}"
                )
        observed_extensions = {Path(name).suffix.casefold() for name in observation.artifact_names}
        for extension in case.required_artifact_extensions:
            if extension.casefold() not in observed_extensions:
                issues.append(f"missing_artifact:{extension}")
        if case.harness_required and observation.harness_passed is not True:
            issues.append("harness_not_passed")
        if not observation.answer_visible:
            issues.append("answer_not_visible")
        return issues
