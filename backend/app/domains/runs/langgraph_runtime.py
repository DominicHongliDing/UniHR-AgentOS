from __future__ import annotations

import hashlib
import operator
import os
from typing import Annotated, Any, Callable, Literal, TypedDict

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.domains.assistant.contracts import BusinessIntent, ExecutionMode
from app.observability import tool_progress_scope

from .contracts import (
    OperationPort,
    OperationResult,
    RunPointer,
    RunStatus,
    WorkflowRunRequest,
    WorkflowRunResult,
)
from .payload_store import RunPayloadReference, RunPayloadStore


_MUTATING_TRACE_EVENTS = {
    "recruitment.evaluate",
    "spreadsheet.transform",
    "document.compose",
    "artifact.create",
}

_WAITING_INPUT_EVENTS = {
    "workflow.waiting_input",
    "workflow.batch_limit",
    "workflow.clarification_required",
}

_REQUIRED_ARTIFACT_KINDS = {
    BusinessIntent.RECRUITMENT_SCREENING: "recruitment_report",
    BusinessIntent.DOCUMENT_GENERATION: "document",
    BusinessIntent.SPREADSHEET_PROCESSING: "spreadsheet",
}

_NODE_LIFECYCLE_LABELS: dict[str, tuple[str, str]] = {
    "prepare": ("正在整理任务输入", "任务输入已整理"),
    "execute": ("正在调用对应工具执行任务", "业务工具执行完成"),
    "mutate": ("正在按要求修改已有结果", "已有结果修改完成"),
    "reuse_result": ("正在结合已有结果回答追问", "已有结果已复用"),
    "clarify": ("正在确认任务缺少的信息", "补充信息要求已整理"),
    "no_op": ("正在判断是否需要业务执行", "本轮无需调用业务工具"),
    "quality_gate": ("正在检查回答、文件和 Harness 结果", "质量检查完成"),
    "repair": ("质量门发现可修复问题，正在准备自动修复", "修复反馈已送入下一轮执行"),
    "human_review_gate": ("正在判断是否需要人工确认", "人工确认要求已建立"),
    "await_human_decision": ("正在保存人工确认检查点", "检查点已保存，等待人工确认"),
    "finalize": ("正在整理最终回答和文件", "最终回答和文件已整理"),
}

RunProgressCallback = Callable[[str, str, dict[str, Any]], None]


class WorkflowRunInterrupted(RuntimeError):
    """Cooperative stop at a durable LangGraph node boundary."""

    def __init__(self, checkpoint_thread_id: str) -> None:
        super().__init__("workflow_run_interrupted_at_checkpoint")
        self.checkpoint_thread_id = checkpoint_thread_id


class _RunState(TypedDict, total=False):
    request: dict[str, Any]
    iteration: int
    answer: str
    artifacts: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    result_ref: dict[str, Any]
    status: str
    quality_decision: str
    quality_issues: list[str]
    requires_human_review: bool
    error_code: str | None
    approval_decision: bool | None
    approval_comment: str
    graph_path: Annotated[list[str], operator.add]


class LangGraphWorkflowRuntime:
    """Execute one bounded UniHR Run behind a small, durable interface."""

    def __init__(
        self,
        operation: OperationPort,
        *,
        checkpointer: BaseCheckpointSaver,
        payload_store: RunPayloadStore | None = None,
    ) -> None:
        self.operation = operation
        self.checkpointer = checkpointer
        self.payload_store = payload_store
        self._checkpoint_resource: Any | None = None
        self.graph = self._compile()

    @classmethod
    def with_postgres(
        cls,
        operation: OperationPort,
        dsn: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        payload_store: RunPayloadStore | None = None,
    ) -> "LangGraphWorkflowRuntime":
        """Build the production checkpoint adapter behind the existing Run interface."""
        pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_pool_size,
            max_size=max_pool_size,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=True,
        )
        saver = PostgresSaver(pool)
        saver.setup()
        runtime = cls(operation, checkpointer=saver, payload_store=payload_store)
        runtime._checkpoint_resource = pool
        return runtime

    def close(self) -> None:
        resource = self._checkpoint_resource
        self._checkpoint_resource = None
        if resource is not None:
            resource.close()

    def start(
        self,
        request: WorkflowRunRequest,
        *,
        progress: RunProgressCallback | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> WorkflowRunResult:
        config = self._config(request)
        initial_state: _RunState = {
            "request": request.model_dump(mode="json"),
            "iteration": 0,
            "answer": "",
            "artifacts": [],
            "trace": [],
            "result_ref": {},
            "status": RunStatus.RUNNING.value,
            "quality_decision": "",
            "quality_issues": [],
            "requires_human_review": False,
            "error_code": None,
            "approval_decision": None,
            "approval_comment": "",
            "graph_path": [],
        }
        with tool_progress_scope(progress):
            if progress is None and cancel_requested is None:
                values = self.graph.invoke(initial_state, config=config)
            else:
                for event in self.graph.stream(initial_state, config=config, stream_mode="debug"):
                    if (
                        str(event.get("type") or "") == "task"
                        and cancel_requested is not None
                        and cancel_requested()
                    ):
                        raise WorkflowRunInterrupted(self._thread_id(request))
                    if progress is not None:
                        self._emit_node_lifecycle(event, progress)
                values = dict(self.graph.get_state(config).values)
        return self._to_result(values, self._thread_id(request))

    def restart(
        self,
        request: WorkflowRunRequest,
        *,
        progress: RunProgressCallback | None = None,
    ) -> WorkflowRunResult:
        """Re-run the parent graph after a child Skill checkpoint was recovered."""

        self.checkpointer.delete_thread(self._thread_id(request))
        return self.start(request, progress=progress)

    @staticmethod
    def _emit_node_lifecycle(event: dict[str, Any], progress: RunProgressCallback) -> None:
        """Publish safe Graph facts while keeping state, prompts, and model reasoning private."""

        event_type = str(event.get("type") or "")
        if event_type not in {"task", "task_result"}:
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        node = str(payload.get("name") or "")
        labels = _NODE_LIFECYCLE_LABELS.get(node)
        if labels is None or node.startswith("__"):
            return
        lifecycle = "started" if event_type == "task" else "completed"
        status = "active" if lifecycle == "started" else "completed"
        if event_type == "task_result" and payload.get("error") is not None:
            lifecycle = "failed"
            status = "failed"
        details: dict[str, Any] = {
            "node": node,
            "lifecycle": lifecycle,
            "step": int(event.get("step") or 0),
        }
        if event_type == "task_result":
            result = payload.get("result")
            if isinstance(result, dict):
                if isinstance(result.get("iteration"), int):
                    details["iteration"] = result["iteration"]
                if isinstance(result.get("quality_decision"), str):
                    details["quality_decision"] = result["quality_decision"]
                issues = result.get("quality_issues")
                if isinstance(issues, list):
                    details["quality_issue_count"] = len(issues)
        summary = labels[0] if lifecycle == "started" else labels[1]
        if lifecycle == "failed":
            summary = f"{labels[0]}时发生错误"
        try:
            progress(f"graph.node.{node}.{lifecycle}", summary, {**details, "event_status": status})
        except Exception:
            # Observability must never break the business Run.
            return

    def resume(
        self,
        pointer: RunPointer,
        *,
        approved: bool,
        comment: str = "",
    ) -> WorkflowRunResult:
        values = self.graph.invoke(
            Command(resume={"approved": approved, "comment": comment.strip()[:1_000]}),
            config=self._config(pointer),
        )
        if not values or "request" not in values:
            raise KeyError(pointer.run_id)
        return self._to_result(values, self._thread_id(pointer))

    def get(self, pointer: RunPointer) -> WorkflowRunResult | None:
        snapshot = self.graph.get_state(self._config(pointer))
        values = dict(snapshot.values)
        if not values or "request" not in values:
            return None
        return self._to_result(values, self._thread_id(pointer))

    def get_request(self, pointer: RunPointer) -> WorkflowRunRequest | None:
        snapshot = self.graph.get_state(self._config(pointer))
        values = dict(snapshot.values)
        if not values or "request" not in values:
            return None
        return WorkflowRunRequest.model_validate(values["request"])

    def delete(self, pointer: RunPointer) -> None:
        """Delete every checkpoint for one tenant-scoped Run thread."""
        self.checkpointer.delete_thread(self._thread_id(pointer))
        if self.payload_store is not None:
            self.payload_store.delete(pointer)

    def _compile(self):
        builder = StateGraph(_RunState)
        builder.add_node("prepare", self._prepare)
        builder.add_node("execute", self._execute)
        builder.add_node("mutate", self._mutate)
        builder.add_node("reuse_result", self._reuse_result)
        builder.add_node("clarify", self._clarify)
        builder.add_node("no_op", self._no_op)
        builder.add_node("quality_gate", self._quality_gate)
        builder.add_node("repair", self._repair)
        builder.add_node("human_review_gate", self._human_review_gate)
        builder.add_node("await_human_decision", self._await_human_decision)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "prepare")
        builder.add_conditional_edges(
            "prepare",
            self._route_execution,
            {
                "execute": "execute",
                "mutate": "mutate",
                "reuse_result": "reuse_result",
                "clarify": "clarify",
                "no_op": "no_op",
            },
        )
        for node in ("execute", "mutate", "reuse_result", "clarify", "no_op"):
            builder.add_edge(node, "quality_gate")
        builder.add_conditional_edges(
            "quality_gate",
            self._route_quality,
            {
                "repair": "repair",
                "human_review": "human_review_gate",
                "finalize": "finalize",
            },
        )
        builder.add_conditional_edges(
            "repair",
            self._route_repair,
            {"execute": "execute", "mutate": "mutate"},
        )
        builder.add_edge("human_review_gate", "await_human_decision")
        builder.add_edge("await_human_decision", "finalize")
        builder.add_edge("finalize", END)
        return builder.compile(checkpointer=self.checkpointer, name="unihr-workflow-runtime")

    @staticmethod
    def _prepare(state: _RunState) -> dict[str, Any]:
        return {"status": RunStatus.RUNNING.value, "graph_path": ["prepare"]}

    @staticmethod
    def _route_execution(state: _RunState) -> Literal[
        "execute", "mutate", "reuse_result", "clarify", "no_op"
    ]:
        request = WorkflowRunRequest.model_validate(state["request"])
        return {
            ExecutionMode.EXECUTE: "execute",
            ExecutionMode.MUTATE: "mutate",
            ExecutionMode.REUSE_RESULT: "reuse_result",
            ExecutionMode.CLARIFY: "clarify",
            ExecutionMode.NO_OP: "no_op",
        }[request.execution_mode]

    def _execute(self, state: _RunState) -> dict[str, Any]:
        return self._invoke(state, "execute")

    def _mutate(self, state: _RunState) -> dict[str, Any]:
        return self._invoke(state, "mutate")

    def _reuse_result(self, state: _RunState) -> dict[str, Any]:
        return self._invoke(state, "reuse_result")

    def _clarify(self, state: _RunState) -> dict[str, Any]:
        return self._invoke(state, "clarify")

    def _no_op(self, state: _RunState) -> dict[str, Any]:
        return self._invoke(state, "no_op")

    def _invoke(self, state: _RunState, node_name: str) -> dict[str, Any]:
        request = WorkflowRunRequest.model_validate(state["request"])
        result = self.operation.invoke(request)
        if self.payload_store is not None:
            payload_bytes = len(result.model_dump_json().encode("utf-8"))
            result = result.model_copy(update={
                "trace": [
                    *result.trace,
                    {
                        "event": "graph.state.offloaded",
                        "status": "completed",
                        "summary": f"Graph 中间结果已外置（{payload_bytes} bytes），Checkpoint 仅保留引用",
                        "payload_bytes": payload_bytes,
                    },
                ],
            })
            reference = self.payload_store.put(request, result)
            return {
                "iteration": int(state.get("iteration", 0)) + 1,
                "answer": "",
                "artifacts": [],
                "trace": [],
                "result_ref": reference.model_dump(mode="json"),
                "error_code": result.error_code,
                "graph_path": [node_name],
            }
        return {
            "iteration": int(state.get("iteration", 0)) + 1,
            "answer": result.answer,
            "artifacts": result.artifacts,
            "trace": result.trace,
            "error_code": result.error_code,
            "graph_path": [node_name],
        }

    def _quality_gate(self, state: _RunState) -> dict[str, Any]:
        request = WorkflowRunRequest.model_validate(state["request"])
        operation_result = self._operation_result(state, request)
        issues: list[str] = []
        answer = operation_result.answer.strip()
        artifacts = operation_result.artifacts
        trace = operation_result.trace
        trace_events = {str(item.get("event") or "") for item in trace}
        waiting_for_input = bool(trace_events.intersection(_WAITING_INPUT_EVENTS))
        recruitment_sourcing_only = "recruitment.sourcing.completed" in trace_events
        if not answer:
            issues.append("answer_missing")
        if state.get("error_code"):
            issues.append("operation_error")
        if request.execution_mode == ExecutionMode.REUSE_RESULT:
            if artifacts:
                issues.append("reuse_created_artifact")
            if trace_events.intersection(_MUTATING_TRACE_EVENTS):
                issues.append("reuse_invoked_mutating_capability")
        if (
            request.execution_mode in {ExecutionMode.EXECUTE, ExecutionMode.MUTATE}
            and not waiting_for_input
            and not recruitment_sourcing_only
            and request.business_intent in _REQUIRED_ARTIFACT_KINDS
        ):
            required_kind = _REQUIRED_ARTIFACT_KINDS[request.business_intent]
            if not any(str(item.get("kind") or "") == required_kind for item in artifacts):
                issues.append(f"required_artifact_missing:{required_kind}")
        for item in artifacts:
            harness = dict(item.get("metadata", {}).get("artifact_harness") or {})
            if harness and not bool(harness.get("passed")):
                issues.append(f"artifact_harness_failed:{item.get('kind') or 'artifact'}")
        requires_review = (
            any(
                bool(item.get("metadata", {}).get("human_review_required"))
                or bool(item.get("metadata", {}).get("requires_human_review"))
                for item in artifacts
            )
            or "human_review.gate" in trace_events
        )
        iteration = int(state.get("iteration", 0))
        if waiting_for_input:
            decision = "waiting_input"
            status = RunStatus.WAITING_INPUT.value
            issues = []
        elif any(item.startswith("reuse_") for item in issues):
            decision = "failed"
            status = RunStatus.FAILED.value
        elif issues and iteration < request.max_iterations and request.execution_mode in {
            ExecutionMode.EXECUTE,
            ExecutionMode.MUTATE,
        }:
            decision = "retry"
            status = RunStatus.RUNNING.value
        elif issues:
            decision = "exhausted"
            status = RunStatus.EXHAUSTED.value
        elif requires_review:
            decision = "human_review"
            status = RunStatus.RUNNING.value
        else:
            decision = "passed"
            status = RunStatus.SUCCEEDED.value
        return {
            "quality_decision": decision,
            "quality_issues": issues,
            "requires_human_review": requires_review,
            "status": status,
            "graph_path": ["quality_gate"],
        }

    def _repair(self, state: _RunState) -> dict[str, Any]:
        """Feed bounded quality evidence into the next execution attempt."""

        request = WorkflowRunRequest.model_validate(state["request"])
        operation_result = self._operation_result(state, request)
        artifact_feedback: list[dict[str, Any]] = []
        for artifact in operation_result.artifacts[:6]:
            metadata = dict(artifact.get("metadata") or {})
            artifact_feedback.append({
                "kind": str(artifact.get("kind") or "artifact"),
                "display_name": str(artifact.get("display_name") or ""),
                "operation_plan": metadata.get("operation_plan"),
                "plan_source": metadata.get("plan_source"),
                "validation": metadata.get("validation"),
                "artifact_harness": metadata.get("artifact_harness"),
                "exceptions": list(metadata.get("exceptions") or [])[:20],
            })
        feedback = {
            "quality_issues": list(state.get("quality_issues") or []),
            "previous_answer": operation_result.answer[:1_000],
            "artifacts": artifact_feedback,
        }
        payload = dict(request.payload)
        payload["quality_feedback"] = feedback
        updated_request = request.model_copy(update={"payload": payload})
        return {
            "request": updated_request.model_dump(mode="json"),
            "status": RunStatus.RUNNING.value,
            "graph_path": ["repair"],
        }

    @staticmethod
    def _route_quality(state: _RunState) -> Literal["repair", "human_review", "finalize"]:
        decision = state.get("quality_decision")
        if decision == "retry":
            return "repair"
        if decision == "human_review":
            return "human_review"
        return "finalize"

    @staticmethod
    def _route_repair(state: _RunState) -> Literal["execute", "mutate"]:
        request = WorkflowRunRequest.model_validate(state["request"])
        return "mutate" if request.execution_mode == ExecutionMode.MUTATE else "execute"

    @staticmethod
    def _human_review_gate(state: _RunState) -> dict[str, Any]:
        return {
            "status": RunStatus.WAITING_REVIEW.value,
            "requires_human_review": True,
            "graph_path": ["human_review_gate"],
        }

    def _await_human_decision(self, state: _RunState) -> dict[str, Any]:
        request = WorkflowRunRequest.model_validate(state["request"])
        operation_result = self._operation_result(state, request)
        decision = interrupt({
            "run_id": request.run_id,
            "conversation_id": request.conversation_id,
            "business_intent": request.business_intent.value,
            "question": "请人工复核当前结果后选择同意或退回。",
            "artifact_names": [
                str(item.get("display_name") or item.get("kind") or "artifact")
                for item in operation_result.artifacts
            ],
        })
        payload = decision if isinstance(decision, dict) else {"approved": bool(decision)}
        approved = bool(payload.get("approved"))
        return {
            "status": RunStatus.SUCCEEDED.value if approved else RunStatus.REJECTED.value,
            "approval_decision": approved,
            "approval_comment": str(payload.get("comment") or "")[:1_000],
            "graph_path": ["approval.resume"],
        }

    @staticmethod
    def _finalize(state: _RunState) -> dict[str, Any]:
        status = state.get("status") or RunStatus.FAILED.value
        return {"status": status, "graph_path": ["finalize"]}

    def _to_result(self, values: dict[str, Any], checkpoint_thread_id: str) -> WorkflowRunResult:
        request = WorkflowRunRequest.model_validate(values["request"])
        operation_result = self._operation_result(values, request)
        return WorkflowRunResult(
            run_id=request.run_id,
            conversation_id=request.conversation_id,
            status=RunStatus(values.get("status") or RunStatus.FAILED.value),
            business_intent=request.business_intent,
            turn_action=request.turn_action,
            execution_mode=request.execution_mode,
            iteration=int(values.get("iteration", 0)),
            max_iterations=request.max_iterations,
            answer=operation_result.answer,
            artifacts=operation_result.artifacts,
            trace=operation_result.trace,
            graph_path=list(values.get("graph_path") or []),
            quality_issues=list(values.get("quality_issues") or []),
            requires_human_review=bool(values.get("requires_human_review")),
            error_code=values.get("error_code"),
            approval_decision=values.get("approval_decision"),
            approval_comment=str(values.get("approval_comment") or ""),
            checkpoint_thread_id=checkpoint_thread_id,
        )

    def _operation_result(
        self,
        state: _RunState | dict[str, Any],
        pointer: RunPointer,
    ) -> OperationResult:
        reference = dict(state.get("result_ref") or {})
        if reference:
            if self.payload_store is None:
                raise RuntimeError("run_payload_store_required")
            return self.payload_store.get(pointer, RunPayloadReference.model_validate(reference))
        return OperationResult(
            answer=str(state.get("answer") or ""),
            artifacts=list(state.get("artifacts") or []),
            trace=list(state.get("trace") or []),
            error_code=state.get("error_code"),
        )

    @classmethod
    def _config(cls, pointer: RunPointer) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": cls._thread_id(pointer)}}

    @staticmethod
    def _thread_id(pointer: RunPointer) -> str:
        raw = f"{pointer.tenant_id}|{pointer.owner_user_id}|{pointer.run_id}".encode("utf-8")
        return "unihr-run-" + hashlib.sha256(raw).hexdigest()
