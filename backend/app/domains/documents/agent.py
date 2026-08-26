from __future__ import annotations

import json
import operator
import re
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.domains.artifacts.documents import (
    DocumentArtifactBuilder,
    DocumentDraft,
    DraftSection,
    DraftTable,
)
from app.models.gateway import ModelGateway, ModelGatewayError, parse_json_object
from app.domains.runs import RunPayloadStore, RunPointer
from app.observability import emit_tool_progress

from .contracts import (
    DocumentAgentRequest,
    DocumentAgentResult,
    DocumentBrief,
    DocumentFact,
    DocumentPlan,
    DocumentSection,
    DocumentTable,
    DocumentValidationReport,
    EvidenceSource,
)
from .harness import DocumentContentHarness, DocumentLayoutHarness, merge_validation_reports


class _DocumentState(TypedDict, total=False):
    request: dict[str, Any]
    brief: dict[str, Any]
    evidence_sources: list[dict[str, str]]
    evidence_sources_ref: dict[str, Any]
    payload_scope: dict[str, Any]
    plan: dict[str, Any]
    plan_source: str
    artifact_path: str
    artifact_metadata: dict[str, Any]
    content_report: dict[str, Any]
    layout_report: dict[str, Any]
    repair_count: int
    graph_path: Annotated[list[str], operator.add]
    trace: Annotated[list[dict[str, Any]], operator.add]


class DocumentAgent:
    """Deep module for evidence-bound drafting, rendering and targeted repair."""

    def __init__(
        self,
        gateway: ModelGateway,
        builder: DocumentArtifactBuilder,
        *,
        model: str,
        layout_harness: DocumentLayoutHarness | None = None,
        payload_store: RunPayloadStore | None = None,
    ) -> None:
        self.gateway = gateway
        self.builder = builder
        self.model = model
        self.content_harness = DocumentContentHarness()
        self.layout_harness = layout_harness or DocumentLayoutHarness()
        self.payload_store = payload_store
        self.graph = self._build_graph()

    def compose(
        self,
        request: DocumentAgentRequest,
        *,
        payload_scope: RunPointer | None = None,
    ) -> DocumentAgentResult:
        state = self.graph.invoke({
            "request": request.model_dump(mode="json"),
            "repair_count": 0,
            "graph_path": [],
            "trace": [],
            "payload_scope": payload_scope.model_dump(mode="json") if payload_scope else {},
        })
        plan = DocumentPlan.model_validate(state["plan"])
        content = DocumentValidationReport.model_validate(state["content_report"])
        layout = DocumentValidationReport.model_validate(state["layout_report"])
        validation = merge_validation_reports(content, layout)
        artifact_path = Path(state["artifact_path"])
        artifact_metadata = dict(state.get("artifact_metadata") or {})
        preview_files = list(layout.details.get("preview_files") or [])
        hard_review_required = bool(request.task_brief.approval_required)
        metadata: dict[str, Any] = {
            **artifact_metadata,
            "human_review_required": hard_review_required,
            "requires_human_review": hard_review_required,
            "review_recommended": True,
            "review_policy": "approval_required" if hard_review_required else "draft_delivery",
            "document_plan": plan.model_dump(mode="json"),
            "fact_matrix": [fact.model_dump(mode="json") for fact in plan.facts],
            "validation": validation.model_dump(mode="json"),
            "content_validation": content.model_dump(mode="json"),
            "layout_validation": layout.model_dump(mode="json"),
            "preview_files": preview_files,
            "source_files": [source.name for source in request.evidence_sources],
            "task_brief_version": request.task_brief.version,
            "skill_ids": list(request.task_brief.selected_skill_ids),
            "document_graph_path": list(state.get("graph_path") or []),
            "document_plan_source": str(state.get("plan_source") or "model"),
            "repair_count": int(state.get("repair_count") or 0),
        }
        supported = sum(fact.status == "supported" for fact in plan.facts)
        missing = len(validation.missing_facts)
        if validation.passed:
            quality = "已通过内容与版式自动校验"
        else:
            quality = f"自动校验仍有 {len(validation.issues)} 项问题，已明确标注供人工复核"
        answer = (
            f"已生成《{plan.title}》Word 草稿，{quality}。"
            f"事实台账记录 {len(plan.facts)} 项，其中 {supported} 项已核对来源"
            + (f"，{missing} 项仍待补充" if missing else "")
            + "。文件没有自动发送；你可以直接下载继续修改，正式发出前建议确认正文、落款、日期与审批要求。"
        )
        return DocumentAgentResult(
            answer=answer,
            artifact_path=artifact_path,
            display_name=f"{_safe_name(plan.title)}.docx",
            plan=plan,
            validation=validation,
            metadata=metadata,
            trace=list(state.get("trace") or []),
            graph_path=list(state.get("graph_path") or []),
            repair_count=int(state.get("repair_count") or 0),
        )

    def _build_graph(self):
        builder = StateGraph(_DocumentState)
        builder.add_node("prepare", self._prepare)
        builder.add_node("plan", self._plan)
        builder.add_node("fact_gate", self._fact_gate)
        builder.add_node("compose", self._compose)
        builder.add_node("content_harness", self._content_harness)
        builder.add_node("layout_harness", self._layout_harness)
        builder.add_node("quality_decision", self._quality_decision)
        builder.add_node("repair_content", self._repair_content)
        builder.add_node("repair_layout", self._repair_layout)
        builder.add_node("fallback_content", self._fallback_content)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "prepare")
        builder.add_edge("prepare", "plan")
        builder.add_edge("plan", "fact_gate")
        builder.add_edge("fact_gate", "compose")
        builder.add_edge("compose", "content_harness")
        builder.add_edge("content_harness", "layout_harness")
        builder.add_edge("layout_harness", "quality_decision")
        builder.add_conditional_edges(
            "quality_decision",
            self._route_quality,
            {
                "repair_content": "repair_content",
                "repair_layout": "repair_layout",
                "fallback_content": "fallback_content",
                "finalize": "finalize",
            },
        )
        builder.add_edge("repair_content", "fact_gate")
        builder.add_edge("repair_layout", "compose")
        builder.add_edge("fallback_content", "fact_gate")
        builder.add_edge("finalize", END)
        return builder.compile(name="unihr-document-agent")

    def _prepare(self, state: _DocumentState) -> dict[str, Any]:
        emit_tool_progress(
            "tool.document.evidence.started",
            "正在整理需求卡、Skill 和公文依据",
        )
        request = DocumentAgentRequest.model_validate(state["request"])
        brief = DocumentBrief.from_task_brief(request.task_brief, request.message)
        brief = brief.model_copy(update={
            "title": _public_document_title(brief.title, request.message),
            "purpose": _public_requirement_text(brief.purpose),
        })
        brief_contract_values = _brief_contract_values(brief)
        brief_source = EvidenceSource(
            name="用户需求卡",
            text="\n".join([
                request.message,
                brief.title,
                brief.document_type,
                brief.addressee,
                brief.purpose,
                *brief.required_points,
                brief.issuer,
                brief.date_text,
                *brief_contract_values.values(),
            ]),
        )
        sources = [brief_source]
        existing_names = {brief_source.name}
        for source in request.evidence_sources:
            if source.name not in existing_names:
                sources.append(source)
                existing_names.add(source.name)
        storage = self._store_sources(state, sources)
        payload_trace = (
            [_trace(
                "graph.payload.offloaded", "completed",
                "公文证据原文已外置，Graph 仅保留引用",
                kind="document.evidence_sources",
                byte_size=int(dict(storage.get("evidence_sources_ref") or {}).get("byte_size") or 0),
            )]
            if storage.get("evidence_sources_ref") else []
        )
        emit_tool_progress(
            "tool.document.evidence.completed",
            f"公文依据已整理，共 {len(sources)} 个来源",
            status="completed",
            source_count=len(sources),
            evidence_offloaded=bool(storage.get("evidence_sources_ref")),
        )
        return {
            "brief": brief.model_dump(mode="json"),
            **storage,
            "graph_path": ["prepare"],
            "trace": [*payload_trace, _trace("document.prepare", "completed", "已编译公文需求卡、Skill 与证据来源")],
        }

    def _store_sources(
        self, state: _DocumentState, sources: list[EvidenceSource]
    ) -> dict[str, Any]:
        payload = [source.model_dump(mode="json") for source in sources]
        scope = dict(state.get("payload_scope") or {})
        if self.payload_store is None or not scope:
            return {"evidence_sources": payload}
        reference = self.payload_store.put_json(
            RunPointer.model_validate(scope), payload, kind="document.evidence_sources",
        )
        return {"evidence_sources": [], "evidence_sources_ref": reference.model_dump(mode="json")}

    def _sources(self, state: _DocumentState) -> list[EvidenceSource]:
        payload = list(state.get("evidence_sources") or [])
        scope = dict(state.get("payload_scope") or {})
        reference = dict(state.get("evidence_sources_ref") or {})
        if not payload and scope and reference and self.payload_store is not None:
            payload = list(self.payload_store.get_json(RunPointer.model_validate(scope), reference))
        return [EvidenceSource.model_validate(item) for item in payload]

    def _stage_trace(
        self,
        state: _DocumentState,
        stage_id: str,
        payload: dict[str, Any],
        *,
        status: str = "completed",
        summary: str,
    ) -> dict[str, Any]:
        """Persist one real document phase and expose only its compact reference."""
        output_ref: dict[str, Any] = {}
        scope = dict(state.get("payload_scope") or {})
        if self.payload_store is not None and scope:
            reference = self.payload_store.put_json(
                RunPointer.model_validate(scope), payload,
                kind=f"professional.document.stage.{stage_id}",
            )
            output_ref = reference.model_dump(mode="json")
        return _trace(
            "document.stage.completed", status, summary,
            stage_id=stage_id, output_ref=output_ref,
        )

    def _plan(self, state: _DocumentState) -> dict[str, Any]:
        request = DocumentAgentRequest.model_validate(state["request"])
        brief = DocumentBrief.model_validate(state["brief"])
        sources = self._sources(state)
        emit_tool_progress(
            "tool.document.plan.started",
            "正在生成公文结构、正文计划和事实台账",
            source_count=len(sources),
        )
        try:
            plan, model_name, latency_ms = self._generate_plan(request, brief, sources)
            plan_source = "model"
            fallback_error = ""
        except ModelGatewayError as exc:
            # A typed Task Brief is already an executable contract. If the drafting
            # model is temporarily unavailable, build a conservative source-bound
            # draft instead of losing the whole cross-Agent workflow. This is an
            # operational fallback, not an alternative intent router.
            plan = _fallback_plan_from_brief(request, brief, sources)
            model_name = "task_brief_fallback"
            latency_ms = 0
            plan_source = "task_brief_fallback"
            fallback_error = str(exc)
        emit_tool_progress(
            "tool.document.plan.completed",
            f"公文计划已生成，包含 {len(plan.sections)} 个正文部分和 {len(plan.facts)} 项事实",
            status="completed",
            section_count=len(plan.sections),
            fact_count=len(plan.facts),
            latency_ms=latency_ms,
            plan_source=plan_source,
            fallback_error=fallback_error,
        )
        plan_payload = plan.model_dump(mode="json")
        return {
            "plan": plan_payload,
            "plan_source": plan_source,
            "graph_path": ["plan"],
            "trace": [
                _trace(
                    "document.plan.created", "completed", "生成结构化正文计划与事实台账",
                    model=model_name, latency_ms=latency_ms, plan_source=plan_source,
                    fallback_error=fallback_error,
                ),
                self._stage_trace(
                    state, "plan", {"plan": plan_payload, "plan_source": plan_source},
                    summary="已形成公文结构、事实台账与版式计划",
                ),
            ],
        }

    def _fact_gate(self, state: _DocumentState) -> dict[str, Any]:
        brief = DocumentBrief.model_validate(state["brief"])
        plan = DocumentPlan.model_validate(state["plan"])
        sources = self._sources(state)
        emit_tool_progress(
            "tool.document.fact_check.started",
            "正在核对日期、数字、单位和政策表述的来源",
            fact_count=len(plan.facts),
        )
        verified, report = self.content_harness.validate(plan, brief, sources)
        missing_fact_count = len(report.missing_facts)
        emit_tool_progress(
            "tool.document.fact_check.completed",
            (
                f"事实核对完成，{int(report.metrics.get('supported_fact_count') or 0)} 项有来源支持"
                + (f"，{missing_fact_count} 项待补充" if missing_fact_count else "")
                if report.checks.get("fact_evidence_verified")
                else "事实核对发现缺少来源或证据不一致"
            ),
            status="completed" if report.checks.get("fact_evidence_verified") else "failed",
            fact_count=int(report.metrics.get("fact_count") or 0),
            supported_fact_count=int(report.metrics.get("supported_fact_count") or 0),
            missing_fact_count=missing_fact_count,
            issue_count=len(report.issues),
        )
        verified_payload = verified.model_dump(mode="json")
        report_payload = report.model_dump(mode="json")
        fact_status = "completed" if report.checks.get("fact_evidence_verified") else "failed"
        return {
            "plan": verified_payload,
            "graph_path": ["fact_gate"],
            "trace": [
                _trace(
                    "document.fact_gate", fact_status,
                    f"核对 {report.metrics.get('fact_count', 0)} 项事实的来源与证据摘录",
                    issues=report.issues,
                ),
                self._stage_trace(
                    state, "fact_validate",
                    {"plan": verified_payload, "validation": report_payload},
                    status=fact_status,
                    summary="已核对日期、数字、单位、人员与政策表述的来源",
                ),
            ],
        }

    def _compose(self, state: _DocumentState) -> dict[str, Any]:
        request = DocumentAgentRequest.model_validate(state["request"])
        plan = DocumentPlan.model_validate(state["plan"])
        emit_tool_progress(
            "tool.document.compose.started",
            "正在生成 Word 正文并应用标题、字体、段落和页边距",
            section_count=len(plan.sections),
            table_count=len(plan.tables),
        )
        draft = DocumentDraft(
            title=plan.title,
            document_type=plan.document_type,
            addressee=plan.addressee,
            lead=plan.lead,
            sections=[
                DraftSection(heading=section.heading, paragraphs=section.paragraphs)
                for section in plan.sections
            ],
            tables=[
                DraftTable(
                    title=table.title,
                    headers=table.headers,
                    rows=table.rows,
                    blank_rows=table.blank_rows,
                    column_widths_cm=table.column_widths_cm,
                    alignment=table.alignment,
                    style=table.style,
                )
                for table in plan.tables
            ],
            closing=plan.closing,
            issuer=plan.issuer,
            date_text=plan.date_text,
        )
        artifact_path, metadata = self.builder.build(draft, style=request.style)
        emit_tool_progress(
            "tool.document.compose.completed",
            "Word 正文与版式已生成",
            status="completed",
            section_count=len(plan.sections),
            table_count=metadata.get("table_count", len(plan.tables)),
            paragraph_count=metadata.get("paragraph_count"),
        )
        return {
            "artifact_path": str(artifact_path),
            "artifact_metadata": metadata,
            "graph_path": ["compose"],
            "trace": [
                _trace("document.compose", "completed", f"生成 Word：{_safe_name(plan.title)}.docx"),
                self._stage_trace(
                    state, "compose",
                    {
                        "artifact_path": str(artifact_path),
                        "artifact_metadata": metadata,
                        "title": plan.title,
                    },
                    summary="已生成可下载的原生 Word 文档",
                ),
            ],
        }

    def _content_harness(self, state: _DocumentState) -> dict[str, Any]:
        brief = DocumentBrief.model_validate(state["brief"])
        plan = DocumentPlan.model_validate(state["plan"])
        sources = self._sources(state)
        emit_tool_progress(
            "tool.document.content_harness.started",
            "正在检查必写要点、禁写内容和关键事实",
        )
        verified, report = self.content_harness.validate(plan, brief, sources)
        emit_tool_progress(
            "tool.document.content_harness.completed",
            (
                f"公文内容检查完成，仍有 {len(report.missing_facts)} 项事实待补充"
                if report.passed and report.missing_facts
                else "公文内容校验通过"
                if report.passed
                else "公文内容校验发现需要定向修复的问题"
            ),
            status="completed" if report.passed else "failed",
            harness_status=report.status,
            issue_count=len(report.issues),
            warning_count=len(report.warnings),
            missing_fact_count=len(report.missing_facts),
        )
        verified_payload = verified.model_dump(mode="json")
        report_payload = report.model_dump(mode="json")
        harness_status = "completed" if report.passed else "failed"
        return {
            "plan": verified_payload,
            "content_report": report_payload,
            "graph_path": ["content_harness"],
            "trace": [
                _trace(
                    "document.content_harness", harness_status,
                    "完成必写要点、禁写内容、关键值和事实来源校验",
                    checks=report.checks, issues=report.issues, warnings=report.warnings,
                ),
                self._stage_trace(
                    state, "content_harness",
                    {"plan": verified_payload, "validation": report_payload},
                    status=harness_status,
                    summary="已检查必写要点、禁写内容和关键事实",
                ),
            ],
        }

    def _layout_harness(self, state: _DocumentState) -> dict[str, Any]:
        request = DocumentAgentRequest.model_validate(state["request"])
        emit_tool_progress(
            "tool.document.layout_harness.started",
            "正在重新打开 DOCX 并检查纸张、字体、段落和对齐方式",
        )
        report = self.layout_harness.validate(state["artifact_path"], request.style)
        emit_tool_progress(
            "tool.document.layout_harness.completed",
            "Word 版式与渲染校验通过" if report.passed else "Word 版式或渲染需要修复",
            status="completed" if report.passed else "failed",
            harness_status=report.status,
            issue_count=len(report.issues),
            warning_count=len(report.warnings),
            paragraph_count=report.metrics.get("paragraph_count"),
            rendered_page_count=report.metrics.get("rendered_page_count"),
        )
        report_payload = report.model_dump(mode="json")
        harness_status = "completed" if report.passed else "failed"
        return {
            "layout_report": report_payload,
            "graph_path": ["layout_harness"],
            "trace": [
                _trace(
                    "document.layout_harness", harness_status,
                    "完成 DOCX 结构、版式与 Word 渲染校验",
                    checks=report.checks, issues=report.issues, warnings=report.warnings,
                ),
                self._stage_trace(
                    state, "layout_harness",
                    {
                        "artifact_path": str(state["artifact_path"]),
                        "validation": report_payload,
                        "preview_files": list(report.details.get("preview_files") or []),
                    },
                    status=harness_status,
                    summary="已重新打开 DOCX 并完成结构、版式与渲染校验",
                ),
            ],
        }

    @staticmethod
    def _quality_decision(state: _DocumentState) -> dict[str, Any]:
        content = DocumentValidationReport.model_validate(state["content_report"])
        layout = DocumentValidationReport.model_validate(state["layout_report"])
        emit_tool_progress(
            "tool.document.quality.completed",
            (
                "公文内容和版式均已通过质量门"
                if content.passed and layout.passed
                else "质量门已定位问题，正在选择定向修复路径"
            ),
            status="completed" if content.passed and layout.passed else "waiting",
            content_passed=content.passed,
            layout_passed=layout.passed,
            issue_count=len(content.issues) + len(layout.issues),
        )
        return {
            "graph_path": ["quality_decision"],
            "trace": [_trace(
                "document.quality_decision",
                "completed" if content.passed and layout.passed else "repairing",
                "公文质量门已形成定向返修决策",
                content_passed=content.passed, layout_passed=layout.passed,
            )],
        }

    @staticmethod
    def _route_quality(
        state: _DocumentState,
    ) -> Literal["repair_content", "repair_layout", "fallback_content", "finalize"]:
        request = DocumentAgentRequest.model_validate(state["request"])
        repairs = int(state.get("repair_count") or 0)
        content = DocumentValidationReport.model_validate(state["content_report"])
        layout = DocumentValidationReport.model_validate(state["layout_report"])
        if repairs < request.max_repairs and not content.passed:
            return "repair_content"
        if repairs < request.max_repairs and not layout.passed:
            return "repair_layout"
        if (
            not content.passed
            and str(state.get("plan_source") or "model")
            not in {"task_brief_fallback", "task_brief_quality_fallback"}
        ):
            return "fallback_content"
        return "finalize"

    def _repair_content(self, state: _DocumentState) -> dict[str, Any]:
        request = DocumentAgentRequest.model_validate(state["request"])
        brief = DocumentBrief.model_validate(state["brief"])
        sources = self._sources(state)
        current = DocumentPlan.model_validate(state["plan"])
        report = DocumentValidationReport.model_validate(state["content_report"])
        repair_issues = list(report.issues)
        for token in report.details.get("unattributed_critical_values") or []:
            repair_issues.append(f"remove_or_source_unattributed_value:{token}")
        if "required_points_not_covered" in report.issues:
            repair_issues.extend(
                f"cover_locked_required_point:{point}" for point in brief.required_points
            )
        emit_tool_progress(
            "tool.document.repair.started",
            "正在根据质量门反馈定向修订正文",
            repair_type="content",
            issue_count=len(repair_issues),
        )
        plan, model_name, latency_ms = self._generate_plan(
            request, brief, sources, current=current, issues=repair_issues,
        )
        repairs = int(state.get("repair_count") or 0) + 1
        emit_tool_progress(
            "tool.document.repair.completed",
            f"第 {repairs} 次正文定向修订完成，正在重新核验",
            status="completed",
            repair_type="content",
            repair_count=repairs,
            latency_ms=latency_ms,
        )
        return {
            "plan": plan.model_dump(mode="json"),
            "repair_count": repairs,
            "graph_path": ["repair_content"],
            "trace": [_trace(
                "document.repair.content", "completed",
                f"第 {repairs} 次定向修订正文，仅处理上一轮内容问题",
                model=model_name, latency_ms=latency_ms, issues=repair_issues,
            )],
        }

    def _fallback_content(self, state: _DocumentState) -> dict[str, Any]:
        request = DocumentAgentRequest.model_validate(state["request"])
        brief = DocumentBrief.model_validate(state["brief"])
        sources = self._sources(state)
        plan = _fallback_plan_from_brief(request, brief, sources)
        emit_tool_progress(
            "tool.document.fallback.completed",
            "模型定向返修仍未通过事实门，已从锁定需求卡与交接数据重建保守版本",
            status="completed",
            table_count=len(plan.tables),
            fact_count=len(plan.facts),
        )
        return {
            "plan": plan.model_dump(mode="json"),
            "plan_source": "task_brief_quality_fallback",
            "graph_path": ["fallback_content"],
            "trace": [_trace(
                "document.fallback.content",
                "completed",
                "模型返修耗尽后，使用锁定需求卡和原始交接数据重建来源绑定版本",
                table_count=len(plan.tables),
                fact_count=len(plan.facts),
            )],
        }

    @staticmethod
    def _repair_layout(state: _DocumentState) -> dict[str, Any]:
        repairs = int(state.get("repair_count") or 0) + 1
        report = DocumentValidationReport.model_validate(state["layout_report"])
        emit_tool_progress(
            "tool.document.repair.started",
            f"正在按锁定样式进行第 {repairs} 次版式重建",
            repair_type="layout",
            repair_count=repairs,
            issue_count=len(report.issues),
        )
        return {
            "repair_count": repairs,
            "graph_path": ["repair_layout"],
            "trace": [_trace(
                "document.repair.layout", "completed",
                f"第 {repairs} 次按锁定样式重建并重新渲染",
                issues=report.issues,
            )],
        }

    def _finalize(self, state: _DocumentState) -> dict[str, Any]:
        emit_tool_progress(
            "tool.document.finalize.completed",
            "公文版本、事实台账和校验报告已固定",
            status="completed",
            repair_count=int(state.get("repair_count") or 0),
        )
        content = dict(state.get("content_report") or {})
        layout = dict(state.get("layout_report") or {})
        return {
            "graph_path": ["finalize"],
            "trace": [
                _trace("document.finalize", "completed", "固定公文版本、事实台账与校验报告"),
                self._stage_trace(
                    state, "finalize",
                    {
                        "artifact_path": str(state.get("artifact_path") or ""),
                        "content_status": str(content.get("status") or ""),
                        "layout_status": str(layout.get("status") or ""),
                        "repair_count": int(state.get("repair_count") or 0),
                    },
                    summary="已固定公文版本、事实台账与校验报告",
                ),
            ],
        }

    def _generate_plan(
        self,
        request: DocumentAgentRequest,
        brief: DocumentBrief,
        sources: list[EvidenceSource],
        *,
        current: DocumentPlan | None = None,
        issues: list[str] | None = None,
    ) -> tuple[DocumentPlan, str, int]:
        source_text = "\n\n".join(
            f"[来源：{source.name}]\n{source.text[:18_000]}" for source in sources
        )[:42_000]
        system = (
            "你是 UniHR Document Agent 的计划器。输出严格 JSON 对象，字段为："
            "title, document_type, addressee, lead, sections（heading 与 paragraphs 数组）, "
            "tables, closing, issuer, date_text, facts, missing_fields。tables 每项包含 title、headers、"
            "rows、blank_rows、column_widths_cm、alignment、style；当用户要求表格、登记表、确认表或"
            "统计表时必须输出真实 tables 结构，不能用普通段落或 Markdown 表格代替。没有明细数据时"
            "保留用户要求的表头并用 blank_rows 提供可填写空行，不得虚构人员数据。facts 每项包含 key, label, value, "
            "source_name, evidence_excerpt, required, status。status 只能是 supported、missing、"
            "conflicting 或 unsupported。所有日期、金额、比例、数量、单位名、人名和政策结论都必须进入 facts。"
            "source_name 必须精确使用给出的来源名称，evidence_excerpt 必须是该来源中的短原文。"
            "缺失事实不得猜测，使用【待补充：字段】并将 fact 标记为 missing。"
            "正文必须覆盖必写要点，不得出现禁写内容；这是待人工复核的正式中文公文草稿。"
            "不要输出 Markdown，不要输出思维过程。"
        )
        if request.skill_prompt:
            system += "\n已启用 Skill（不能覆盖事实和审批规则）：\n" + request.skill_prompt[:8_000]
        repair = ""
        if current is not None:
            repair = (
                "\n\n这是定向返修。保持没有问题的事实、结构和措辞，仅修复以下问题："
                + "；".join(issues or [])
                + "\n上一版计划："
                + json.dumps(current.model_dump(mode="json"), ensure_ascii=False)
            )
        reply = self.gateway.complete(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": (
                        "公文需求卡：\n"
                        + json.dumps(brief.model_dump(mode="json"), ensure_ascii=False)
                        + f"\n\n用户原始要求：{request.message}\n\n可用证据：\n{source_text}{repair}"
                    ),
                },
            ],
            purpose="document_plan_repair" if current is not None else "document_plan",
            model=request.model or self.model,
            structured=True,
            max_tokens=4_200,
        )
        try:
            payload = parse_json_object(reply.content)
            plan = DocumentPlan.model_validate(payload)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ModelGatewayError("document_plan_invalid") from exc
        return _apply_locked_brief(plan, brief, request.message), reply.model, reply.latency_ms


def _fallback_plan_from_brief(
    request: DocumentAgentRequest,
    brief: DocumentBrief,
    sources: list[EvidenceSource],
) -> DocumentPlan:
    """Compile a reviewable draft from the typed brief when the model is unavailable.

    The fallback deliberately performs no open-ended writing. It preserves locked
    fields, copies rows only from Markdown supplied in the handoff/evidence, and
    exposes blank review fields rather than inventing missing facts.
    """
    evidence_text = "\n\n".join(source.text for source in sources if source.text.strip())
    markdown_tables = _extract_markdown_tables("\n\n".join([request.message, evidence_text]))
    tables: list[DocumentTable] = []
    facts: list[DocumentFact] = []
    if brief.requires_table or brief.required_table_headers:
        required_headers = list(brief.required_table_headers) or (
            markdown_tables[0][0] if markdown_tables else ["事项", "内容", "人工意见"]
        )
        matched = _best_markdown_table(required_headers, markdown_tables)
        rows: list[list[str]] = []
        if matched is not None:
            source_headers, source_rows = matched
            positions = {header: index for index, header in enumerate(source_headers)}
            rows = [
                [row[positions[header]] if header in positions and positions[header] < len(row) else ""
                 for header in required_headers]
                for row in source_rows
            ]
            for row_index, (row, source_row) in enumerate(zip(rows, source_rows), start=1):
                evidence_excerpt = "| " + " | ".join(source_row) + " |"
                source_name = _source_name_for_excerpt(sources, evidence_excerpt)
                for column_index, (header, value) in enumerate(zip(required_headers, row), start=1):
                    if not value.strip():
                        continue
                    facts.append(DocumentFact(
                        key=f"table_1_row_{row_index}_column_{column_index}",
                        label=f"{row[0] if row else row_index}·{header}",
                        value=value,
                        source_name=source_name,
                        evidence_excerpt=evidence_excerpt,
                        required=True,
                        status="supported",
                    ))
        tables.append(DocumentTable(
            title=next(
                (point for point in brief.required_points if "表" in point),
                "信息确认表",
            ),
            headers=required_headers,
            rows=rows,
            blank_rows=0 if rows else max(3, brief.required_blank_rows),
            alignment="center",
            style="table_grid",
        ))

    sections: list[DocumentSection] = []
    for point in brief.required_points:
        if "表" in point and tables:
            paragraph = "相关信息详见下表，请逐项核验并填写人工意见。"
        elif "签字" in point:
            paragraph = "面试记录：____________________    领导签字：________    日期：________"
        else:
            paragraph = f"{point}。请依据现有材料和现场情况据实核验。"
        sections.append(DocumentSection(heading=point, paragraphs=[paragraph]))
    if not sections:
        sections = [DocumentSection(
            heading="有关事项",
            paragraphs=[brief.purpose or request.message],
        )]

    purpose = (brief.purpose or request.message).strip().rstrip("。；;")
    lead = f"为{purpose}，现形成以下材料，供人工复核。" if purpose else "现形成以下材料，供人工复核。"
    for key, label, value in (
        ("issuer", "落款单位", brief.issuer),
        ("date_text", "成文日期", brief.date_text),
    ):
        if value.strip():
            facts.append(DocumentFact(
                key=key,
                label=label,
                value=value,
                source_name=_source_name_for_excerpt(sources, value),
                evidence_excerpt=value,
                required=True,
                status="supported",
            ))
    return _apply_locked_brief(DocumentPlan(
        title=brief.title or _fallback_title(request.message),
        document_type=brief.document_type,
        addressee=brief.addressee,
        lead=lead,
        sections=sections,
        tables=tables,
        closing="本材料仅用于辅助人工复核，不构成自动录用或淘汰决定。",
        issuer=brief.issuer,
        date_text=brief.date_text,
        facts=facts,
        missing_fields=[],
    ), brief, request.message)


def _source_name_for_excerpt(sources: list[EvidenceSource], excerpt: str) -> str:
    normalized_excerpt = " ".join(excerpt.split())
    for source in sources:
        if normalized_excerpt and normalized_excerpt in " ".join(source.text.split()):
            return source.name
    return sources[0].name if sources else "用户需求卡"


def _extract_markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    tables: list[tuple[list[str], list[list[str]]]] = []
    block: list[list[str]] = []

    def flush() -> None:
        nonlocal block
        if len(block) >= 2 and _is_markdown_separator(block[1]):
            headers = block[0]
            rows = [row for row in block[2:] if not _is_markdown_separator(row)]
            tables.append((headers, rows))
        block = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("|") and line.endswith("|"):
            block.append([cell.strip() for cell in line.strip("|").split("|")])
        else:
            flush()
    flush()
    return tables


def _is_markdown_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _best_markdown_table(
    required_headers: list[str],
    tables: list[tuple[list[str], list[list[str]]]],
) -> tuple[list[str], list[list[str]]] | None:
    if not tables:
        return None
    required = set(required_headers)
    return max(tables, key=lambda item: len(required.intersection(item[0])))


def _apply_locked_brief(
    plan: DocumentPlan,
    brief: DocumentBrief,
    message: str,
) -> DocumentPlan:
    title = _public_document_title(brief.title or plan.title, message)
    values: dict[str, str] = {
        "title": title,
        "document_type": brief.document_type or plan.document_type,
        "addressee": brief.addressee or plan.addressee,
        "issuer": brief.issuer or plan.issuer,
        "date_text": brief.date_text or plan.date_text,
    }
    labels = {
        "title": "标题",
        "document_type": "文种",
        "addressee": "受文对象",
        "issuer": "落款单位",
        "date_text": "成文日期",
    }
    missing_fields = list(plan.missing_fields)
    for field in brief.required_fields:
        if field in values and not values[field].strip():
            values[field] = f"【待补充：{labels.get(field, field)}】"
            if field not in missing_fields:
                missing_fields.append(field)
    tables = list(plan.tables)
    if brief.required_table_headers:
        current = tables[0] if tables else DocumentTable(headers=list(brief.required_table_headers))
        positions = {header: index for index, header in enumerate(current.headers)}
        rows = [
            [
                row[positions[header]] if header in positions and positions[header] < len(row) else ""
                for header in brief.required_table_headers
            ]
            for row in current.rows
        ]
        locked_table = current.model_copy(update={
            "headers": list(brief.required_table_headers),
            "rows": rows,
            "blank_rows": max(current.blank_rows, brief.required_blank_rows),
            "column_widths_cm": (
                current.column_widths_cm
                if len(current.column_widths_cm) == len(brief.required_table_headers)
                else []
            ),
        })
        tables = [locked_table, *tables[1:]]
    normalized = plan.model_copy(update={
        **values, "tables": tables, "missing_fields": missing_fields,
    })
    return normalized.model_copy(update={
        "facts": _merge_brief_contract_facts(normalized, brief),
    })


def _fallback_title(message: str) -> str:
    return _public_document_title("", message)


def _public_document_title(value: str, message: str = "") -> str:
    """Return a user-facing title without orchestration or action-language leakage."""
    # An explicitly labelled/quoted title is a locked business field.  Recover it
    # from the original user message before consulting a model-rewritten Task Brief,
    # because rewrites may shorten names or alter digits in otherwise valid titles.
    explicit = _explicit_document_title(message)
    if explicit:
        return explicit[:80]
    candidate = _public_requirement_text(value)
    if not candidate:
        source = str(message or "")
        action_match = re.search(
            r"(?:做|生成|创建|制作|起草|撰写)(?:一份|一个)?\s*"
            r"([^，。；;\n]{2,60}?(?:通知|确认单|方案|报告|纪要|材料|公函|函|请示|决定|公告|表))",
            source,
            flags=re.IGNORECASE,
        )
        if action_match:
            candidate = action_match.group(1)
        else:
            lines = [
                line.strip() for line in source.splitlines()
                if line.strip() and not _is_internal_context_header(line)
            ]
            candidate = lines[0] if lines else ""
            candidate = re.sub(r"^(?:任务目标|目标|本轮动作)\s*[：:]\s*", "", candidate)
    candidate = _extract_document_subject(candidate)
    candidate = candidate.strip(" ，。；：,.!！?？-—")
    return candidate[:80] or "未命名公文"


def _explicit_document_title(message: str) -> str:
    source = str(message or "")
    labelled = re.search(
        r"(?:标题|题目|题名|文件名)(?:为|是)?\s*[：:]?\s*"
        r"[《“\"]([^》”\"\n]{2,160})[》”\"]",
        source,
        flags=re.IGNORECASE,
    )
    if labelled:
        return labelled.group(1).strip(" ，。；：,.!！?？-—")
    plain = re.search(
        r"(?:标题|题目|题名|文件名)(?:为|是)\s*[：:]?\s*"
        r"([^，。；;\n]{2,160}?(?:通知|确认单|方案|报告|纪要|材料|公函|请示|决定|公告|名单|审批单|登记表|统计表|汇总表))",
        source,
        flags=re.IGNORECASE,
    )
    return plain.group(1).strip(" ，。；：,.!！?？-—") if plain else ""


def _extract_document_subject(value: str) -> str:
    """Separate a document subject from conversational actions and delivery clauses.

    Task Brief titles may be copied from a user's full sentence.  The public title
    must keep the business subject while dropping instructions such as “帮我写一份”
    and “生成 Word”.  This is deliberately based on document grammar, not on a
    particular date, department, or acceptance prompt.
    """
    candidate = str(value or "").strip()
    candidate = re.sub(
        r"^(?:(?:请|麻烦)?\s*帮我|我想要|我需要|需要|请|麻烦)?\s*"
        r"(?:做|写|编写|拟写|生成|创建|制作|起草|草拟|撰写)(?:一份|一个)?\s*",
        "",
        candidate,
        flags=re.IGNORECASE,
    )

    # Remove explicit delivery instructions even when they follow the subject.
    candidate = re.sub(
        r"(?:[，,；;]\s*)?(?:并|再|然后)?\s*"
        r"(?:生成|输出|导出|保存为|制作成)?\s*"
        r"(?:Word|DOCX)(?:\s*(?:文档|草稿|文件))?\s*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    )

    # Once a well-known document noun closes the subject, a comma normally starts
    # content/style instructions rather than the title itself.
    document_noun = re.search(
        r"(?:通知|确认单|方案|报告|纪要|材料|公函|请示|决定|公告|名单|审批单|登记表|统计表|汇总表)",
        candidate,
        flags=re.IGNORECASE,
    )
    if document_noun:
        tail = candidate[document_noun.end():]
        if re.match(r"^\s*[，,；;：:]", tail):
            candidate = candidate[:document_noun.end()]

    return candidate.strip()


def _public_requirement_text(value: str) -> str:
    cleaned = re.sub(
        r"[\[【][^\]】]*(?:上下文理解|本轮动作|task\s*brief|用户锁定)[^\]】]*[\]】]",
        "",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    lines = [
        line.strip() for line in cleaned.splitlines()
        if line.strip() and not _is_internal_context_header(line)
    ]
    return " ".join(lines).strip()


def _is_internal_context_header(value: str) -> bool:
    compact = "".join(str(value).split()).casefold()
    return bool(
        re.fullmatch(r"[\[【].*(?:上下文理解|本轮动作|taskbrief|用户锁定).*[\]】]", compact)
    )


def _brief_contract_values(brief: DocumentBrief) -> dict[str, str]:
    values: dict[str, str] = {}
    purpose = _public_document_title(brief.purpose) if brief.purpose else ""
    if purpose:
        values["document_purpose"] = purpose
    if brief.required_table_headers:
        values["table_headers"] = "、".join(brief.required_table_headers)
    if brief.required_points:
        values["required_points"] = "；".join(brief.required_points)
    if brief.tone:
        values["tone"] = brief.tone
    return values


def _merge_brief_contract_facts(plan: DocumentPlan, brief: DocumentBrief) -> list[DocumentFact]:
    """Make explicit user requirements visible in the fact ledger on every path."""
    facts = list(plan.facts)
    existing_keys = {fact.key for fact in facts if fact.key}
    labels = {
        "document_purpose": "文档用途",
        "table_headers": "表格包含字段",
        "required_points": "签署与必写要素",
        "tone": "语气风格",
    }
    for key, value in _brief_contract_values(brief).items():
        if key in existing_keys:
            continue
        facts.append(DocumentFact(
            key=key,
            label=labels[key],
            value=value,
            source_name="用户需求卡",
            evidence_excerpt=value,
            required=True,
            status="supported",
        ))
        existing_keys.add(key)

    locked_values = {
        "issuer": brief.issuer,
        "date_text": brief.date_text,
    }
    missing_labels = {
        "issuer": "发文单位",
        "date_text": "发文日期",
    }
    for key in brief.required_fields:
        if key not in locked_values or locked_values[key].strip() or key in existing_keys:
            continue
        facts.append(DocumentFact(
            key=key,
            label=missing_labels[key],
            value="",
            source_name="用户需求卡",
            evidence_excerpt="",
            required=True,
            status="missing",
        ))
        existing_keys.add(key)

    has_rows = any(any(cell.strip() for cell in row) for table in plan.tables for row in table.rows)
    if (brief.requires_table or brief.required_table_headers) and not has_rows and "table_data" not in existing_keys:
        facts.append(DocumentFact(
            key="table_data",
            label="具体人员数据",
            value="",
            source_name="用户需求卡",
            evidence_excerpt="",
            required=True,
            status="missing",
        ))
    return facts


def _safe_name(value: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "-", value).strip().strip(".")
    return (name or "公文草稿")[:80]


def _trace(event: str, status: str, summary: str, **details: Any) -> dict[str, Any]:
    return {"event": event, "status": status, "summary": summary, **details}
