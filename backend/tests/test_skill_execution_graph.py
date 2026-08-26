import threading
import time

from langgraph.checkpoint.memory import InMemorySaver

from app.domains.skills import (
    SkillExecutionPlan,
    SkillGraphBudget,
    SkillGraphRequest,
    SkillGraphRuntime,
    SkillHandoff,
    SkillNodeExecutionRequest,
    SkillNodeNeedsInput,
    SkillNodeExecutorRegistry,
    SkillNodeOutput,
    SkillPlanNode,
)


def _node(
    skill_id: str,
    display_name: str,
    *,
    depends_on: list[str] | None = None,
    provides: list[str] | None = None,
) -> SkillPlanNode:
    return SkillPlanNode(
        skill_id=skill_id,
        skill_version=1,
        display_name=display_name,
        score=0.9,
        selection_mode="automatic",
        provides=provides or [],
        depends_on=depends_on or [],
        reason="测试计划",
    )


def _request(plan: SkillExecutionPlan) -> SkillGraphRequest:
    return SkillGraphRequest(
        run_id="run_skill_graph",
        conversation_id="conv_skill_graph",
        tenant_id="tenant_demo",
        owner_user_id="user_demo",
        plan=plan,
        initial_inputs={"resource_ref": "resource://resume-batch"},
    )


def test_legacy_skill_handoff_is_upgraded_to_shared_contract():
    handoff = SkillHandoff.model_validate({
        "source_skill_id": "read_source",
        "target_skill_id": "build_artifact",
        "output_refs": ["result://source"],
    })

    assert handoff.source_kind == "skill"
    assert handoff.source_id == "read_source"
    assert handoff.target_id == "build_artifact"
    assert handoff.source_skill_id == "read_source"


class FailSecondNodeOnce:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, list[str]]] = []

    def execute(self, request: SkillNodeExecutionRequest) -> SkillNodeOutput:
        self.calls.append((
            request.node.skill_id,
            request.attempt,
            [item.source_skill_id for item in request.handoffs],
        ))
        if request.node.skill_id == "generate_excel" and request.attempt == 1:
            raise RuntimeError("formula_recalculation_failed")
        return SkillNodeOutput(
            output_refs=[f"result://{request.node.skill_id}/{request.attempt}"],
            artifact_refs=(
                ["artifact://recruitment-progress.xlsx"]
                if request.node.skill_id == "generate_excel" else []
            ),
        )


class AlwaysFailSecondNode(FailSecondNodeOnce):
    def execute(self, request: SkillNodeExecutionRequest) -> SkillNodeOutput:
        self.calls.append((
            request.node.skill_id,
            request.attempt,
            [item.source_skill_id for item in request.handoffs],
        ))
        if request.node.skill_id == "generate_excel":
            raise RuntimeError("formula_recalculation_failed")
        return SkillNodeOutput(output_refs=["result://read-template"])


class NeedsInputThenSucceeds:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, bool]] = []

    def execute(self, request: SkillNodeExecutionRequest) -> SkillNodeOutput:
        has_mapping = bool(request.initial_inputs.get("field_mapping"))
        self.calls.append((request.node.skill_id, request.attempt, has_mapping))
        if request.node.skill_id == "map_fields" and not has_mapping:
            raise SkillNodeNeedsInput(
                ["field_mapping"],
                "请补充源字段与目标字段的映射关系。",
            )
        return SkillNodeOutput(
            output_refs=[f"result://{request.node.skill_id}"],
            estimated_cost_cny=0.012 if request.node.skill_id == "map_fields" else 0.003,
        )


class FailUntilReleased:
    def __init__(self) -> None:
        self.released = False
        self.calls: list[str] = []

    def execute(self, request: SkillNodeExecutionRequest) -> SkillNodeOutput:
        self.calls.append(request.node.skill_id)
        if request.node.skill_id == "generate_excel" and not self.released:
            raise RuntimeError("excel_engine_unavailable")
        return SkillNodeOutput(output_refs=[f"result://{request.node.skill_id}"])


class ParallelBranchExecutor:
    def __init__(self, *, fail_branch: str = "") -> None:
        self.fail_branch = fail_branch
        self.started: dict[str, float] = {}
        self.finished: dict[str, float] = {}
        self.lock = threading.Lock()

    def execute(self, request: SkillNodeExecutionRequest) -> SkillNodeOutput:
        with self.lock:
            self.started[request.node.skill_id] = time.perf_counter()
        time.sleep(0.18)
        if request.node.skill_id == self.fail_branch:
            raise RuntimeError("branch_failed")
        with self.lock:
            self.finished[request.node.skill_id] = time.perf_counter()
        return SkillNodeOutput(
            output_refs=[f"result://{request.node.skill_id}"],
            artifact_refs=[f"artifact://{request.node.skill_id}"],
        )


def test_skill_node_executor_registry_dispatches_by_capability_and_rejects_ambiguity():
    document = FailSecondNodeOnce()
    spreadsheet = FailSecondNodeOnce()
    registry = SkillNodeExecutorRegistry()
    registry.register("document.compose", document)
    registry.register("spreadsheet.transform", spreadsheet)
    request = SkillNodeExecutionRequest(
        run_id="run", conversation_id="conv", tenant_id="tenant", owner_user_id="user",
        node=_node("document", "公文", provides=["document.compose", "artifact.create"]),
        attempt=1,
    )

    registry.execute(request)
    assert [item[0] for item in document.calls] == ["document"]
    assert spreadsheet.calls == []

    ambiguous = request.model_copy(update={
        "node": _node(
            "mixed", "混合", provides=["document.compose", "spreadsheet.transform"],
        ),
    })
    try:
        registry.execute(ambiguous)
    except RuntimeError as exc:
        assert str(exc).startswith("skill_node_executor_ambiguous:")
    else:
        raise AssertionError("one node must not silently dispatch to two professional agents")


def test_skill_graph_retries_only_failed_node_and_reuses_typed_handoff():
    plan = SkillExecutionPlan(
        status="ready",
        nodes=[
            _node("read_template", "读取模板", provides=["spreadsheet.inspect"]),
            _node(
                "generate_excel",
                "生成 Excel",
                depends_on=["read_template"],
                provides=["spreadsheet.transform", "artifact.create"],
            ),
        ],
        max_loop_rounds=2,
        human_review_required=True,
    )
    executor = FailSecondNodeOnce()
    runtime = SkillGraphRuntime(checkpointer=InMemorySaver(), executor=executor)

    result = runtime.start(_request(plan))

    assert result.status == "succeeded"
    assert result.requires_human_review is True
    assert executor.calls == [
        ("read_template", 1, []),
        ("generate_excel", 1, ["read_template"]),
        ("generate_excel", 2, ["read_template"]),
    ]
    assert result.node_states["read_template"].attempts == 1
    assert result.node_states["generate_excel"].attempts == 2
    assert result.graph_path == [
        "skill_graph.prepare",
        "skill:read_template",
        "skill:generate_excel:failed",
        "skill:generate_excel",
    ]
    assert any(item.event == "skill.graph.handoff" for item in map(_Trace, result.trace))
    handoff = result.handoffs[0]
    assert handoff.contract_version == "unihr.handoff/v1"
    assert handoff.source_kind == "skill"
    assert handoff.source_id == "read_template"
    assert handoff.target_id == "generate_excel"
    assert handoff.business_refs == {
        "conversation_id": "conv_skill_graph",
        "run_id": "run_skill_graph",
    }


def test_skill_graph_stops_downstream_after_node_retry_budget_is_exhausted():
    plan = SkillExecutionPlan(
        status="ready",
        nodes=[
            _node("read_template", "读取模板", provides=["spreadsheet.inspect"]),
            _node("generate_excel", "生成 Excel", depends_on=["read_template"]),
            _node("publish", "发布文件", depends_on=["generate_excel"]),
        ],
        max_loop_rounds=2,
    )
    executor = AlwaysFailSecondNode()
    runtime = SkillGraphRuntime(checkpointer=InMemorySaver(), executor=executor)

    result = runtime.start(_request(plan))

    assert result.status == "failed"
    assert result.failed_node_id == "generate_excel"
    assert executor.calls == [
        ("read_template", 1, []),
        ("generate_excel", 1, ["read_template"]),
        ("generate_excel", 2, ["read_template"]),
    ]
    assert "publish" not in result.node_states
    assert result.node_states["read_template"].status == "succeeded"
    assert result.node_states["generate_excel"].status == "exhausted"


def test_skill_graph_reuses_completed_checkpoint_on_parent_retry():
    plan = SkillExecutionPlan(
        status="ready",
        nodes=[_node("read_template", "读取模板")],
        max_loop_rounds=2,
    )
    executor = FailSecondNodeOnce()
    runtime = SkillGraphRuntime(checkpointer=InMemorySaver(), executor=executor)
    request = _request(plan)

    first = runtime.start(request)
    second = runtime.start(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.reused is True
    assert executor.calls == [("read_template", 1, [])]
    assert second.graph_path == ["skill_graph.reused"]
    assert second.trace[0]["event"] == "skill.graph.reused"


def test_skill_graph_reruns_business_node_when_execution_inputs_change():
    plan = SkillExecutionPlan(
        status="ready",
        nodes=[_node("document", "公文", provides=["document.compose"])],
        max_loop_rounds=2,
    )

    class BusinessExecutor:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def execute(self, request: SkillNodeExecutionRequest) -> SkillNodeOutput:
            repaired = bool(
                dict(request.initial_inputs.get("operation_payload") or {}).get("quality_feedback")
            )
            self.calls.append(repaired)
            return SkillNodeOutput(data={
                "executor_kind": "professional_sub_agent",
                "workbench_result": {"answer": "repaired" if repaired else "draft"},
            })

    executor = BusinessExecutor()
    runtime = SkillGraphRuntime(checkpointer=InMemorySaver(), executor=executor)
    first_request = _request(plan).model_copy(update={
        "initial_inputs": {"operation_payload": {}},
    })
    repaired_request = first_request.model_copy(update={
        "initial_inputs": {"operation_payload": {"quality_feedback": {"issues": ["layout"]}}},
    })

    first = runtime.start(first_request)
    repaired = runtime.start(repaired_request)

    assert first.status == repaired.status == "succeeded"
    assert executor.calls == [False, True]
    assert repaired.reused is False
    assert any(item["event"] == "skill.graph.input_changed" for item in repaired.trace)


def test_skill_graph_pauses_for_input_and_recovers_without_rerunning_predecessor():
    plan = SkillExecutionPlan(
        status="ready",
        nodes=[
            _node("read_template", "读取模板", provides=["spreadsheet.inspect"]),
            _node("map_fields", "字段映射", depends_on=["read_template"]),
            _node("publish", "生成结果", depends_on=["map_fields"]),
        ],
        max_loop_rounds=2,
    )
    executor = NeedsInputThenSucceeds()
    runtime = SkillGraphRuntime(checkpointer=InMemorySaver(), executor=executor)
    request = _request(plan)

    paused = runtime.start(request)

    assert paused.status == "waiting_input"
    assert paused.failed_node_id == "map_fields"
    assert paused.required_inputs == ["field_mapping"]
    assert "源字段" in paused.input_prompt
    assert executor.calls == [
        ("read_template", 1, False),
        ("map_fields", 1, False),
    ]

    resumed = runtime.recover(
        request,
        supplied_inputs={"field_mapping": {"姓名": "name"}},
        comment="已补充字段映射",
    )

    assert resumed.status == "succeeded"
    assert executor.calls == [
        ("read_template", 1, False),
        ("map_fields", 1, False),
        ("map_fields", 1, True),
        ("publish", 1, True),
    ]
    assert resumed.node_states["read_template"].attempts == 1
    assert resumed.node_states["read_template"].status == "reused"
    assert any(item["event"] == "skill.graph.recovery.completed" for item in resumed.trace)


def test_manual_retry_runs_failed_node_and_downstream_only():
    plan = SkillExecutionPlan(
        status="ready",
        nodes=[
            _node("read_template", "读取模板"),
            _node("generate_excel", "生成 Excel", depends_on=["read_template"]),
            _node("publish", "发布文件", depends_on=["generate_excel"]),
        ],
        max_loop_rounds=1,
    )
    executor = FailUntilReleased()
    runtime = SkillGraphRuntime(checkpointer=InMemorySaver(), executor=executor)
    request = _request(plan)

    failed = runtime.start(request)
    assert failed.status == "failed"
    assert executor.calls == ["read_template", "generate_excel"]

    executor.released = True
    resumed = runtime.recover(request, node_id="generate_excel", comment="Excel 引擎已恢复")

    assert resumed.status == "succeeded"
    assert executor.calls == [
        "read_template", "generate_excel", "generate_excel", "publish",
    ]
    assert resumed.retry_count == 1
    assert resumed.node_states["read_template"].attempts == 1


def test_manual_retry_budget_prevents_infinite_human_loop():
    plan = SkillExecutionPlan(
        status="ready",
        nodes=[_node("generate_excel", "生成 Excel")],
        max_loop_rounds=1,
    )
    executor = FailUntilReleased()
    runtime = SkillGraphRuntime(checkpointer=InMemorySaver(), executor=executor)
    request = _request(plan).model_copy(update={
        "budget": SkillGraphBudget(max_manual_retries=1),
    })

    runtime.start(request)
    first_retry = runtime.recover(request)
    assert first_retry.status == "failed"

    try:
        runtime.recover(request)
    except ValueError as exc:
        assert str(exc) == "skill_graph_manual_retry_budget_exhausted"
    else:
        raise AssertionError("manual retry budget should be enforced")


def test_skill_graph_runs_independent_dependency_branches_concurrently():
    plan = SkillExecutionPlan(
        status="ready",
        nodes=[
            _node("read_case", "读取 Case"),
            _node("make_word", "生成 Word", depends_on=["read_case"]),
            _node("make_excel", "生成 Excel", depends_on=["read_case"]),
        ],
        max_loop_rounds=1,
    )
    executor = ParallelBranchExecutor()
    runtime = SkillGraphRuntime(checkpointer=InMemorySaver(), executor=executor)

    started = time.perf_counter()
    result = runtime.start(_request(plan))
    elapsed = time.perf_counter() - started

    assert result.status == "succeeded"
    assert elapsed < 0.48
    assert abs(executor.started["make_word"] - executor.started["make_excel"]) < 0.08
    assert any(item["event"] == "skill.graph.parallel_wave.completed" for item in result.trace)
    assert result.graph_path[-1] == "skill_parallel:make_word+make_excel"


def test_parallel_branch_failure_preserves_successful_sibling_for_recovery():
    plan = SkillExecutionPlan(
        status="ready",
        nodes=[
            _node("read_case", "读取 Case"),
            _node("make_word", "生成 Word", depends_on=["read_case"]),
            _node("make_excel", "生成 Excel", depends_on=["read_case"]),
        ],
        max_loop_rounds=1,
    )
    executor = ParallelBranchExecutor(fail_branch="make_excel")
    runtime = SkillGraphRuntime(checkpointer=InMemorySaver(), executor=executor)
    request = _request(plan)

    failed = runtime.start(request)

    assert failed.status == "failed"
    assert failed.failed_node_id == "make_excel"
    assert failed.node_states["make_word"].status == "succeeded"
    assert failed.node_states["make_word"].output.artifact_refs == ["artifact://make_word"]
    assert failed.node_states["read_case"].status == "succeeded"
    word_started = executor.started["make_word"]

    executor.fail_branch = ""
    recovered = runtime.recover(request, node_id="make_excel", comment="表格引擎已恢复")

    assert recovered.status == "succeeded"
    assert recovered.node_states["read_case"].status == "reused"
    assert recovered.node_states["make_word"].status == "reused"
    assert recovered.node_states["make_excel"].status == "succeeded"
    assert executor.started["make_word"] == word_started


class _Trace:
    def __init__(self, raw: dict[str, object]) -> None:
        self.event = str(raw.get("event") or "")
