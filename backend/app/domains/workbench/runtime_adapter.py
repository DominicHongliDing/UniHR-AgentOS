from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.domains.assistant.context import PreparedContext
from app.domains.assistant.contracts import TurnInterpretation
from app.domains.artifacts import ArtifactHarness, attach_harness_report
from app.domains.conversations.contracts import AttachmentRecord
from app.domains.identity import Principal
from app.domains.runs import OperationResult, RunPayloadStore, RunPointer, WorkflowRunRequest
from app.domains.skills import (
    ContractSkillNodeExecutor,
    SkillExecutionPlan,
    SkillGraphRequest,
    SkillGraphRuntime,
    SkillNodeExecutionRequest,
    SkillNodeNeedsInput,
    SkillNodeOutput,
)
from app.domains.task_briefs import TaskBrief
from app.models.gateway import ModelGatewayError
from app.observability import emit_tool_progress

from .stage_contracts import compile_professional_stage_states
from .spreadsheet_stage_graph import (
    SpreadsheetStageGraphRequest,
    SpreadsheetStageGraphRuntime,
)


ArtifactCandidate = tuple[str, Path, str, str, dict[str, object]]
WorkbenchExecutor = Callable[..., tuple[str, list[ArtifactCandidate], list[dict[str, object]]]]
ContextLoader = Callable[[dict[str, Any], Principal], PreparedContext]


class WorkbenchOperationAdapter:
    """Translate JSON-safe Run payloads to the existing UniHR operation seam."""

    def __init__(
        self,
        executor: WorkbenchExecutor,
        artifact_harness: ArtifactHarness | None = None,
        context_loader: ContextLoader | None = None,
        skill_graph: SkillGraphRuntime | None = None,
        payload_store: RunPayloadStore | None = None,
        spreadsheet_stage_graph: SpreadsheetStageGraphRuntime | None = None,
    ) -> None:
        self.executor = executor
        self.artifact_harness = artifact_harness or ArtifactHarness()
        self.context_loader = context_loader
        self.skill_graph = skill_graph
        self.payload_store = payload_store
        self.spreadsheet_stage_graph = spreadsheet_stage_graph

    def invoke(self, request: WorkflowRunRequest) -> OperationResult:
        payload = request.payload
        skill_graph_trace: list[dict[str, Any]] = []
        skill_graph_contract: dict[str, Any] = {}
        raw_skill_plan = payload.get("skill_execution_plan")
        if self.skill_graph is not None and isinstance(raw_skill_plan, dict):
            plan = SkillExecutionPlan.model_validate(raw_skill_plan)
            if plan.status == "ready" and plan.nodes:
                graph_result = self.skill_graph.start(SkillGraphRequest(
                    run_id=request.run_id,
                    conversation_id=request.conversation_id,
                    tenant_id=request.tenant_id,
                    owner_user_id=request.owner_user_id,
                    plan=plan,
                    initial_inputs={
                        "message": str(payload.get("message") or ""),
                        "context_snapshot_id": str(
                            dict(payload.get("context") or {}).get("snapshot_id") or ""
                        ),
                        "task_brief_kind": str(
                            dict(payload.get("task_brief") or {}).get("kind") or "general"
                        ),
                        # JSON-safe payload only. Context bodies and artifacts remain external refs.
                        "operation_payload": payload,
                    },
                ))
                skill_graph_trace = graph_result.trace
                skill_graph_contract = {
                    "status": graph_result.status,
                    "checkpoint_thread_id": graph_result.checkpoint_thread_id,
                    "requires_human_review": graph_result.requires_human_review,
                    "node_outputs": {
                        skill_id: state.output.model_dump(mode="json")
                        for skill_id, state in graph_result.node_states.items()
                        if state.status in {"succeeded", "reused"}
                    },
                    "handoffs": [item.model_dump(mode="json") for item in graph_result.handoffs],
                    "reused": graph_result.reused,
                    "failed_node_id": graph_result.failed_node_id,
                    "required_inputs": graph_result.required_inputs,
                    "input_prompt": graph_result.input_prompt,
                    "total_duration_ms": graph_result.total_duration_ms,
                    "total_estimated_cost_cny": graph_result.total_estimated_cost_cny,
                    "retry_count": graph_result.retry_count,
                    "reused_node_count": graph_result.reused_node_count,
                }
                if graph_result.status == "waiting_input":
                    prompt = graph_result.input_prompt or "请补充当前 Skill 节点所需资料后继续。"
                    return OperationResult(
                        answer=prompt,
                        trace=[
                            *skill_graph_trace,
                            {
                                "event": "workflow.waiting_input",
                                "status": "paused",
                                "summary": prompt,
                                "failed_node_id": graph_result.failed_node_id,
                                "required_inputs": graph_result.required_inputs,
                            },
                        ],
                    )
                if graph_result.status != "succeeded":
                    return OperationResult(
                        answer=(
                            "Skill 执行子图未通过节点级质量门，系统已停止后续业务工具。"
                            "已完成节点和中间结果仍保留，可在修复失败节点后继续。"
                        ),
                        trace=skill_graph_trace,
                        error_code="SkillGraphExecutionFailed",
                    )
                node_result = self._business_result_from_graph(
                    graph_result,
                    RunPointer(
                        run_id=request.run_id,
                        tenant_id=request.tenant_id,
                        owner_user_id=request.owner_user_id,
                    ),
                )
                if node_result is not None:
                    return OperationResult(
                        answer=node_result.answer,
                        artifacts=node_result.artifacts,
                        trace=[*skill_graph_trace, *node_result.trace],
                    )
        try:
            answer, artifacts, trace = self._invoke_executor(
                request, payload, skill_graph_contract=skill_graph_contract,
            )
        except ModelGatewayError:
            return OperationResult(error_code="ModelGatewayError")
        inspected_artifacts, harness_traces = self._inspect_artifacts(artifacts)
        return OperationResult(
            answer=answer,
            artifacts=inspected_artifacts,
            trace=[*skill_graph_trace, *trace, *harness_traces],
        )

    def execute_skill_node(self, request: SkillNodeExecutionRequest) -> SkillNodeOutput:
        """Run an existing professional Agent inside one Skill Graph node."""
        raw_payload = request.initial_inputs.get("operation_payload")
        if not isinstance(raw_payload, dict):
            raise RuntimeError("skill_operation_payload_missing")
        payload = dict(raw_payload)
        interpretation = TurnInterpretation.model_validate(payload["interpretation"])
        supported = (
            interpretation.business_intent.value == "document_generation"
            and "document.compose" in request.node.provides
        ) or (
            interpretation.business_intent.value == "spreadsheet_processing"
            and "spreadsheet.transform" in request.node.provides
        ) or (
            interpretation.business_intent.value == "recruitment_screening"
            and "recruitment.evaluate" in request.node.provides
        )
        if not supported:
            return ContractSkillNodeExecutor().execute(request)
        run_request = WorkflowRunRequest(
            run_id=request.run_id,
            conversation_id=request.conversation_id,
            tenant_id=request.tenant_id,
            owner_user_id=request.owner_user_id,
            business_intent=interpretation.business_intent,
            turn_action=interpretation.turn_action,
            execution_mode=interpretation.execution_mode,
            payload=payload,
        )
        stage_graph_result = None
        if self._can_use_spreadsheet_stage_graph(interpretation, payload):
            stage_graph_result = self.spreadsheet_stage_graph.start(SpreadsheetStageGraphRequest(
                run_id=request.run_id,
                conversation_id=request.conversation_id,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                inputs=payload,
            ))
            if stage_graph_result.status != "succeeded":
                raise RuntimeError("spreadsheet_stage_graph_failed")
            raw_result = dict(stage_graph_result.workbench_result)
            answer = str(raw_result.get("answer") or "")
            inspected_artifacts = list(raw_result.get("artifacts") or [])
            full_trace = [
                *stage_graph_result.trace,
                *list(raw_result.get("trace") or []),
            ]
        else:
            answer, artifacts, trace = self._invoke_executor(run_request, payload)
        waiting_events = [
            item for item in (full_trace if stage_graph_result is not None else trace)
            if item.get("status") in {"waiting", "paused"}
            and item.get("event") in {"workflow.waiting_input", "skill.input.required"}
        ]
        if waiting_events:
            waiting = waiting_events[-1]
            required = list(waiting.get("required_inputs") or [])
            raise SkillNodeNeedsInput(
                [str(item) for item in required] or ["business_input"],
                answer or str(waiting.get("summary") or "请补充业务资料后继续。"),
            )
        if stage_graph_result is None:
            inspected_artifacts, harness_traces = self._inspect_artifacts(artifacts)
            full_trace = [*trace, *harness_traces]
        artifact_refs = [str(item.get("path") or "") for item in inspected_artifacts]
        stage_kind = (
            "spreadsheet"
            if interpretation.business_intent.value == "spreadsheet_processing"
            else "recruitment" if interpretation.business_intent.value == "recruitment_screening"
            else "document"
        )
        stage_states = compile_professional_stage_states(
            stage_kind, full_trace, artifact_refs=artifact_refs,
        )
        workbench_result = {
            "answer": answer,
            "artifacts": inspected_artifacts,
            "trace": [
                {
                    "event": "skill.graph.node.business_executed",
                    "status": "completed",
                    "summary": f"Skill 节点已调用专业子 Agent：{request.node.display_name}",
                    "skill_id": request.node.skill_id,
                },
                *full_trace,
                *[
                    {
                        "event": "skill.graph.stage.recorded",
                        "status": item.status.value,
                        "summary": f"{item.display_name}：{item.summary}",
                        "stage_id": item.stage_id,
                        "source_events": item.source_events,
                        "output_refs": item.output_refs,
                        "reusable": item.reusable,
                    }
                    for item in stage_states
                ],
            ],
        }
        result_ref: dict[str, Any] | None = None
        stages_ref: dict[str, Any] | None = None
        if self.payload_store is not None:
            pointer = RunPointer(
                run_id=request.run_id,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
            )
            result_ref = self.payload_store.put_json(
                pointer,
                workbench_result,
                kind=f"skill.node.{request.node.skill_id}.workbench_result",
            ).model_dump(mode="json")
            stages_ref = self.payload_store.put_json(
                pointer,
                [item.model_dump(mode="json") for item in stage_states],
                kind=f"skill.node.{request.node.skill_id}.stage_states",
            ).model_dump(mode="json")
        return SkillNodeOutput(
            output_refs=(
                [f"payload://{result_ref['payload_id']}"]
                if result_ref is not None
                else [f"workbench-result://{request.run_id}/{request.node.skill_id}"]
            ),
            artifact_refs=artifact_refs,
            data={
                "executor_kind": "professional_sub_agent",
                "agent_kind": stage_kind,
                **(
                    {
                        "stage_states_ref": stages_ref,
                        "workbench_result_ref": result_ref,
                    }
                    if result_ref is not None and stages_ref is not None
                    else {
                        "stage_states": [item.model_dump(mode="json") for item in stage_states],
                        "workbench_result": workbench_result,
                    }
                ),
            },
        )

    def _can_use_spreadsheet_stage_graph(
        self,
        interpretation: TurnInterpretation,
        payload: dict[str, Any],
    ) -> bool:
        if self.spreadsheet_stage_graph is None:
            return False
        if interpretation.business_intent.value != "spreadsheet_processing":
            return False
        if interpretation.execution_mode.value not in {"execute", "mutate"}:
            return False
        runtime_contract = dict(payload.get("skill_runtime_contract") or {})
        message = str(payload.get("message") or "")
        if runtime_contract.get("requires_user_file") and not any(
            token in message for token in ("应到", "实到", "计划", "完成", "总计", "合计")
        ):
            return False
        return True

    def _business_result_from_graph(
        self, graph_result: Any, pointer: RunPointer,
    ) -> OperationResult | None:
        for node in reversed(list(graph_result.node_states.values())):
            raw = node.output.data.get("workbench_result")
            if isinstance(raw, dict):
                return OperationResult.model_validate(raw)
            raw_ref = node.output.data.get("workbench_result_ref")
            if isinstance(raw_ref, dict) and self.payload_store is not None:
                return OperationResult.model_validate(
                    self.payload_store.get_json(pointer, raw_ref)
                )
        return None

    def _invoke_executor(
        self,
        request: WorkflowRunRequest,
        payload: dict[str, Any],
        *,
        skill_graph_contract: dict[str, Any] | None = None,
    ) -> tuple[str, list[ArtifactCandidate], list[dict[str, object]]]:
        context_payload = dict(payload["context"])
        interpretation = TurnInterpretation.model_validate(payload["interpretation"])
        principal_payload = dict(payload.get("principal") or {})
        principal = Principal(
            user_id=str(principal_payload.get("user_id") or request.owner_user_id),
            username=str(principal_payload.get("username") or "workflow-user"),
            display_name=str(principal_payload.get("display_name") or "Workflow User"),
            role=str(principal_payload.get("role") or "user"),  # type: ignore[arg-type]
            tenant_id=str(principal_payload.get("tenant_id") or request.tenant_id),
            permissions=list(principal_payload.get("permissions") or []),
        )
        if self.context_loader is not None and context_payload.get("snapshot_id"):
            context = self.context_loader(context_payload, principal)
        else:
            context = PreparedContext(
                messages=list(context_payload.get("messages") or []),
                attachments=[
                    AttachmentRecord.model_validate(item)
                    for item in context_payload.get("attachments") or []
                ],
                attachment_text=str(context_payload.get("attachment_text") or ""),
                usage=list(context_payload.get("usage") or []),
                compact_summary=str(context_payload.get("compact_summary") or ""),
                snapshot_id=str(context_payload.get("snapshot_id") or ""),
                summary_id=str(context_payload.get("summary_id") or ""),
                compaction_applied=bool(context_payload.get("compaction_applied")),
                raw_chars=int(context_payload.get("raw_chars") or 0),
                injected_chars=int(context_payload.get("injected_chars") or 0),
                estimated_tokens=int(context_payload.get("estimated_tokens") or 0),
                fidelity_status=str(context_payload.get("fidelity_status") or "not_required"),
            )
        skill_resource_attachments = [
            AttachmentRecord.model_validate(item)
            for item in payload.get("skill_resource_attachments") or []
        ]
        skill_resource_attachment_text = str(payload.get("skill_resource_attachment_text") or "")
        if skill_resource_attachments or skill_resource_attachment_text:
            context = PreparedContext(
                messages=context.messages,
                attachments=[*context.attachments, *skill_resource_attachments],
                attachment_text="\n\n".join(
                    item for item in [context.attachment_text, skill_resource_attachment_text] if item.strip()
                ),
                usage=context.usage,
                compact_summary=context.compact_summary,
                snapshot_id=context.snapshot_id,
                summary_id=context.summary_id,
                compaction_applied=context.compaction_applied,
                raw_chars=context.raw_chars,
                injected_chars=context.injected_chars + len(skill_resource_attachment_text),
                estimated_tokens=context.estimated_tokens + max(0, len(skill_resource_attachment_text) // 4),
                fidelity_status=context.fidelity_status,
                semantic_facts=context.semantic_facts,
            )
        runtime_contract = dict(payload.get("skill_runtime_contract") or {})
        if skill_graph_contract:
            runtime_contract["execution_graph"] = dict(skill_graph_contract)
        try:
            return self.executor(
                str(payload["message"]),
                interpretation,
                context,
                original_message=str(payload.get("original_message") or payload["message"]),
                skill_prompt=str(payload.get("skill_prompt") or ""),
                skill_runtime_contract=runtime_contract,
                document_font=str(payload.get("document_font") or "宋体"),
                document_font_size=float(payload.get("document_font_size") or 14),
                task_brief=TaskBrief.model_validate(payload.get("task_brief") or {}),
                tenant_id=str(principal_payload.get("tenant_id") or request.tenant_id),
                user_id=str(principal_payload.get("user_id") or request.owner_user_id),
                user_role=str(principal_payload.get("role") or "user"),
                run_id=request.run_id,
                conversation_id=request.conversation_id,
                quality_feedback=dict(payload.get("quality_feedback") or {}),
            )
        except ModelGatewayError:
            raise

    def _inspect_artifacts(
        self,
        artifacts: list[ArtifactCandidate],
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        inspected_artifacts: list[dict[str, object]] = []
        harness_traces: list[dict[str, object]] = []
        for kind, path, display_name, mime_type, metadata in artifacts:
            emit_tool_progress(
                "tool.artifact.harness.started",
                (
                    "正在用真实 Excel 重算公式并渲染预览"
                    if kind in {"spreadsheet", "recruitment_report"}
                    else "正在重新打开并检查生成文件"
                ),
                artifact_kind=kind,
            )
            report = self.artifact_harness.inspect(
                path, kind=kind, mime_type=mime_type, metadata=metadata,
            )
            emit_tool_progress(
                "tool.artifact.harness.completed",
                (
                    (
                        "真实文件重开、重算与渲染校验通过"
                        if kind in {"spreadsheet", "recruitment_report"}
                        else "真实文件重开与预览校验通过"
                    )
                    if report.passed
                    else "生成文件未通过完整校验，质量门将判断是否自动修复"
                ),
                status="completed" if report.passed else "failed",
                artifact_kind=kind,
                harness_status=report.status,
                issue_count=len(report.issues),
                formula_count=report.metrics.get("formula_count_after_recalculation"),
                formula_cache_coverage=report.metrics.get("formula_cache_coverage"),
                rendered_page_count=report.metrics.get("rendered_page_count"),
            )
            inspected_artifacts.append({
                "kind": kind,
                "path": str(path),
                "display_name": display_name,
                "mime_type": mime_type,
                "metadata": attach_harness_report(metadata, report),
            })
            harness_traces.append({
                "event": "artifact.harness.completed",
                "status": report.status,
                "summary": f"实际打开并检查产物 {display_name}",
                "checks": report.checks,
                "metrics": report.metrics,
                "issues": report.issues,
            })
        return inspected_artifacts, harness_traces


class WorkbenchSkillNodeExecutor:
    """Adapter from the Skill node seam to existing UniHR professional Agents."""

    def __init__(self, operation: WorkbenchOperationAdapter) -> None:
        self.operation = operation

    def execute(self, request: SkillNodeExecutionRequest) -> SkillNodeOutput:
        return self.operation.execute_skill_node(request)
