from __future__ import annotations

from typing import Any

from .contracts import CapabilityObservation


def observe_workbench_turn(
    case_id: str,
    response: Any,
    *,
    duration_ms: int = 0,
) -> CapabilityObservation:
    """Project the public Workbench turn contract into release-smoke facts."""
    intent = response.interpretation.business_intent
    observed_route = str(getattr(intent, "value", intent))
    artifacts = list(response.artifacts or [])
    trace = list(response.trace or [])
    return CapabilityObservation(
        case_id=case_id,
        observed_route=observed_route,
        graph_path=list(response.run.graph_path or []),
        trace_events=[str(item.get("event")) for item in trace if item.get("event")],
        trace_statuses={
            str(item.get("event")): str(item.get("status") or "unknown")
            for item in trace if item.get("event")
        },
        artifact_names=[str(item.name) for item in artifacts],
        harness_passed=_combined_harness_status(artifacts),
        answer_visible=bool(str(response.assistant_message.content or "").strip()),
        duration_ms=duration_ms,
    )


def observe_skill_record(
    case_id: str,
    record: Any,
    *,
    duration_ms: int = 0,
) -> CapabilityObservation:
    """Project a governed Skill draft into the same release-smoke contract."""
    metadata = dict(getattr(record, "metadata", {}) or {})
    authoring = dict(metadata.get("authoring") or {})
    validation = dict(metadata.get("validation") or {})
    eval_suite = dict(metadata.get("eval_suite") or {})
    exists = bool(str(getattr(record, "id", "") or "").strip())
    return CapabilityObservation(
        case_id=case_id,
        observed_route="skill_create" if exists else "",
        graph_path=[str(item) for item in authoring.get("graph_path") or []],
        trace_events=["skill.draft.created"] if exists else [],
        trace_statuses={"skill.draft.created": "completed"} if exists else {},
        harness_passed=(
            bool(validation.get("publishable")) and eval_suite.get("status") == "passed"
        ),
        answer_visible=bool(str(getattr(record, "display_name", "") or "").strip()),
        duration_ms=duration_ms,
    )


def _combined_harness_status(artifacts: list[Any]) -> bool | None:
    statuses: list[bool] = []
    for artifact in artifacts:
        metadata = dict(getattr(artifact, "metadata", {}) or {})
        for key in ("artifact_harness", "harness", "validation"):
            report = metadata.get(key)
            if isinstance(report, dict) and "passed" in report:
                statuses.append(bool(report["passed"]))
    return all(statuses) if statuses else None
