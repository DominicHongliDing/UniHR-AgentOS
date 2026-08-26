from __future__ import annotations

import hashlib
import json
import operator
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Iterable, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, computed_field, model_validator

from app.domains.handoffs import HandoffEndpointKind, TypedHandoff
from app.observability import emit_tool_progress

from .orchestration import SkillExecutionPlan, SkillPlanNode


class SkillNodeStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    REUSED = "reused"
    WAITING_INPUT = "waiting_input"


class SkillNodeNeedsInput(RuntimeError):
    """Pause one Skill node without losing completed predecessors."""

    def __init__(self, required_inputs: list[str], prompt: str = "") -> None:
        super().__init__("skill_input_required")
        self.required_inputs = list(dict.fromkeys(item.strip() for item in required_inputs if item.strip()))
        self.prompt = prompt.strip()[:1_000]


class SkillGraphBudget(BaseModel):
    max_node_duration_ms: int = Field(default=120_000, ge=100, le=600_000)
    max_node_cost_cny: float = Field(default=2.0, ge=0, le=1_000)
    max_manual_retries: int = Field(default=2, ge=0, le=10)


class SkillNodeOutput(BaseModel):
    output_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    estimated_cost_cny: float | None = Field(default=None, ge=0)


class SkillNodeState(BaseModel):
    skill_id: str
    skill_version: int
    display_name: str
    status: SkillNodeStatus = SkillNodeStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    output: SkillNodeOutput = Field(default_factory=SkillNodeOutput)
    error_code: str = ""
    duration_ms: int = Field(default=0, ge=0)
    estimated_cost_cny: float | None = Field(default=None, ge=0)
    manual_retries: int = Field(default=0, ge=0)
    required_inputs: list[str] = Field(default_factory=list)
    input_prompt: str = ""


class SkillHandoff(TypedHandoff):
    """Skill compatibility view over the shared typed handoff contract."""

    source_kind: HandoffEndpointKind = HandoffEndpointKind.SKILL
    target_kind: HandoffEndpointKind = HandoffEndpointKind.SKILL

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        normalized.setdefault("source_id", normalized.get("source_skill_id"))
        normalized.setdefault("target_id", normalized.get("target_skill_id"))
        return normalized

    @computed_field
    @property
    def source_skill_id(self) -> str:
        return self.source_id

    @computed_field
    @property
    def target_skill_id(self) -> str:
        return self.target_id


class SkillNodeExecutionRequest(BaseModel):
    run_id: str
    conversation_id: str
    tenant_id: str
    owner_user_id: str
    node: SkillPlanNode
    attempt: int = Field(ge=1)
    initial_inputs: dict[str, Any] = Field(default_factory=dict)
    handoffs: list[SkillHandoff] = Field(default_factory=list)


class SkillNodeExecutor(Protocol):
    def execute(self, request: SkillNodeExecutionRequest) -> SkillNodeOutput: ...


class ContractSkillNodeExecutor:
    """Execute the F3 control-plane contract before S4 binds atomic tools.

    This adapter validates typed dependency handoffs and produces stable output
    references. S4 can replace it with Word/Excel/search adapters without
    changing the graph interface or Workbench caller.
    """

    def execute(self, request: SkillNodeExecutionRequest) -> SkillNodeOutput:
        received_sources = {item.source_skill_id for item in request.handoffs}
        missing_dependencies = [
            item for item in request.node.depends_on if item not in received_sources
        ]
        if missing_dependencies:
            raise RuntimeError(
                "skill_handoff_missing:" + ",".join(sorted(missing_dependencies))
            )
        reference = (
            f"skill-output://{request.run_id}/{request.node.skill_id}/"
            f"v{request.node.skill_version}"
        )
        return SkillNodeOutput(
            output_refs=[reference],
            data={
                "provided_capabilities": list(request.node.provides),
                "declared_outputs": list(request.node.outputs),
                "dependency_count": len(request.handoffs),
            },
        )


class SkillNodeExecutorRegistry:
    """Route a Skill node to one capability adapter behind a small interface.

    Graph callers only know ``execute``.  Adapters may wrap deterministic tools,
    professional sub-agents, MCP calls, or harnesses without exposing those
    implementations to the graph module.
    """

    def __init__(self, fallback: SkillNodeExecutor | None = None) -> None:
        self._fallback = fallback or ContractSkillNodeExecutor()
        self._adapters: dict[str, SkillNodeExecutor] = {}

    def register(
        self,
        capabilities: str | Iterable[str],
        adapter: SkillNodeExecutor,
    ) -> "SkillNodeExecutorRegistry":
        values = [capabilities] if isinstance(capabilities, str) else list(capabilities)
        for raw in values:
            capability = str(raw).strip()
            if not capability:
                continue
            current = self._adapters.get(capability)
            if current is not None and current is not adapter:
                raise ValueError(f"skill_node_executor_already_registered:{capability}")
            self._adapters[capability] = adapter
        return self

    def execute(self, request: SkillNodeExecutionRequest) -> SkillNodeOutput:
        selected = [
            self._adapters[capability]
            for capability in request.node.provides
            if capability in self._adapters
        ]
        unique = list(dict.fromkeys(id(item) for item in selected))
        if len(unique) > 1:
            raise RuntimeError(
                "skill_node_executor_ambiguous:" + ",".join(request.node.provides)
            )
        return selected[0].execute(request) if selected else self._fallback.execute(request)


class SkillGraphRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=120)
    conversation_id: str = Field(min_length=1, max_length=120)
    tenant_id: str = Field(min_length=1, max_length=120)
    owner_user_id: str = Field(min_length=1, max_length=120)
    plan: SkillExecutionPlan
    initial_inputs: dict[str, Any] = Field(default_factory=dict)
    budget: SkillGraphBudget = Field(default_factory=SkillGraphBudget)

    @property
    def plan_fingerprint(self) -> str:
        payload = json.dumps(
            self.plan.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @property
    def input_fingerprint(self) -> str:
        payload = json.dumps(
            self.initial_inputs,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class SkillGraphResult(BaseModel):
    status: str
    node_states: dict[str, SkillNodeState] = Field(default_factory=dict)
    handoffs: list[SkillHandoff] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    graph_path: list[str] = Field(default_factory=list)
    failed_node_id: str = ""
    requires_human_review: bool = False
    checkpoint_thread_id: str = ""
    reused: bool = False
    required_inputs: list[str] = Field(default_factory=list)
    input_prompt: str = ""
    total_duration_ms: int = Field(default=0, ge=0)
    total_estimated_cost_cny: float = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    reused_node_count: int = Field(default=0, ge=0)


class _SkillGraphState(TypedDict, total=False):
    request: dict[str, Any]
    plan_fingerprint: str
    input_fingerprint: str
    status: str
    node_states: dict[str, dict[str, Any]]
    handoffs: list[dict[str, Any]]
    trace: Annotated[list[dict[str, Any]], operator.add]
    graph_path: Annotated[list[str], operator.add]
    failed_node_id: str
    requires_human_review: bool
    recovery_counts: dict[str, int]


class SkillGraphRuntime:
    """Run a version-locked Skill plan as a checkpointed LangGraph subgraph."""

    def __init__(
        self,
        *,
        checkpointer: BaseCheckpointSaver,
        executor: SkillNodeExecutor | None = None,
    ) -> None:
        self.checkpointer = checkpointer
        self.executor = executor or ContractSkillNodeExecutor()

    def start(self, request: SkillGraphRequest) -> SkillGraphResult:
        if request.plan.status != "ready" or not request.plan.nodes:
            return SkillGraphResult(
                status="skipped",
                requires_human_review=request.plan.human_review_required,
                checkpoint_thread_id=self._thread_id(request),
            )
        graph = self._compile(request)
        config = self._config(request)
        existing = dict(graph.get_state(config).values)
        if (
            existing.get("status") == "succeeded"
            and existing.get("plan_fingerprint") == request.plan_fingerprint
        ):
            if existing.get("input_fingerprint") != request.input_fingerprint:
                return self._restart_for_changed_inputs(existing, request)
            result = self._to_result(existing, request)
            return result.model_copy(update={
                "trace": [{
                    "event": "skill.graph.reused",
                    "status": "completed",
                    "summary": "Skill 子图已复用同一 Run 中完成的节点与输出引用",
                    "node_count": len(result.node_states),
                }],
                "graph_path": ["skill_graph.reused"],
                "reused": True,
            })
        if existing.get("status") in {"failed", "waiting_input"}:
            result = self._to_result(existing, request)
            return result.model_copy(update={
                "trace": [{
                    "event": "skill.graph.awaiting_recovery",
                    "status": "paused",
                    "summary": (
                        "Skill 子图正在等待补充资料"
                        if result.status == "waiting_input"
                        else "Skill 子图已停止自动循环，等待人工仅重试失败节点"
                    ),
                    "failed_node_id": result.failed_node_id,
                }],
                "graph_path": ["skill_graph.awaiting_recovery"],
            })
        initial = self._initial_state(request)
        values = self._invoke(graph, request, initial)
        return self._final_result(values, request)

    def _restart_for_changed_inputs(
        self,
        existing: dict[str, Any],
        request: SkillGraphRequest,
    ) -> SkillGraphResult:
        """Re-run from the first business executor when material inputs change.

        This is used by parent quality repair: the Skill plan remains stable, but
        quality feedback becomes a new execution input. Contract-only predecessors
        stay reusable while the professional Agent node and its downstream rerun.
        """
        raw_states = dict(existing.get("node_states") or {})
        node_order = [item.skill_id for item in request.plan.nodes]
        target_index = next((
            index for index, skill_id in enumerate(node_order)
            if str(
                dict(dict(raw_states.get(skill_id) or {}).get("output") or {})
                .get("data", {})
                .get("executor_kind") or ""
            ) == "professional_sub_agent"
        ), 0)
        invalidated = self._node_and_descendants(
            request.plan.nodes, node_order[target_index],
        )
        retained_states = {
            key: value for key, value in raw_states.items()
            if key not in invalidated
            and str(dict(value).get("status") or "") in {"succeeded", "reused"}
        }
        retained_handoffs = [
            item for item in list(existing.get("handoffs") or [])
            if str(item.get("source_skill_id") or "") in retained_states
        ]
        initial = self._initial_state(request)
        initial.update({
            "node_states": retained_states,
            "handoffs": retained_handoffs,
            "trace": [{
                "event": "skill.graph.input_changed",
                "status": "active",
                "summary": "执行输入或质量反馈已变化，从最早受影响的 Skill 节点重新执行",
                "retained_node_count": len(retained_states),
            }],
            "graph_path": ["skill_graph.input_changed"],
        })
        self.checkpointer.delete_thread(self._thread_id(request))
        graph = self._compile(request)
        values = self._invoke(graph, request, initial)
        return self._final_result(values, request)

    def recover(
        self,
        request: SkillGraphRequest,
        *,
        node_id: str = "",
        supplied_inputs: dict[str, Any] | None = None,
        comment: str = "",
    ) -> SkillGraphResult:
        """Resume from one failed/waiting node while reusing successful predecessors."""

        if request.plan.status != "ready" or not request.plan.nodes:
            raise ValueError("skill_graph_plan_not_recoverable")
        graph = self._compile(request)
        existing = dict(graph.get_state(self._config(request)).values)
        if not existing:
            raise KeyError(request.run_id)
        current = self._to_result(existing, request)
        target_id = node_id.strip() or current.failed_node_id
        target = next((item for item in request.plan.nodes if item.skill_id == target_id), None)
        if target is None:
            raise ValueError("skill_graph_failed_node_not_found")
        current_state = current.node_states.get(target_id)
        if current_state is None or current_state.status not in {
            SkillNodeStatus.FAILED,
            SkillNodeStatus.EXHAUSTED,
            SkillNodeStatus.WAITING_INPUT,
        }:
            raise ValueError("skill_graph_node_not_recoverable")
        recovery_counts = {
            key: int(value) for key, value in dict(existing.get("recovery_counts") or {}).items()
        }
        next_recovery = recovery_counts.get(target_id, 0) + 1
        if next_recovery > request.budget.max_manual_retries:
            raise ValueError("skill_graph_manual_retry_budget_exhausted")
        recovery_counts[target_id] = next_recovery
        node_order = [item.skill_id for item in request.plan.nodes]
        invalidated = self._node_and_descendants(request.plan.nodes, target_id)
        retained_states = {
            key: value for key, value in dict(existing.get("node_states") or {}).items()
            if key not in invalidated
            and str(dict(value).get("status") or "") in {"succeeded", "reused"}
        }
        retained_handoffs = [
            item for item in list(existing.get("handoffs") or [])
            if str(item.get("source_skill_id") or "") in retained_states
        ]
        merged_inputs = {
            **request.initial_inputs,
            **dict(supplied_inputs or {}),
        }
        supplement = str(merged_inputs.get("user_supplement") or "").strip()
        if supplement:
            for required_input in current_state.required_inputs:
                merged_inputs.setdefault(required_input, supplement)
        recovered_request = request.model_copy(update={"initial_inputs": merged_inputs})
        initial = self._initial_state(recovered_request)
        initial.update({
            "node_states": retained_states,
            "handoffs": retained_handoffs,
            "recovery_counts": recovery_counts,
            "trace": [{
                "event": "skill.graph.recovery.started",
                "status": "active",
                "summary": f"仅恢复 Skill 节点：{target.display_name}",
                "skill_id": target_id,
                "manual_retry": next_recovery,
                "comment": comment.strip()[:500],
            }],
            "graph_path": [f"skill_graph.recover:{target_id}"],
        })
        # Start a new checkpoint lineage with the deliberately retained state.
        # LangGraph would otherwise merge the old terminal value back into the
        # new input and keep the node in waiting/failed status.
        self.checkpointer.delete_thread(self._thread_id(request))
        recovered_graph = self._compile(recovered_request)
        values = self._invoke(recovered_graph, recovered_request, initial)
        result = self._final_result(values, recovered_request)
        return result.model_copy(update={
            "trace": [
                *result.trace,
                {
                    "event": "skill.graph.recovery.completed",
                    "status": "completed" if result.status == "succeeded" else "paused",
                    "summary": (
                        "失败节点已恢复，后续 Skill 已从检查点继续"
                        if result.status == "succeeded"
                        else "恢复后仍需处理当前节点，已继续保留前序结果"
                    ),
                    "skill_id": target_id,
                    "manual_retry": next_recovery,
                },
            ],
        })

    @staticmethod
    def _initial_state(request: SkillGraphRequest) -> _SkillGraphState:
        return {
            "request": request.model_dump(mode="json"),
            "plan_fingerprint": request.plan_fingerprint,
            "input_fingerprint": request.input_fingerprint,
            "status": "running",
            "node_states": {},
            "handoffs": [],
            "trace": [{
                "event": "skill.graph.started",
                "status": "completed",
                "summary": f"Skill 子图已启动，共 {len(request.plan.nodes)} 个版本锁定节点",
                "node_count": len(request.plan.nodes),
            }],
            "graph_path": ["skill_graph.prepare"],
            "failed_node_id": "",
            "requires_human_review": request.plan.human_review_required,
            "recovery_counts": {},
        }

    @staticmethod
    def _invoke(graph: Any, request: SkillGraphRequest, initial: _SkillGraphState) -> dict[str, Any]:
        return dict(graph.invoke(
            initial,
            config={
                **SkillGraphRuntime._config(request),
                "recursion_limit": max(
                    30,
                    len(request.plan.nodes) * (request.plan.max_loop_rounds + 2) + 10,
                ),
            },
        ))

    def _final_result(
        self,
        values: dict[str, Any],
        request: SkillGraphRequest,
    ) -> SkillGraphResult:
        result = self._to_result(dict(values), request)
        if result.status == "waiting_input":
            terminal_event = {
                "event": "skill.graph.waiting_input",
                "status": "paused",
                "summary": result.input_prompt or "Skill 节点等待补充资料",
                "failed_node_id": result.failed_node_id,
                "required_inputs": result.required_inputs,
            }
            return result.model_copy(update={"trace": [*result.trace, terminal_event]})
        terminal_event = {
            "event": (
                "skill.graph.completed"
                if result.status == "succeeded" else "skill.graph.failed"
            ),
            "status": "completed" if result.status == "succeeded" else "failed",
            "summary": (
                "Skill 子图全部节点完成，输出引用可交给业务执行器"
                if result.status == "succeeded"
                else f"Skill 子图在 {result.failed_node_id or '未知节点'} 达到重试上限"
            ),
            "failed_node_id": result.failed_node_id,
        }
        return result.model_copy(update={"trace": [*result.trace, terminal_event]})

    def _compile(self, request: SkillGraphRequest):
        waves = self._dependency_waves(request.plan.nodes)
        if any(len(wave) > 1 for wave in waves):
            return self._compile_parallel(request, waves)
        builder = StateGraph(_SkillGraphState)
        nodes = request.plan.nodes
        for index, node in enumerate(nodes):
            builder.add_node(node.skill_id, self._node_runner(request, node))
            next_node = nodes[index + 1].skill_id if index + 1 < len(nodes) else END
            builder.add_conditional_edges(
                node.skill_id,
                self._route_after_node(node.skill_id, request.plan.max_loop_rounds),
                {"retry": node.skill_id, "next": next_node, "failed": END, "paused": END},
            )
        builder.add_edge(START, nodes[0].skill_id)
        return builder.compile(
            checkpointer=self.checkpointer,
            name="unihr-skill-execution-" + request.plan_fingerprint[:12],
        )

    def _compile_parallel(
        self,
        request: SkillGraphRequest,
        waves: list[list[SkillPlanNode]],
    ):
        """Compile dependency waves; independent nodes in one wave run concurrently.

        A wave is one checkpoint transaction. Each Skill still produces an
        independent node state, Typed Handoff and output reference, while sibling
        failures cannot erase successful branch results.
        """
        builder = StateGraph(_SkillGraphState)
        names = [f"parallel_wave_{index + 1}" for index in range(len(waves))]
        for index, (name, wave) in enumerate(zip(names, waves)):
            builder.add_node(
                name,
                self._parallel_wave_runner(
                    request, wave, final_wave=index == len(waves) - 1,
                ),
            )
            destination = names[index + 1] if index + 1 < len(names) else END
            builder.add_conditional_edges(
                name,
                self._route_after_parallel_wave,
                {"next": destination, "stop": END},
            )
        builder.add_edge(START, names[0])
        return builder.compile(
            checkpointer=self.checkpointer,
            name="unihr-skill-execution-" + request.plan_fingerprint[:12],
        )

    def _parallel_wave_runner(
        self,
        request: SkillGraphRequest,
        wave: list[SkillPlanNode],
        *,
        final_wave: bool,
    ):
        node_order = {node.skill_id: index for index, node in enumerate(wave)}

        def execute_node(
            node: SkillPlanNode,
            state: _SkillGraphState,
        ) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
            local_state = dict(state)
            local_state["node_states"] = dict(state.get("node_states") or {})
            local_state["handoffs"] = list(state.get("handoffs") or [])
            traces: list[dict[str, Any]] = []
            paths: list[str] = []
            new_handoffs: list[dict[str, Any]] = []
            while True:
                partial = self._node_runner(request, node)(local_state)
                traces.extend(list(partial.get("trace") or []))
                paths.extend(list(partial.get("graph_path") or []))
                raw_states = dict(partial.get("node_states") or {})
                node_state = dict(raw_states[node.skill_id])
                local_state["node_states"] = raw_states
                for handoff in partial.get("handoffs") or []:
                    if str(handoff.get("source_skill_id") or "") == node.skill_id:
                        new_handoffs.append(dict(handoff))
                parsed = SkillNodeState.model_validate(node_state)
                if parsed.status == SkillNodeStatus.FAILED and parsed.attempts < request.plan.max_loop_rounds:
                    continue
                return node.skill_id, node_state, traces, paths, new_handoffs

        def run(state: _SkillGraphState) -> dict[str, Any]:
            started_at = time.perf_counter()
            results: list[tuple[str, dict[str, Any], list[dict[str, Any]], list[str], list[dict[str, Any]]]] = []
            with ThreadPoolExecutor(max_workers=len(wave), thread_name_prefix="unihr-skill") as pool:
                futures = [pool.submit(execute_node, node, state) for node in wave]
                for future in as_completed(futures):
                    results.append(future.result())
            results.sort(key=lambda item: node_order[item[0]])

            merged_states = dict(state.get("node_states") or {})
            merged_handoffs = list(state.get("handoffs") or [])
            trace: list[dict[str, Any]] = []
            graph_path: list[str] = []
            for skill_id, node_state, node_trace, node_path, handoffs in results:
                merged_states[skill_id] = node_state
                trace.extend(node_trace)
                graph_path.extend(node_path)
                merged_handoffs.extend(handoffs)
            merged_handoffs = _unique_handoffs(merged_handoffs)
            states = {
                skill_id: SkillNodeState.model_validate(raw)
                for skill_id, raw in merged_states.items()
            }
            waiting = [node for node in wave if states[node.skill_id].status == SkillNodeStatus.WAITING_INPUT]
            exhausted = [node for node in wave if states[node.skill_id].status == SkillNodeStatus.EXHAUSTED]
            status = "waiting_input" if waiting else "failed" if exhausted else "succeeded" if final_wave else "running"
            failed = (waiting or exhausted)
            duration_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
            trace.append({
                "event": "skill.graph.parallel_wave.completed",
                "status": "paused" if waiting else "failed" if exhausted else "completed",
                "summary": f"并行阶段完成：{len(wave)} 个独立 Skill 分支",
                "skill_ids": [node.skill_id for node in wave],
                "duration_ms": duration_ms,
                "succeeded_count": sum(
                    states[node.skill_id].status in {SkillNodeStatus.SUCCEEDED, SkillNodeStatus.REUSED}
                    for node in wave
                ),
                "failed_count": len(exhausted),
                "waiting_count": len(waiting),
            })
            graph_path.append("skill_parallel:" + "+".join(node.skill_id for node in wave))
            return {
                "status": status,
                "failed_node_id": failed[0].skill_id if failed else "",
                "node_states": merged_states,
                "handoffs": merged_handoffs,
                "trace": trace,
                "graph_path": graph_path,
            }

        return run

    @staticmethod
    def _route_after_parallel_wave(state: _SkillGraphState) -> str:
        return "stop" if state.get("status") in {"failed", "waiting_input"} else "next"

    @staticmethod
    def _dependency_waves(nodes: list[SkillPlanNode]) -> list[list[SkillPlanNode]]:
        """Return deterministic topological waves without changing plan order."""
        remaining = {node.skill_id: node for node in nodes}
        completed: set[str] = set()
        waves: list[list[SkillPlanNode]] = []
        while remaining:
            wave = [
                node for node in nodes
                if node.skill_id in remaining and set(node.depends_on).issubset(completed)
            ]
            if not wave:
                raise ValueError("skill_graph_dependency_cycle")
            waves.append(wave)
            completed.update(node.skill_id for node in wave)
            for node in wave:
                remaining.pop(node.skill_id, None)
        return waves

    @staticmethod
    def _node_and_descendants(nodes: list[SkillPlanNode], target_id: str) -> set[str]:
        invalidated = {target_id}
        changed = True
        while changed:
            changed = False
            for node in nodes:
                if node.skill_id not in invalidated and invalidated.intersection(node.depends_on):
                    invalidated.add(node.skill_id)
                    changed = True
        return invalidated

    def _node_runner(self, request: SkillGraphRequest, node: SkillPlanNode):
        def run(state: _SkillGraphState) -> dict[str, Any]:
            raw_states = dict(state.get("node_states") or {})
            prior = SkillNodeState.model_validate(raw_states.get(node.skill_id) or {
                "skill_id": node.skill_id,
                "skill_version": node.skill_version,
                "display_name": node.display_name,
            })
            if prior.status in {SkillNodeStatus.SUCCEEDED, SkillNodeStatus.REUSED}:
                raw_states[node.skill_id] = prior.model_copy(update={
                    "status": SkillNodeStatus.REUSED,
                }).model_dump(mode="json")
                return {
                    "node_states": raw_states,
                    "trace": [{
                        "event": "skill.graph.node.reused",
                        "status": "completed",
                        "summary": f"复用已完成 Skill 节点：{node.display_name}",
                        "skill_id": node.skill_id,
                    }],
                    "graph_path": [f"skill:{node.skill_id}:reused"],
                }
            attempt = prior.attempts + 1
            handoffs = self._handoffs_for(node, state)
            manual_retries = int(dict(state.get("recovery_counts") or {}).get(node.skill_id, 0))
            emit_tool_progress(
                "tool.skill.node.started",
                f"正在准备 Skill 节点：{node.display_name}",
                skill_id=node.skill_id,
                attempt=attempt,
            )
            started = {
                "event": "skill.graph.node.started",
                "status": "active",
                "summary": f"开始 Skill 节点：{node.display_name}",
                "skill_id": node.skill_id,
                "skill_version": node.skill_version,
                "attempt": attempt,
                "manual_retries": manual_retries,
            }
            started_at = time.perf_counter()
            try:
                output = self.executor.execute(SkillNodeExecutionRequest(
                    run_id=request.run_id,
                    conversation_id=request.conversation_id,
                    tenant_id=request.tenant_id,
                    owner_user_id=request.owner_user_id,
                    node=node,
                    attempt=attempt,
                    initial_inputs=request.initial_inputs,
                    handoffs=handoffs,
                ))
                duration_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
                if duration_ms > request.budget.max_node_duration_ms:
                    raise RuntimeError("skill_node_duration_budget_exceeded")
                if (
                    output.estimated_cost_cny is not None
                    and output.estimated_cost_cny > request.budget.max_node_cost_cny
                ):
                    raise RuntimeError("skill_node_cost_budget_exceeded")
            except SkillNodeNeedsInput as exc:
                duration_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
                raw_states[node.skill_id] = SkillNodeState(
                    skill_id=node.skill_id,
                    skill_version=node.skill_version,
                    display_name=node.display_name,
                    status=SkillNodeStatus.WAITING_INPUT,
                    attempts=attempt,
                    error_code="skill_input_required",
                    duration_ms=duration_ms,
                    manual_retries=manual_retries,
                    required_inputs=exc.required_inputs,
                    input_prompt=exc.prompt,
                ).model_dump(mode="json")
                emit_tool_progress(
                    "tool.skill.node.waiting_input",
                    exc.prompt or f"Skill 节点等待补充资料：{node.display_name}",
                    status="paused",
                    skill_id=node.skill_id,
                    required_inputs=exc.required_inputs,
                )
                return {
                    "status": "waiting_input",
                    "failed_node_id": node.skill_id,
                    "node_states": raw_states,
                    "trace": [started, {
                        "event": "skill.graph.node.waiting_input",
                        "status": "paused",
                        "summary": exc.prompt or f"{node.display_name} 等待补充资料",
                        "skill_id": node.skill_id,
                        "attempt": attempt,
                        "required_inputs": exc.required_inputs,
                        "duration_ms": duration_ms,
                    }],
                    "graph_path": [f"skill:{node.skill_id}:waiting_input"],
                }
            except Exception as exc:
                duration_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
                error_code = str(exc)[:300] or type(exc).__name__
                if str(exc).strip():
                    error_code = f"{type(exc).__name__}:{error_code}"
                failed_status = (
                    SkillNodeStatus.EXHAUSTED
                    if attempt >= request.plan.max_loop_rounds
                    else SkillNodeStatus.FAILED
                )
                raw_states[node.skill_id] = SkillNodeState(
                    skill_id=node.skill_id,
                    skill_version=node.skill_version,
                    display_name=node.display_name,
                    status=failed_status,
                    attempts=attempt,
                    error_code=error_code,
                    duration_ms=duration_ms,
                    manual_retries=manual_retries,
                ).model_dump(mode="json")
                emit_tool_progress(
                    "tool.skill.node.failed",
                    f"Skill 节点失败：{node.display_name}",
                    status="failed",
                    skill_id=node.skill_id,
                    attempt=attempt,
                )
                return {
                    "status": "failed" if failed_status == SkillNodeStatus.EXHAUSTED else "running",
                    "failed_node_id": node.skill_id,
                    "node_states": raw_states,
                    "trace": [started, {
                        "event": "skill.graph.node.failed",
                        "status": "failed",
                        "summary": (
                            f"{node.display_name} 执行失败，"
                            + (
                                "已达到节点重试上限"
                                if failed_status == SkillNodeStatus.EXHAUSTED
                                else "仅重试当前节点"
                            )
                        ),
                        "skill_id": node.skill_id,
                        "attempt": attempt,
                        "error_code": error_code,
                        "duration_ms": duration_ms,
                        "manual_retries": manual_retries,
                    }],
                    "graph_path": [f"skill:{node.skill_id}:failed"],
                }
            raw_states[node.skill_id] = SkillNodeState(
                skill_id=node.skill_id,
                skill_version=node.skill_version,
                display_name=node.display_name,
                status=SkillNodeStatus.SUCCEEDED,
                attempts=attempt,
                output=output,
                duration_ms=duration_ms,
                estimated_cost_cny=output.estimated_cost_cny,
                manual_retries=manual_retries,
            ).model_dump(mode="json")
            new_handoffs = [
                SkillHandoff(
                    id=f"handoff_{request.run_id}_{node.skill_id}_{target.skill_id}",
                    source_id=node.skill_id,
                    target_id=target.skill_id,
                    summary=f"{node.display_name} 已完成并交接给 {target.display_name}",
                    output_refs=output.output_refs,
                    artifact_refs=output.artifact_refs,
                    provided_capabilities=node.provides,
                    business_refs={
                        "conversation_id": request.conversation_id,
                        "run_id": request.run_id,
                    },
                    created_at=datetime.now(timezone.utc).isoformat(),
                ).model_dump(mode="json")
                for target in request.plan.nodes
                if node.skill_id in target.depends_on
            ]
            emit_tool_progress(
                "tool.skill.node.completed",
                f"Skill 节点已准备：{node.display_name}",
                skill_id=node.skill_id,
                attempt=attempt,
            )
            trace = [started, {
                "event": "skill.graph.node.completed",
                "status": "completed",
                "summary": f"Skill 节点完成：{node.display_name}",
                "skill_id": node.skill_id,
                "skill_version": node.skill_version,
                "attempt": attempt,
                "output_refs": output.output_refs,
                "duration_ms": duration_ms,
                "estimated_cost_cny": output.estimated_cost_cny,
                "manual_retries": manual_retries,
            }]
            result_ref = output.data.get("workbench_result_ref")
            stage_ref = output.data.get("stage_states_ref")
            if isinstance(result_ref, dict):
                trace.append({
                    "event": "skill.graph.node.result_offloaded",
                    "status": "completed",
                    "summary": "专业 Agent 节点结果已外置，Checkpoint 仅保留校验引用",
                    "skill_id": node.skill_id,
                    "payload_id": str(result_ref.get("payload_id") or ""),
                    "payload_bytes": int(result_ref.get("byte_size") or 0),
                    "stage_payload_id": (
                        str(stage_ref.get("payload_id") or "")
                        if isinstance(stage_ref, dict) else ""
                    ),
                })
            trace.extend({
                "event": "skill.graph.handoff",
                "status": "completed",
                "summary": f"已从 {node.display_name} 形成 Typed Handoff",
                **item,
            } for item in new_handoffs)
            is_last = node.skill_id == request.plan.nodes[-1].skill_id
            return {
                "status": "succeeded" if is_last else "running",
                "failed_node_id": "",
                "node_states": raw_states,
                "handoffs": [*list(state.get("handoffs") or []), *new_handoffs],
                "trace": trace,
                "graph_path": [f"skill:{node.skill_id}"],
            }

        return run

    @staticmethod
    def _route_after_node(skill_id: str, max_attempts: int):
        def route(state: _SkillGraphState) -> str:
            node = SkillNodeState.model_validate(
                dict(state.get("node_states") or {})[skill_id]
            )
            if node.status == SkillNodeStatus.EXHAUSTED:
                return "failed"
            if node.status == SkillNodeStatus.WAITING_INPUT:
                return "paused"
            if node.status == SkillNodeStatus.FAILED and node.attempts < max_attempts:
                return "retry"
            return "next"

        return route

    @staticmethod
    def _handoffs_for(
        node: SkillPlanNode,
        state: _SkillGraphState,
    ) -> list[SkillHandoff]:
        return [
            SkillHandoff.model_validate(item)
            for item in state.get("handoffs") or []
            if SkillHandoff.model_validate(item).targets(
                HandoffEndpointKind.SKILL, node.skill_id,
            )
        ]

    def _to_result(
        self,
        values: dict[str, Any],
        request: SkillGraphRequest,
    ) -> SkillGraphResult:
        node_states = {
            key: SkillNodeState.model_validate(value)
            for key, value in dict(values.get("node_states") or {}).items()
        }
        failed_node_id = str(values.get("failed_node_id") or "")
        return SkillGraphResult(
            status=str(values.get("status") or "failed"),
            node_states=node_states,
            handoffs=[
                SkillHandoff.model_validate(item) for item in values.get("handoffs") or []
            ],
            trace=list(values.get("trace") or []),
            graph_path=list(values.get("graph_path") or []),
            failed_node_id=failed_node_id,
            requires_human_review=bool(values.get("requires_human_review")),
            checkpoint_thread_id=self._thread_id(request),
            required_inputs=(
                list(node_states[failed_node_id].required_inputs)
                if failed_node_id in node_states else []
            ),
            input_prompt=(
                node_states[failed_node_id].input_prompt
                if failed_node_id in node_states else ""
            ),
            total_duration_ms=sum(item.duration_ms for item in node_states.values()),
            total_estimated_cost_cny=sum(
                item.estimated_cost_cny or 0 for item in node_states.values()
            ),
            retry_count=sum(max(0, item.attempts - 1) + item.manual_retries for item in node_states.values()),
            reused_node_count=sum(
                1 for item in node_states.values() if item.status == SkillNodeStatus.REUSED
            ),
        )

    @classmethod
    def _config(cls, request: SkillGraphRequest) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": cls._thread_id(request)}}

    @staticmethod
    def _thread_id(request: SkillGraphRequest) -> str:
        raw = (
            f"{request.tenant_id}|{request.owner_user_id}|{request.run_id}|"
            f"{request.plan_fingerprint}"
        ).encode("utf-8")
        return "unihr-skill-run-" + hashlib.sha256(raw).hexdigest()


def _unique_handoffs(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        parsed = SkillHandoff.model_validate(item)
        key = (parsed.source_id, parsed.target_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
