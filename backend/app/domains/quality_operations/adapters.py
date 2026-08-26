from __future__ import annotations

from typing import Any

from .contracts import EvaluationObservation


def observe_workbench_response(response: Any, *, duration_ms: int = 0) -> EvaluationObservation:
    """Project only the public Workbench contract into stable evaluation facts."""

    interpretation = response.interpretation
    artifacts = list(response.artifacts or [])
    trace = list(response.trace or [])
    return EvaluationObservation(
        capability_id=str(interpretation.selected_capability_id or ""),
        business_intent=_value(interpretation.business_intent),
        turn_action=_value(interpretation.turn_action),
        retrieval_mode=_value(interpretation.retrieval_mode),
        execution_mode=_value(interpretation.execution_mode),
        delivery_mode=_value(interpretation.delivery_mode),
        routing_fallback_used=bool(interpretation.routing_fallback_used),
        matched_layer=str(interpretation.matched_layer or ""),
        graph_path=list(response.run.graph_path or []),
        trace_events=[str(item.get("event")) for item in trace if item.get("event")],
        trace_statuses={
            str(item.get("event")): str(item.get("status") or "unknown")
            for item in trace if item.get("event")
        },
        artifact_names=[str(item.name) for item in artifacts],
        artifact_harness_passed=_combined_harness_status(artifacts),
        answer_visible=bool(str(response.assistant_message.content or "").strip()),
        answer_excerpt=str(response.assistant_message.content or "").strip()[:500],
        duration_ms=duration_ms,
        model_used=str(getattr(response, "model_status", "") or ""),
        raw_refs={
            "conversation_id": str(response.conversation.id),
            "run_id": str(response.run.id),
            "message_id": str(response.assistant_message.id),
        },
    )


def _value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _combined_harness_status(artifacts: list[Any]) -> bool | None:
    statuses: list[bool] = []
    for artifact in artifacts:
        metadata = dict(getattr(artifact, "metadata", {}) or {})
        for key in ("artifact_harness", "harness", "validation"):
            report = metadata.get(key)
            if isinstance(report, dict) and "passed" in report:
                statuses.append(bool(report["passed"]))
    return all(statuses) if statuses else None
