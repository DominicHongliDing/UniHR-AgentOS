from __future__ import annotations

import json
import re
from typing import Any

from app.domains.assistant.contracts import BusinessIntent, TurnInterpretation
from app.domains.task_briefs import TaskBrief
from app.models.gateway import ModelGateway, ModelGatewayError

from .contracts import WorkflowAgent, WorkflowPlan, WorkflowStep


_RECRUITMENT_HINTS = ("简历", "候选人", "招聘", "面试", "人选", "这些人", "这批人")
_DOCUMENT_HINTS = (
    "word", "文档", "材料", "资料", "确认单", "面试", "面试题", "面试准备", "通知", "公文", "领导确认",
)
_SPREADSHEET_HINTS = ("excel", "表格", "汇总表", "台账", "名单", "评分表")
_PROCESS_HINTS = (
    "然后", "再", "后面", "一并", "一起", "全流程", "走完", "我确认", "我点头",
    "我来定", "人工确认", "通过后", "接着",
)
_DELIVERY_HINTS = ("接着办", "弄好", "准备好", "后续材料", "后续资料", "交付", "产物")


class WorkflowPlanner:
    """Turn a broad goal into a small governed graph without exposing chain-of-thought."""

    def __init__(self, gateway: ModelGateway, model: str) -> None:
        self.gateway = gateway
        self.model = model

    def should_plan(
        self, message: str, task_brief: TaskBrief, interpretation: TurnInterpretation,
    ) -> bool:
        """Deterministic fallback only; never the primary semantic gate."""

        if interpretation.business_intent != BusinessIntent.RECRUITMENT_SCREENING:
            return False
        return self._fallback_should_plan(message, task_brief)

    def plan_if_needed(
        self, message: str, task_brief: TaskBrief, interpretation: TurnInterpretation,
    ) -> WorkflowPlan | None:
        """Ask the model about task shape before consulting lexical fallback.

        Unknown phrasing therefore still reaches semantic planning.  The term
        matcher is used only when the configured routing model is unavailable
        or violates the response contract.
        """

        if interpretation.business_intent != BusinessIntent.RECRUITMENT_SCREENING:
            return None
        try:
            return self._model_plan(message, task_brief, interpretation)
        except (ModelGatewayError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            if not self._fallback_should_plan(message, task_brief):
                return None
            return self._fallback(message, task_brief)

    @staticmethod
    def _fallback_should_plan(message: str, task_brief: TaskBrief) -> bool:
        text = " ".join([
            message, task_brief.objective, *task_brief.constraints, *task_brief.deliverables,
            json.dumps(task_brief.mode_spec, ensure_ascii=False),
        ]).casefold()
        has_recruitment = any(term in text for term in _RECRUITMENT_HINTS)
        downstream_domains = sum((
            any(term in text for term in _DOCUMENT_HINTS),
            any(term in text for term in _SPREADSHEET_HINTS),
        ))
        has_process = any(term in text for term in _PROCESS_HINTS)
        has_delivery = any(term in text for term in _DELIVERY_HINTS)
        explicit_cross_agent_goal = downstream_domains > 0 and (has_process or downstream_domains > 1)
        implicit_cross_agent_goal = has_process and has_delivery
        return has_recruitment and (explicit_cross_agent_goal or implicit_cross_agent_goal)

    def plan(
        self, message: str, task_brief: TaskBrief, interpretation: TurnInterpretation,
    ) -> WorkflowPlan:
        try:
            result = self._model_plan(message, task_brief, interpretation)
            if result is None:
                raise ValueError("planner_returned_no_downstream_agent")
            return result
        except (ModelGatewayError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return self._fallback(message, task_brief)

    def _model_plan(
        self, message: str, task_brief: TaskBrief, interpretation: TurnInterpretation,
    ) -> WorkflowPlan | None:
        reply = self.gateway.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "你是 UniHR 的任务形态规划器。只输出 JSON，不输出思维过程。"
                        "根据完整语义判断这是一项单 Agent 招聘筛选，还是需要在人工确认后继续调用"
                        "其他 Agent 的多步骤业务目标。不要依赖固定关键词；理解口语、省略和同义表达。"
                        "当前可交付的下游能力是：document（Word 面试/确认材料）和 spreadsheet"
                        "（Excel 汇总、名单或台账）。不要执行任务，不要补造人员事实。"
                        "返回字段：is_multi_agent、title、objective、needs_document、"
                        "needs_spreadsheet、rationale。rationale 只写一行可展示的规划依据。"
                        "如果用户只要求筛选、比较或解释候选人，is_multi_agent 必须为 false；"
                        "只有用户表达了后续交付目标时才为 true。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "message": message,
                        "task_brief": task_brief.model_dump(mode="json"),
                        "resolved_request": interpretation.resolved_request,
                        "available_downstream_capabilities": [
                            {
                                "id": "document",
                                "description": "生成可复核 Word 面试、确认或沟通材料",
                            },
                            {
                                "id": "spreadsheet",
                                "description": "生成可复核 Excel 名单、汇总或跟进台账",
                            },
                        ],
                    }, ensure_ascii=False),
                },
            ],
            purpose="workflow_planning", model=self.model, structured=True, max_tokens=800,
        )
        raw = self._json_object(reply.content)
        needs_document = bool(raw.get("needs_document"))
        needs_spreadsheet = bool(raw.get("needs_spreadsheet"))
        is_multi_agent = bool(raw.get(
            "is_multi_agent", needs_document or needs_spreadsheet,
        ))
        if not is_multi_agent or (not needs_document and not needs_spreadsheet):
            return None
        return self._governed_plan(
            title=str(raw.get("title") or "招聘协同任务"),
            objective=str(raw.get("objective") or task_brief.objective or message),
            rationale=str(raw.get("rationale") or "根据目标拆分招聘、人工确认和后续材料交付。"),
            needs_document=needs_document,
            needs_spreadsheet=needs_spreadsheet,
            source=f"model:{reply.model}",
        )

    def _fallback(self, message: str, task_brief: TaskBrief) -> WorkflowPlan:
        text = " ".join([message, task_brief.objective, *task_brief.deliverables]).casefold()
        needs_document = any(term in text for term in _DOCUMENT_HINTS)
        needs_spreadsheet = any(term in text for term in _SPREADSHEET_HINTS)
        # “材料”在招聘全流程中通常至少包括一份可复核汇总；缺省补齐结构化交付，
        # 但绝不补造候选人事实，事实仍来自招聘 Case 的人工确认快照。
        if needs_document and not needs_spreadsheet and "材料" in text:
            needs_spreadsheet = True
        return self._governed_plan(
            title="招聘筛选与面试材料协同任务",
            objective=task_brief.objective or message,
            rationale="识别到招聘筛选、人工确认和后续材料属于同一业务目标。",
            needs_document=needs_document or not needs_spreadsheet,
            needs_spreadsheet=needs_spreadsheet or not needs_document,
            source="governed_fallback",
        )

    @staticmethod
    def _governed_plan(
        *, title: str, objective: str, rationale: str,
        needs_document: bool, needs_spreadsheet: bool, source: str,
    ) -> WorkflowPlan:
        steps = [
            WorkflowStep(
                id="screen_candidates", sequence=1, title="证据化筛选候选人",
                agent=WorkflowAgent.RECRUITMENT,
                instruction="读取本轮候选人材料，按岗位相关证据评分并形成可复核建议。",
            ),
            WorkflowStep(
                id="confirm_shortlist", sequence=2, title="人工确认入围人选",
                agent=WorkflowAgent.HUMAN_REVIEW,
                instruction="由招聘人员核验证据，并确认采用、修改或退回候选人建议。",
                depends_on=["screen_candidates"], requires_human_review=True,
            ),
        ]
        parents = ["confirm_shortlist"]
        if needs_document:
            steps.append(WorkflowStep(
                id="prepare_interview_document", sequence=len(steps) + 1,
                title="生成面试与确认材料", agent=WorkflowAgent.DOCUMENT,
                instruction="基于人工确认的候选人快照生成 Word 材料，不补造事实。",
                depends_on=parents,
                required_artifact_kinds=["document"],
            ))
        if needs_spreadsheet:
            steps.append(WorkflowStep(
                id="prepare_candidate_workbook", sequence=len(steps) + 1,
                title="生成候选人汇总表", agent=WorkflowAgent.SPREADSHEET,
                instruction="基于同一人工确认快照生成 Excel 汇总与复核字段。",
                depends_on=parents,
                required_artifact_kinds=["spreadsheet"],
            ))
        return WorkflowPlan(
            title=title.strip()[:160] or "招聘协同任务",
            objective=objective.strip()[:4_000] or "完成招聘筛选和后续材料交付",
            rationale=rationale.strip()[:2_000], source=source, steps=steps,
        )

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        text = content.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if fenced:
            text = fenced.group(1)
        elif not text.startswith("{"):
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise ValueError("planner_json_missing")
            text = match.group(0)
        value = json.loads(text)
        if not isinstance(value, dict):
            raise TypeError("planner_json_must_be_object")
        return value
