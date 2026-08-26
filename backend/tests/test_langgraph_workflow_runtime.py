import os
import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.domains.assistant.contracts import BusinessIntent, ExecutionMode, TurnAction
from app.domains.conversations.repository import ConversationRepository
from app.domains.runs import (
    LangGraphWorkflowRuntime,
    OperationResult,
    RunPointer,
    RunPayloadStore,
    RunRepository,
    RunStatus,
    WorkflowRunRequest,
    WorkflowRunInterrupted,
)
from app.observability import emit_tool_progress


class SequencedOperation:
    def __init__(self, results: list[OperationResult]):
        self.results = list(results)
        self.calls = 0
        self.requests: list[WorkflowRunRequest] = []

    def invoke(self, request: WorkflowRunRequest) -> OperationResult:
        self.calls += 1
        self.requests.append(request)
        return self.results.pop(0)


def request_for(
    *,
    run_id: str = "run_test",
    intent: BusinessIntent = BusinessIntent.POLICY_QUESTION,
    action: TurnAction = TurnAction.START_NEW,
    mode: ExecutionMode = ExecutionMode.EXECUTE,
    max_iterations: int = 2,
) -> WorkflowRunRequest:
    return WorkflowRunRequest(
        run_id=run_id,
        conversation_id="conv_test",
        tenant_id="tenant_test",
        owner_user_id="user_test",
        business_intent=intent,
        turn_action=action,
        execution_mode=mode,
        max_iterations=max_iterations,
        payload={"message": "test"},
    )


def test_runtime_retries_failed_quality_gate_then_succeeds():
    operation = SequencedOperation([
        OperationResult(answer="", trace=[]),
        OperationResult(answer="有证据支持的最终回答", trace=[{"event": "model.answer", "status": "completed"}]),
    ])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())

    result = runtime.start(request_for())

    assert result.status == RunStatus.SUCCEEDED
    assert result.iteration == 2
    assert operation.calls == 2
    assert result.graph_path.count("execute") == 2
    assert result.graph_path.count("quality_gate") == 2
    assert result.quality_issues == []


def test_runtime_streams_safe_graph_node_lifecycle_without_state_payload():
    operation = SequencedOperation([
        OperationResult(answer="包含敏感业务正文的回答", trace=[]),
    ])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())
    events: list[tuple[str, str, dict]] = []

    result = runtime.start(
        request_for(),
        progress=lambda event, summary, details: events.append((event, summary, details)),
    )

    assert result.status == RunStatus.SUCCEEDED
    assert [event for event, _, _ in events] == [
        "graph.node.prepare.started",
        "graph.node.prepare.completed",
        "graph.node.execute.started",
        "graph.node.execute.completed",
        "graph.node.quality_gate.started",
        "graph.node.quality_gate.completed",
        "graph.node.finalize.started",
        "graph.node.finalize.completed",
    ]
    assert events[0][2] == {
        "node": "prepare", "lifecycle": "started", "step": 1, "event_status": "active",
    }
    assert events[-1][2]["event_status"] == "completed"
    serialized = json.dumps(events, ensure_ascii=False)
    assert "包含敏感业务正文的回答" not in serialized
    assert '"request"' not in serialized
    assert '"artifacts"' not in serialized


def test_runtime_interrupts_at_checkpoint_boundary_before_next_tool_node():
    operation = SequencedOperation([OperationResult(answer="不应执行", trace=[])])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())
    cancellation = {"requested": False}

    def progress(event: str, _summary: str, _details: dict) -> None:
        if event == "graph.node.prepare.completed":
            cancellation["requested"] = True

    with pytest.raises(WorkflowRunInterrupted) as raised:
        runtime.start(
            request_for(run_id="run_steered"),
            progress=progress,
            cancel_requested=lambda: cancellation["requested"],
        )

    assert operation.calls == 0
    assert raised.value.checkpoint_thread_id == runtime._thread_id(request_for(run_id="run_steered"))
    snapshot = runtime.graph.get_state(runtime._config(request_for(run_id="run_steered")))
    assert list(snapshot.values.get("graph_path") or []) == ["prepare"]


def test_runtime_streams_repair_loop_as_real_graph_nodes():
    operation = SequencedOperation([
        OperationResult(answer="", trace=[]),
        OperationResult(answer="修复后的回答", trace=[]),
    ])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())
    events: list[str] = []

    runtime.start(
        request_for(),
        progress=lambda event, _summary, _details: events.append(event),
    )

    assert "graph.node.repair.started" in events
    assert "graph.node.repair.completed" in events
    assert events.count("graph.node.execute.started") == 2
    assert events.count("graph.node.quality_gate.completed") == 2


def test_runtime_streams_tool_events_inside_execute_node_and_filters_private_fields():
    class ObservableOperation:
        def invoke(self, _request: WorkflowRunRequest) -> OperationResult:
            emit_tool_progress(
                "tool.test.started",
                "正在执行真实工具",
                count=3,
                content="不得进入事件流的正文",
                path="D:/private/result.xlsx",
            )
            emit_tool_progress(
                "tool.test.completed",
                "真实工具执行完成",
                status="completed",
                count=3,
            )
            return OperationResult(answer="完成", trace=[])

    runtime = LangGraphWorkflowRuntime(ObservableOperation(), checkpointer=InMemorySaver())
    events: list[tuple[str, dict]] = []

    runtime.start(
        request_for(),
        progress=lambda event, _summary, details: events.append((event, details)),
    )

    names = [event for event, _details in events]
    assert names.index("graph.node.execute.started") < names.index("tool.test.started")
    assert names.index("tool.test.completed") < names.index("graph.node.execute.completed")
    tool_details = dict(events[names.index("tool.test.started")][1])
    assert tool_details == {"count": 3, "event_status": "active"}


def test_runtime_repair_node_feeds_spreadsheet_harness_failure_to_next_execution():
    operation = SequencedOperation([
        OperationResult(
            answer="已生成第一版",
            artifacts=[{
                "kind": "spreadsheet",
                "path": "first.xlsx",
                "display_name": "first.xlsx",
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "metadata": {
                    "operation_plan": {"objective": "生成汇总", "operations": []},
                    "validation": {"passed": False, "checks": {"required_sheet:汇总": False}},
                    "artifact_harness": {
                        "passed": False, "status": "failed", "issues": ["generation_validation_failed"],
                    },
                },
            }],
        ),
        OperationResult(
            answer="已修复并生成第二版",
            artifacts=[{
                "kind": "spreadsheet",
                "path": "second.xlsx",
                "display_name": "second.xlsx",
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "metadata": {"artifact_harness": {"passed": True, "status": "passed"}},
            }],
        ),
    ])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())

    result = runtime.start(request_for(intent=BusinessIntent.SPREADSHEET_PROCESSING))

    assert result.status == RunStatus.SUCCEEDED
    assert result.graph_path == [
        "prepare", "execute", "quality_gate", "repair", "execute", "quality_gate", "finalize",
    ]
    feedback = operation.requests[1].payload["quality_feedback"]
    assert feedback["quality_issues"] == ["artifact_harness_failed:spreadsheet"]
    assert feedback["artifacts"][0]["validation"]["checks"]["required_sheet:汇总"] is False
    assert feedback["artifacts"][0]["operation_plan"]["objective"] == "生成汇总"


def test_document_route_loops_when_required_artifact_is_missing():
    operation = SequencedOperation([
        OperationResult(answer="只生成了文字，没有文档", trace=[]),
        OperationResult(
            answer="已生成公文",
            artifacts=[{
                "kind": "document",
                "path": "draft.docx",
                "display_name": "draft.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "metadata": {},
            }],
        ),
    ])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())

    result = runtime.start(request_for(intent=BusinessIntent.DOCUMENT_GENERATION))

    assert result.status == RunStatus.SUCCEEDED
    assert result.iteration == 2
    assert operation.calls == 2
    assert result.graph_path.count("execute") == 2


def test_runtime_waits_for_user_input_without_repeating_operation():
    operation = SequencedOperation([
        OperationResult(
            answer="请先上传上一年度工作簿和当年数据。",
            trace=[{"event": "workflow.waiting_input", "status": "waiting"}],
        )
    ])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())

    result = runtime.start(request_for(intent=BusinessIntent.SPREADSHEET_PROCESSING))

    assert result.status == RunStatus.WAITING_INPUT
    assert result.iteration == 1
    assert operation.calls == 1
    assert result.graph_path[-1] == "finalize"


def test_runtime_blocks_reuse_branch_that_creates_a_new_artifact():
    operation = SequencedOperation([
        OperationResult(
            answer="错误地重跑了任务",
            artifacts=[{
                "kind": "recruitment_report",
                "path": "unexpected.xlsx",
                "display_name": "unexpected.xlsx",
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "metadata": {},
            }],
            trace=[{"event": "recruitment.evaluate", "status": "completed"}],
        )
    ])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())

    result = runtime.start(request_for(
        intent=BusinessIntent.RECRUITMENT_SCREENING,
        action=TurnAction.COMPARE_EXISTING,
        mode=ExecutionMode.REUSE_RESULT,
    ))

    assert result.status == RunStatus.FAILED
    assert operation.calls == 1
    assert "reuse_created_artifact" in result.quality_issues
    assert "reuse_invoked_mutating_capability" in result.quality_issues


def test_runtime_marks_governed_hr_output_as_waiting_review():
    operation = SequencedOperation([
        OperationResult(
            answer="已生成候选人辅助筛选报告",
            artifacts=[{
                "kind": "recruitment_report",
                "path": "report.xlsx",
                "display_name": "report.xlsx",
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "metadata": {"human_review_required": True},
            }],
            trace=[{"event": "human_review.gate", "status": "waiting"}],
        )
    ])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())

    result = runtime.start(request_for(intent=BusinessIntent.RECRUITMENT_SCREENING))

    assert result.status == RunStatus.WAITING_REVIEW
    assert result.graph_path[-1] == "human_review_gate"
    assert result.requires_human_review is True

    resumed = runtime.resume(
        RunPointer(run_id="run_test", tenant_id="tenant_test", owner_user_id="user_test"),
        approved=True,
        comment="人工已核对评分证据",
    )

    assert resumed.status == RunStatus.SUCCEEDED
    assert resumed.graph_path[-2:] == ["approval.resume", "finalize"]
    assert operation.calls == 1


def test_runtime_rejects_artifact_that_failed_real_harness():
    operation = SequencedOperation([OperationResult(
        answer="文件生成完成",
        artifacts=[{
            "kind": "spreadsheet",
            "path": "broken.xlsx",
            "display_name": "broken.xlsx",
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "metadata": {"artifact_harness": {"passed": False, "status": "failed"}},
        }],
    )])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())

    result = runtime.start(request_for(
        intent=BusinessIntent.SPREADSHEET_PROCESSING,
        max_iterations=1,
    ))

    assert result.status == RunStatus.EXHAUSTED
    assert "artifact_harness_failed:spreadsheet" in result.quality_issues


def test_runtime_rejected_review_resumes_without_reexecuting_operation():
    operation = SequencedOperation([
        OperationResult(
            answer="待复核公文",
            artifacts=[{
                "kind": "document",
                "path": "draft.docx",
                "display_name": "draft.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "metadata": {"requires_human_review": True},
            }],
        )
    ])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())
    request = request_for(
        run_id="run_rejected",
        intent=BusinessIntent.DOCUMENT_GENERATION,
    )

    started = runtime.start(request)
    resumed = runtime.resume(
        RunPointer(run_id=request.run_id, tenant_id=request.tenant_id, owner_user_id=request.owner_user_id),
        approved=False,
        comment="落款日期需要修改",
    )

    assert started.status == RunStatus.WAITING_REVIEW
    assert resumed.status == RunStatus.REJECTED
    assert resumed.approval_comment == "落款日期需要修改"
    assert operation.calls == 1


def test_runtime_checkpoint_can_be_read_through_run_pointer():
    operation = SequencedOperation([OperationResult(answer="checkpointed")])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())
    request = request_for(run_id="run_checkpoint")

    started = runtime.start(request)
    restored = runtime.get(RunPointer(
        run_id=request.run_id,
        tenant_id=request.tenant_id,
        owner_user_id=request.owner_user_id,
    ))

    assert restored is not None
    assert restored.run_id == started.run_id
    assert restored.status == RunStatus.SUCCEEDED
    assert restored.graph_path == started.graph_path


def test_runtime_offloads_large_operation_result_and_restores_from_reference(tmp_path):
    large_evidence = "候选人证据" * 20_000
    operation = SequencedOperation([OperationResult(
        answer="外置结果已生成",
        artifacts=[{
            "kind": "analysis",
            "path": "result.json",
            "display_name": "result.json",
            "mime_type": "application/json",
            "metadata": {"evidence": large_evidence},
        }],
        trace=[{"event": "analysis.completed", "details": {"report": large_evidence}}],
    )])
    saver = InMemorySaver()
    store = RunPayloadStore(tmp_path / "payloads.db")
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=saver, payload_store=store)
    request = request_for(run_id="run_offloaded")

    started = runtime.start(request)
    snapshot = runtime.graph.get_state(runtime._config(request))
    serialized_state = json.dumps(snapshot.values, ensure_ascii=False)

    assert started.answer == "外置结果已生成"
    assert started.artifacts[0]["metadata"]["evidence"] == large_evidence
    assert snapshot.values["result_ref"]["schema_version"] == "run_payload_ref_v1"
    assert snapshot.values["answer"] == ""
    assert snapshot.values["artifacts"] == []
    assert snapshot.values["trace"] == []
    assert "候选人证据" not in serialized_state
    assert len(serialized_state.encode("utf-8")) < 2_000

    recreated = LangGraphWorkflowRuntime(
        SequencedOperation([]), checkpointer=saver, payload_store=RunPayloadStore(tmp_path / "payloads.db"),
    )
    restored = recreated.get(RunPointer(
        run_id=request.run_id, tenant_id=request.tenant_id, owner_user_id=request.owner_user_id,
    ))
    assert restored is not None
    assert restored.answer == started.answer
    assert restored.artifacts == started.artifacts


def test_run_payload_reference_is_tenant_and_owner_scoped(tmp_path):
    store = RunPayloadStore(tmp_path / "payloads.db")
    pointer = RunPointer(run_id="run_scoped", tenant_id="tenant_a", owner_user_id="user_a")
    reference = store.put(pointer, OperationResult(answer="private result"))

    with pytest.raises(KeyError):
        store.get(pointer.model_copy(update={"owner_user_id": "user_b"}), reference)


def test_run_payload_store_round_trips_generic_subgraph_payload(tmp_path):
    store = RunPayloadStore(tmp_path / "payloads.db")
    pointer = RunPointer(run_id="run_recruitment", tenant_id="tenant_a", owner_user_id="user_a")
    payload = [{"candidate": "A", "evidence": "x" * 5000}]

    reference = store.put_json(pointer, payload, kind="recruitment.candidate_batches")

    assert reference.byte_size > 5000
    assert reference.kind == "recruitment.candidate_batches"
    assert store.get_json(pointer, reference) == payload
    with pytest.raises(KeyError):
        store.get_json(pointer.model_copy(update={"tenant_id": "tenant_b"}), reference)


POSTGRES_TEST_DSN = os.getenv("UNIHR_TEST_POSTGRES_DSN", "").strip()


@pytest.mark.skipif(not POSTGRES_TEST_DSN, reason="UNIHR_TEST_POSTGRES_DSN is not configured")
def test_postgres_checkpointer_survives_runtime_recreation():
    request = request_for(run_id="run_durable")
    pointer = RunPointer(
        run_id=request.run_id,
        tenant_id=request.tenant_id,
        owner_user_id=request.owner_user_id,
    )
    first_operation = SequencedOperation([OperationResult(answer="durable")])
    first_runtime = LangGraphWorkflowRuntime.with_postgres(
        first_operation, POSTGRES_TEST_DSN, max_pool_size=2,
    )
    try:
        first_runtime.delete(pointer)
        started = first_runtime.start(request)
    finally:
        first_runtime.close()

    second_operation = SequencedOperation([])
    second_runtime = LangGraphWorkflowRuntime.with_postgres(
        second_operation, POSTGRES_TEST_DSN, max_pool_size=2,
    )
    try:
        restored = second_runtime.get(pointer)
        second_runtime.delete(pointer)
    finally:
        second_runtime.close()

    assert started.status == RunStatus.SUCCEEDED
    assert restored is not None
    assert restored.answer == "durable"
    assert second_operation.calls == 0


def test_runtime_deletes_all_checkpoints_for_run():
    request = request_for(run_id="run_delete_checkpoint")
    runtime = LangGraphWorkflowRuntime(
        SequencedOperation([OperationResult(answer="temporary")]), checkpointer=InMemorySaver(),
    )
    pointer = RunPointer(
        run_id=request.run_id,
        tenant_id=request.tenant_id,
        owner_user_id=request.owner_user_id,
    )
    runtime.start(request)

    runtime.delete(pointer)

    assert runtime.get(pointer) is None


def test_control_plane_run_repository_is_tenant_scoped_and_keeps_events(tmp_path: Path):
    conversation_repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = conversation_repository.create_conversation("Run repository")
    run_repository = RunRepository(tmp_path / "unihr.db")
    request = WorkflowRunRequest(
        run_id="run_owned",
        conversation_id=conversation.id,
        tenant_id=conversation.tenant_id,
        owner_user_id=conversation.owner_user_id,
        business_intent=BusinessIntent.POLICY_QUESTION,
        turn_action=TurnAction.START_NEW,
        execution_mode=ExecutionMode.EXECUTE,
        payload={},
    )
    operation = SequencedOperation([OperationResult(answer="done")])
    runtime = LangGraphWorkflowRuntime(operation, checkpointer=InMemorySaver())

    started_record = run_repository.start(request)
    result = runtime.start(request)
    pointer = RunPointer(
        run_id=request.run_id,
        tenant_id=request.tenant_id,
        owner_user_id=request.owner_user_id,
    )
    finished_record = run_repository.finish(result, pointer)
    run_repository.append_events(pointer, [
        {"event": "graph.run.started", "status": "completed", "summary": "started"},
        {"event": "graph.run.completed", "status": "succeeded", "summary": "done"},
    ])

    assert started_record.status == RunStatus.RUNNING
    assert finished_record.status == RunStatus.SUCCEEDED
    assert [item.event for item in run_repository.list_events(pointer)] == [
        "graph.run.started", "graph.run.completed",
    ]
    assert run_repository.list_for_conversation(
        conversation.id,
        tenant_id=conversation.tenant_id,
        owner_user_id=conversation.owner_user_id,
    )[0].id == request.run_id
    with pytest.raises(KeyError):
        run_repository.get(pointer.model_copy(update={"owner_user_id": "someone_else"}))


def test_skill_node_operations_are_aggregated_from_persisted_events(tmp_path: Path):
    conversation_repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = conversation_repository.create_conversation("Skill node operations")
    run_repository = RunRepository(tmp_path / "unihr.db")
    request = WorkflowRunRequest(
        run_id="run_skill_operations",
        conversation_id=conversation.id,
        tenant_id=conversation.tenant_id,
        owner_user_id=conversation.owner_user_id,
        business_intent=BusinessIntent.DOCUMENT_GENERATION,
        turn_action=TurnAction.START_NEW,
        execution_mode=ExecutionMode.EXECUTE,
        payload={},
    )
    pointer = RunPointer(
        run_id=request.run_id,
        tenant_id=request.tenant_id,
        owner_user_id=request.owner_user_id,
    )
    run_repository.start(request)
    run_repository.append_events(pointer, [
        {"event": "skill.graph.started", "status": "running", "summary": "started"},
        {"event": "skill.graph.node.started", "status": "running", "summary": "node-a"},
        {"event": "skill.graph.node.failed", "status": "failed", "summary": "node-a"},
        {"event": "skill.graph.recovery.started", "status": "running", "summary": "retry"},
        {"event": "skill.graph.node.reused", "status": "completed", "summary": "node-before"},
        {
            "event": "skill.graph.node.completed", "status": "completed", "summary": "node-a",
            "duration_ms": 120, "estimated_cost_cny": 0.02,
        },
        {
            "event": "skill.graph.node.completed", "status": "completed", "summary": "node-b",
            "duration_ms": 180, "estimated_cost_cny": 0.01,
        },
        {"event": "skill.graph.recovery.completed", "status": "completed", "summary": "done"},
    ])

    metrics = run_repository.skill_node_operations(conversation.tenant_id)

    assert metrics.run_count == 1
    assert metrics.node_started == 1
    assert metrics.node_completed == 2
    assert metrics.node_failed == 1
    assert metrics.node_reused == 1
    assert metrics.recovery_started == metrics.recovery_completed == 1
    assert metrics.retry_success_rate == 1
    assert metrics.reuse_rate == pytest.approx(1 / 3)
    assert metrics.average_duration_ms == 150
    assert metrics.total_estimated_cost_cny == pytest.approx(0.03)
