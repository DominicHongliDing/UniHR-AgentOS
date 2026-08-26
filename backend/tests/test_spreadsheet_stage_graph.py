from langgraph.checkpoint.memory import InMemorySaver
import pytest

from app.domains.runs import RunPayloadStore
from app.domains.workbench.spreadsheet_stage_graph import (
    SpreadsheetStageGraphRequest,
    SpreadsheetStageGraphRuntime,
)


def test_spreadsheet_stage_graph_checkpoints_and_reuses_completed_atomic_stages(tmp_path):
    calls: list[str] = []

    def execute(stage_id, inputs, prior, pointer):
        calls.append(stage_id)
        if stage_id == "harness":
            return {
                "workbench_result": {"answer": "完成", "artifacts": [], "trace": []},
                "trace": [],
            }
        return {"value": inputs["message"], "prior": sorted(prior), "trace": []}

    runtime = SpreadsheetStageGraphRuntime(
        checkpointer=InMemorySaver(),
        payload_store=RunPayloadStore(tmp_path / "payloads.db"),
        executor=execute,
    )
    request = SpreadsheetStageGraphRequest(
        run_id="run-1",
        conversation_id="conversation-1",
        tenant_id="tenant-1",
        owner_user_id="user-1",
        inputs={"message": "生成表格"},
    )

    first = runtime.start(request)
    second = runtime.start(request)

    assert first.status == "succeeded"
    assert list(first.stage_refs) == ["plan", "execute", "validate", "harness"]
    assert calls == ["plan", "execute", "validate", "harness"]
    assert second.status == "succeeded"
    assert second.reused_stage_count == 4
    assert calls == ["plan", "execute", "validate", "harness"]
    assert any(item["event"] == "spreadsheet.stage.graph.reused" for item in second.trace)


def test_spreadsheet_stage_graph_resumes_from_failed_stage_without_rebuilding_predecessors(tmp_path):
    calls: list[str] = []
    validation_attempts = 0

    def execute(stage_id, inputs, prior, pointer):
        nonlocal validation_attempts
        calls.append(stage_id)
        if stage_id == "validate":
            validation_attempts += 1
            if validation_attempts == 1:
                raise RuntimeError("injected_validation_failure")
        if stage_id == "harness":
            return {
                "workbench_result": {"answer": "恢复成功", "artifacts": [], "trace": []},
                "trace": [],
            }
        return {"stage": stage_id, "trace": []}

    runtime = SpreadsheetStageGraphRuntime(
        checkpointer=InMemorySaver(),
        payload_store=RunPayloadStore(tmp_path / "payloads.db"),
        executor=execute,
    )
    request = SpreadsheetStageGraphRequest(
        run_id="run-recover",
        conversation_id="conversation-1",
        tenant_id="tenant-1",
        owner_user_id="user-1",
        inputs={"message": "生成表格"},
    )

    with pytest.raises(RuntimeError, match="injected_validation_failure"):
        runtime.start(request)
    recovered = runtime.start(request)

    assert recovered.status == "succeeded"
    assert calls == ["plan", "execute", "validate", "validate", "harness"]
    assert recovered.reused_stage_count == 2
    assert [
        item["stage_id"] for item in recovered.trace
        if item["event"] == "spreadsheet.stage.reused"
    ] == ["plan", "execute"]
