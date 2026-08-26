from __future__ import annotations

from app.domains.assistant.capability_registry import default_task_capability_registry
from app.domains.assistant.contracts import (
    BusinessIntent,
    DeliveryMode,
    ExecutionMode,
    ExecutionRoute,
    RetrievalMode,
    TaskIntent,
    TurnAction,
    TurnInterpretation,
)
from app.domains.capability_operations import CapabilityOperationsCenter, RoutingGoldenCase
from app.domains.conversations.contracts import AdminFeedbackRecord
from app.domains.runs.contracts import RunEventRecord, RunRecord, RunStatus


def _run(*, status: RunStatus = RunStatus.SUCCEEDED) -> RunRecord:
    return RunRecord(
        id="run_1", tenant_id="tenant", owner_user_id="user",
        conversation_id="conversation", message_id="message_1", status=status,
        business_intent=BusinessIntent.POLICY_QUESTION,
        turn_action=TurnAction.START_NEW, execution_mode=ExecutionMode.EXECUTE,
        iteration=1, max_iterations=2, graph_path=["prepare", "execute", "quality_gate"],
        checkpoint_thread_id="thread", created_at="2026-08-10T00:00:00+00:00",
        updated_at="2026-08-10T00:00:01+00:00",
    )


def test_operations_snapshot_joins_registry_runs_fallback_and_feedback():
    feedback = AdminFeedbackRecord(
        id="feedback", conversation_id="conversation", message_id="message_1",
        user_id="user", helpful=False, comment="证据不够", created_at="2026-08-10T00:00:00Z",
        updated_at="2026-08-10T00:00:00Z", owner_username="hr-user",
        owner_display_name="HR 用户", conversation_title="政策咨询", question="怎么定",
        answer="回答",
    )
    fallback_event = RunEventRecord(
        id="event", run_id="run_1", sequence=1, event="capability.fallback",
        status="completed", summary="fallback", created_at="2026-08-10T00:00:00Z",
    )

    snapshot = CapabilityOperationsCenter(default_task_capability_registry()).snapshot(
        runs=[_run()], events_by_run={"run_1": [fallback_event]}, feedback=[feedback],
        window_days=30,
    )

    policy = next(item for item in snapshot.capabilities if item.capability_id == "policy_evidence")
    assert snapshot.summary.registered_capabilities == 10
    assert snapshot.summary.total_runs == 1
    assert snapshot.summary.fallback_rate == 1.0
    assert snapshot.summary.helpful_rate == 0.0
    assert policy.owner == "Policy RAG Agent"
    assert policy.run_count == 1
    assert policy.completed_runs == 1
    assert policy.fallback_runs == 1
    assert policy.feedback_count == 1


class _Interpreter:
    def interpret(self, message: str, **_: object) -> TurnInterpretation:
        return TurnInterpretation(
            business_intent=BusinessIntent.SPREADSHEET_PROCESSING,
            execution_route=ExecutionRoute.HR_WORKFLOW,
            confidence=0.9, reason_code="TEST", matched_layer="governed_model_first:test",
            turn_action=TurnAction.START_NEW, execution_mode=ExecutionMode.EXECUTE,
            delivery_mode=DeliveryMode.CREATE_ARTIFACT,
            retrieval_mode=RetrievalMode.NONE,
            selected_capability_id="spreadsheet_processing",
            task_intent=TaskIntent(
                domain=BusinessIntent.SPREADSHEET_PROCESSING,
                objective=message,
            ),
        )


def test_routing_golden_report_checks_capability_action_and_retrieval_contract():
    report = CapabilityOperationsCenter(default_task_capability_registry()).evaluate_routing(
        _Interpreter(),
        [RoutingGoldenCase(
            "sheet", "口语化表格", "给我弄个进度盘子", "spreadsheet_processing",
            TurnAction.START_NEW, RetrievalMode.NONE,
            expected_delivery_mode=DeliveryMode.CREATE_ARTIFACT,
        )],
    )

    assert report.status == "passed"
    assert report.accuracy == 1.0
    assert report.model_routed_cases == 1
    assert report.results[0].execution_mode == "execute"
    assert report.results[0].actual_delivery_mode == "create_artifact"


def test_routing_golden_report_rejects_correct_domain_with_wrong_delivery_mode():
    report = CapabilityOperationsCenter(default_task_capability_registry()).evaluate_routing(
        _Interpreter(),
        [RoutingGoldenCase(
            "inline-polish", "聊天内润色", "帮我润色这段话", "spreadsheet_processing",
            expected_delivery_mode=DeliveryMode.INLINE_TRANSFORM,
        )],
    )

    assert report.status == "needs_attention"
    assert report.results[0].issues == [
        "delivery:inline_transform!=create_artifact",
    ]
