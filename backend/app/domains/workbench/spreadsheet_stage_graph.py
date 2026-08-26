from __future__ import annotations

import hashlib
import json
import operator
from typing import Annotated, Any, Callable, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.domains.runs import RunPayloadStore, RunPointer


SpreadsheetStageExecutor = Callable[
    [str, dict[str, Any], dict[str, Any], RunPointer], dict[str, Any]
]


class SpreadsheetStageGraphRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=120)
    conversation_id: str = Field(min_length=1, max_length=120)
    tenant_id: str = Field(min_length=1, max_length=120)
    owner_user_id: str = Field(min_length=1, max_length=120)
    inputs: dict[str, Any]

    @property
    def pointer(self) -> RunPointer:
        return RunPointer(
            run_id=self.run_id,
            tenant_id=self.tenant_id,
            owner_user_id=self.owner_user_id,
        )

    @property
    def input_fingerprint(self) -> str:
        encoded = json.dumps(
            self.inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class SpreadsheetStageGraphResult(BaseModel):
    status: str
    workbench_result: dict[str, Any]
    stage_refs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    checkpoint_thread_id: str = ""
    reused_stage_count: int = 0


class _SpreadsheetStageState(TypedDict, total=False):
    status: str
    input_fingerprint: str
    request_ref: dict[str, Any]
    stage_refs: dict[str, dict[str, Any]]
    trace: Annotated[list[dict[str, Any]], operator.add]
    reused_stage_count: int


class SpreadsheetStageGraphRuntime:
    """Checkpoint real spreadsheet phases while keeping large state in the payload store."""

    STAGES = ("plan", "execute", "validate", "harness")

    def __init__(
        self,
        *,
        checkpointer: BaseCheckpointSaver,
        payload_store: RunPayloadStore,
        executor: SpreadsheetStageExecutor,
    ) -> None:
        self.checkpointer = checkpointer
        self.payload_store = payload_store
        self.executor = executor

    def start(self, request: SpreadsheetStageGraphRequest) -> SpreadsheetStageGraphResult:
        graph = self._compile(request)
        config = self._config(request)
        existing = dict(graph.get_state(config).values)
        if (
            existing
            and str(existing.get("input_fingerprint") or "") != request.input_fingerprint
        ):
            self.checkpointer.delete_thread(self._thread_id(request))
            graph = self._compile(request)
            existing = {}
        if existing.get("status") == "succeeded":
            return self._result(existing, request, reused=True)

        request_ref = self.payload_store.put_json(
            request.pointer,
            request.inputs,
            kind="professional.spreadsheet.stage.request",
        ).model_dump(mode="json")
        values = dict(graph.invoke(
            {
                "status": "running",
                "input_fingerprint": request.input_fingerprint,
                "request_ref": request_ref,
                "stage_refs": dict(existing.get("stage_refs") or {}),
                "trace": [{
                    "event": "spreadsheet.stage.graph.started",
                    "status": "completed",
                    "summary": "表格专业 Agent 原子阶段图已启动",
                }],
                "reused_stage_count": int(existing.get("reused_stage_count") or 0),
            },
            config=config,
        ))
        return self._result(values, request)

    def _compile(self, request: SpreadsheetStageGraphRequest):
        builder = StateGraph(_SpreadsheetStageState)
        for index, stage_id in enumerate(self.STAGES):
            builder.add_node(stage_id, self._stage_runner(request, stage_id))
            builder.add_edge(stage_id, self.STAGES[index + 1] if index + 1 < len(self.STAGES) else END)
        builder.add_edge(START, self.STAGES[0])
        return builder.compile(
            checkpointer=self.checkpointer,
            name="unihr-spreadsheet-professional-stages",
        )

    def _stage_runner(self, request: SpreadsheetStageGraphRequest, stage_id: str):
        def run(state: _SpreadsheetStageState) -> dict[str, Any]:
            refs = dict(state.get("stage_refs") or {})
            if stage_id in refs:
                return {
                    "stage_refs": refs,
                    "reused_stage_count": int(state.get("reused_stage_count") or 0) + 1,
                    "trace": [{
                        "event": "spreadsheet.stage.reused",
                        "status": "completed",
                        "summary": f"复用已完成表格阶段：{stage_id}",
                        "stage_id": stage_id,
                        "output_ref": refs[stage_id],
                    }],
                }
            inputs = self.payload_store.get_json(request.pointer, state["request_ref"])
            prior = {
                key: self.payload_store.get_json(request.pointer, value)
                for key, value in refs.items()
            }
            output = self.executor(stage_id, dict(inputs), prior, request.pointer)
            output_ref = self.payload_store.put_json(
                request.pointer,
                output,
                kind=f"professional.spreadsheet.stage.{stage_id}",
            ).model_dump(mode="json")
            refs[stage_id] = output_ref
            return {
                "status": "succeeded" if stage_id == "harness" else "running",
                "stage_refs": refs,
                "trace": [
                    *list(output.get("trace") or []),
                    {
                        "event": "spreadsheet.stage.completed",
                        "status": "completed",
                        "summary": f"表格原子阶段已完成并保存：{stage_id}",
                        "stage_id": stage_id,
                        "output_ref": output_ref,
                    },
                ],
            }

        return run

    def _result(
        self,
        values: dict[str, Any],
        request: SpreadsheetStageGraphRequest,
        *,
        reused: bool = False,
    ) -> SpreadsheetStageGraphResult:
        refs = dict(values.get("stage_refs") or {})
        harness = (
            self.payload_store.get_json(request.pointer, refs["harness"])
            if "harness" in refs else {}
        )
        trace = list(values.get("trace") or [])
        if reused:
            trace.append({
                "event": "spreadsheet.stage.graph.reused",
                "status": "completed",
                "summary": "输入未变化，复用已完成的表格原子阶段图",
            })
        return SpreadsheetStageGraphResult(
            status=str(values.get("status") or "failed"),
            workbench_result=dict(harness.get("workbench_result") or {}),
            stage_refs=refs,
            trace=trace,
            checkpoint_thread_id=self._thread_id(request),
            reused_stage_count=(
                len(self.STAGES) if reused else int(values.get("reused_stage_count") or 0)
            ),
        )

    @staticmethod
    def _thread_id(request: SpreadsheetStageGraphRequest) -> str:
        return (
            f"spreadsheet-stage:{request.tenant_id}:{request.owner_user_id}:"
            f"{request.conversation_id}:{request.run_id}"
        )

    @classmethod
    def _config(cls, request: SpreadsheetStageGraphRequest) -> dict[str, Any]:
        return {"configurable": {"thread_id": cls._thread_id(request)}}
