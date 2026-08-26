from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence

from app.domains.assistant.capability_registry import TaskCapabilityRegistry
from app.domains.assistant.contracts import (
    DeliveryMode,
    ExecutionMode,
    RetrievalMode,
    TurnAction,
    TurnInterpretation,
)
from app.domains.conversations.contracts import AdminFeedbackRecord
from app.domains.runs.contracts import RunEventRecord, RunRecord, RunStatus

from .contracts import (
    CapabilityOperationsCard,
    CapabilityOperationsSnapshot,
    CapabilityOperationsSummary,
    RoutingGoldenCaseResult,
    RoutingGoldenReport,
)


_COMPLETED_STATUSES = {RunStatus.SUCCEEDED, RunStatus.WAITING_REVIEW}


class TurnInterpreter(Protocol):
    def interpret(
        self,
        message: str,
        *,
        task_mode: str = "自动选择",
        attachment_summaries: list[dict[str, Any]] | None = None,
        active_context: str = "",
        has_active_result: bool = False,
    ) -> TurnInterpretation: ...


@dataclass(frozen=True)
class RoutingGoldenCase:
    case_id: str
    title: str
    prompt: str
    expected_capability_id: str
    expected_action: TurnAction | None = None
    expected_retrieval_mode: RetrievalMode | None = None
    expected_execution_mode: ExecutionMode | None = None
    expected_delivery_mode: DeliveryMode | None = None
    active_context: str = ""
    has_active_result: bool = False


class CapabilityOperationsCenter:
    """One deep module for capability contracts, evidence and release checks."""

    def __init__(self, registry: TaskCapabilityRegistry) -> None:
        self.registry = registry

    def snapshot(
        self,
        *,
        runs: Sequence[RunRecord],
        events_by_run: dict[str, list[RunEventRecord]],
        feedback: Sequence[AdminFeedbackRecord],
        window_days: int,
    ) -> CapabilityOperationsSnapshot:
        descriptors = self.registry.descriptors()
        by_intent = {
            item.intent.value: item.capability_id
            for item in descriptors
        }
        run_groups: dict[str, list[RunRecord]] = defaultdict(list)
        for run in runs:
            capability_id = by_intent.get(run.business_intent.value)
            if capability_id:
                run_groups[capability_id].append(run)

        feedback_by_message: dict[str, list[AdminFeedbackRecord]] = defaultdict(list)
        for item in feedback:
            feedback_by_message[item.message_id].append(item)

        cards: list[CapabilityOperationsCard] = []
        all_feedback: list[AdminFeedbackRecord] = []
        total_fallbacks = 0
        total_completed = 0
        for descriptor in descriptors:
            capability_runs = run_groups.get(descriptor.capability_id, [])
            completed = sum(run.status in _COMPLETED_STATUSES for run in capability_runs)
            fallback_count = sum(
                any(event.event == "capability.fallback" for event in events_by_run.get(run.id, []))
                for run in capability_runs
            )
            capability_feedback = [
                item
                for run in capability_runs
                if run.message_id
                for item in feedback_by_message.get(run.message_id, [])
            ]
            all_feedback.extend(capability_feedback)
            helpful = sum(item.helpful for item in capability_feedback)
            quality_issues = sum(len(run.quality_issues) for run in capability_runs)
            completion_rate = completed / len(capability_runs) if capability_runs else None
            helpful_rate = helpful / len(capability_feedback) if capability_feedback else None
            health = self._health(
                run_count=len(capability_runs),
                completion_rate=completion_rate,
                feedback_count=len(capability_feedback),
                helpful_rate=helpful_rate,
                quality_issue_count=quality_issues,
            )
            cards.append(CapabilityOperationsCard(
                **descriptor.operations_card(),
                run_count=len(capability_runs),
                completed_runs=completed,
                completion_rate=completion_rate,
                fallback_runs=fallback_count,
                feedback_count=len(capability_feedback),
                helpful_rate=helpful_rate,
                quality_issue_count=quality_issues,
                health=health,
            ))
            total_completed += completed
            total_fallbacks += fallback_count

        total_runs = len(runs)
        total_feedback = len(all_feedback)
        helpful_total = sum(item.helpful for item in all_feedback)
        return CapabilityOperationsSnapshot(
            generated_at=datetime.now(timezone.utc).isoformat(),
            window_days=window_days,
            summary=CapabilityOperationsSummary(
                registered_capabilities=len(cards),
                active_capabilities=sum(item.lifecycle == "active" for item in cards),
                observed_capabilities=sum(item.run_count > 0 for item in cards),
                total_runs=total_runs,
                completed_runs=total_completed,
                completion_rate=total_completed / total_runs if total_runs else None,
                fallback_runs=total_fallbacks,
                fallback_rate=total_fallbacks / total_runs if total_runs else None,
                feedback_count=total_feedback,
                helpful_rate=helpful_total / total_feedback if total_feedback else None,
                capabilities_needing_attention=sum(item.health == "watch" for item in cards),
            ),
            capabilities=cards,
        )

    def evaluate_routing(
        self,
        interpreter: TurnInterpreter,
        cases: Sequence[RoutingGoldenCase] | None = None,
    ) -> RoutingGoldenReport:
        results: list[RoutingGoldenCaseResult] = []
        for case in cases or default_routing_golden_cases():
            result = interpreter.interpret(
                case.prompt,
                active_context=case.active_context,
                has_active_result=case.has_active_result,
            )
            issues: list[str] = []
            if result.selected_capability_id != case.expected_capability_id:
                issues.append(
                    f"capability:{case.expected_capability_id}!={result.selected_capability_id}"
                )
            if case.expected_action is not None and result.turn_action != case.expected_action:
                issues.append(f"action:{case.expected_action.value}!={result.turn_action.value}")
            if (
                case.expected_retrieval_mode is not None
                and result.retrieval_mode != case.expected_retrieval_mode
            ):
                issues.append(
                    f"retrieval:{case.expected_retrieval_mode.value}!={result.retrieval_mode.value}"
                )
            if (
                case.expected_execution_mode is not None
                and result.execution_mode != case.expected_execution_mode
            ):
                issues.append(
                    f"execution:{case.expected_execution_mode.value}!={result.execution_mode.value}"
                )
            if (
                case.expected_delivery_mode is not None
                and result.delivery_mode != case.expected_delivery_mode
            ):
                issues.append(
                    f"delivery:{case.expected_delivery_mode.value}!={result.delivery_mode.value}"
                )
            results.append(RoutingGoldenCaseResult(
                case_id=case.case_id,
                title=case.title,
                prompt=case.prompt,
                expected_capability_id=case.expected_capability_id,
                actual_capability_id=result.selected_capability_id,
                expected_action=case.expected_action.value if case.expected_action else None,
                actual_action=result.turn_action.value,
                expected_retrieval_mode=(
                    case.expected_retrieval_mode.value if case.expected_retrieval_mode else None
                ),
                actual_retrieval_mode=result.retrieval_mode.value,
                expected_execution_mode=(
                    case.expected_execution_mode.value
                    if case.expected_execution_mode else None
                ),
                execution_mode=result.execution_mode.value,
                expected_delivery_mode=(
                    case.expected_delivery_mode.value
                    if case.expected_delivery_mode else None
                ),
                actual_delivery_mode=result.delivery_mode.value,
                matched_layer=result.matched_layer,
                fallback_used=result.routing_fallback_used,
                passed=not issues,
                issues=issues,
            ))
        passed = sum(item.passed for item in results)
        return RoutingGoldenReport(
            dataset_version="unihr-task-intent-capability-delivery-routing-v2",
            status="passed" if passed == len(results) else "needs_attention",
            total=len(results),
            passed=passed,
            accuracy=passed / len(results) if results else 0.0,
            model_routed_cases=sum(not item.fallback_used for item in results),
            fallback_cases=sum(item.fallback_used for item in results),
            results=results,
        )

    @staticmethod
    def _health(
        *,
        run_count: int,
        completion_rate: float | None,
        feedback_count: int,
        helpful_rate: float | None,
        quality_issue_count: int,
    ) -> str:
        if run_count == 0:
            return "no_data"
        if (
            (run_count >= 3 and completion_rate is not None and completion_rate < 0.8)
            or quality_issue_count > 0
            or (feedback_count >= 2 and helpful_rate is not None and helpful_rate < 0.6)
        ):
            return "watch"
        return "healthy"


def default_routing_golden_cases() -> list[RoutingGoldenCase]:
    """Ordinary prompts: short, odd, contextual and composite, never executor hints."""

    return [
        RoutingGoldenCase(
            "chat-plain-001", "普通寒暄", "谢谢，先这样", "general_conversation",
            expected_retrieval_mode=RetrievalMode.NONE,
            expected_execution_mode=ExecutionMode.NO_OP,
        ),
        RoutingGoldenCase(
            "product-help-001", "产品能力询问", "你现在到底能帮HR干哪些活", "product_help",
            TurnAction.START_NEW, RetrievalMode.NONE,
        ),
        RoutingGoldenCase(
            "weather-odd-001", "缺地点的实时问题", "今天礼拜几，台风是不是很大", "public_information",
            TurnAction.CLARIFY, RetrievalMode.WEB,
        ),
        RoutingGoldenCase(
            "public-topic-isolation-001", "公开信息主题隔离", "晚上好，我想看电影，查查最近有哪些热门大片",
            "public_information", expected_retrieval_mode=RetrievalMode.WEB,
            expected_execution_mode=ExecutionMode.EXECUTE,
            expected_delivery_mode=DeliveryMode.INLINE_RESPONSE,
        ),
        RoutingGoldenCase(
            "policy-plain-001", "口语化政策咨询", "行政管理岗这套薪酬现在怎么定的", "policy_evidence",
            expected_retrieval_mode=RetrievalMode.INTERNAL,
            expected_execution_mode=ExecutionMode.EXECUTE,
        ),
        RoutingGoldenCase(
            "recruitment-web-001", "招聘与联网组合", "请联网搜一下适合行政岗的候选人", "recruitment_screening",
            expected_retrieval_mode=RetrievalMode.WEB,
        ),
        RoutingGoldenCase(
            "document-composite-001", "带内部依据的公文", "按内部政策帮我起草一份调岗通知", "document_generation",
            TurnAction.START_NEW, RetrievalMode.INTERNAL,
            expected_delivery_mode=DeliveryMode.CREATE_ARTIFACT,
        ),
        RoutingGoldenCase(
            "document-inline-polish-001", "正式语气仍是聊天内润色",
            "王老师，这周四周五找专家面试，提醒候选人认真准备，帮我润色一下，要有正式通知的感觉",
            "document_generation", TurnAction.START_NEW, RetrievalMode.NONE,
            expected_execution_mode=ExecutionMode.EXECUTE,
            expected_delivery_mode=DeliveryMode.INLINE_TRANSFORM,
        ),
        RoutingGoldenCase(
            "spreadsheet-odd-001", "口语化表格创建", "给我弄个招聘进度盘子，能自己算完成率", "spreadsheet_processing",
            TurnAction.START_NEW,
        ),
        RoutingGoldenCase(
            "recruitment-followup-001", "已有结果续问", "那就从里面定两位吧", "recruitment_screening",
            TurnAction.COMPARE_EXISTING, RetrievalMode.NONE,
            ExecutionMode.REUSE_RESULT,
            active_context="上一轮已完成6位产业拓展候选人的证据评分和排名。",
            has_active_result=True,
        ),
    ]
