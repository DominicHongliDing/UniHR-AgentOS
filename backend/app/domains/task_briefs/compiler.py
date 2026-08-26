from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import TaskBrief, TaskKind


_MODE_KIND = {
    "自动选择": TaskKind.GENERAL,
    "简历筛选": TaskKind.RECRUITMENT,
    "政策问答": TaskKind.POLICY,
    "表格处理": TaskKind.SPREADSHEET,
    "公文材料": TaskKind.DOCUMENT,
    "图片理解": TaskKind.IMAGE,
}

_MODE_DEFAULT_OBJECTIVE = {
    TaskKind.RECRUITMENT: "按岗位要求批量筛选上传的简历，并生成证据化人工复核建议。",
    TaskKind.POLICY: "基于指定知识范围回答政策问题，并给出可定位的原文证据。",
    TaskKind.SPREADSHEET: "处理上传的表格，或根据纯文字要求从零创建可复核工作簿。",
    TaskKind.DOCUMENT: "根据要求生成可复核的 Word 文档。",
    TaskKind.IMAGE: "读取并分析上传的图片材料。",
    TaskKind.GENERAL: "处理本轮用户请求。",
}

_FIELD_LABELS = {
    "job_title": "岗位名称",
    "must_haves": "必须条件",
    "nice_to_haves": "加分项",
    "selection_count": "期望人数",
    "topic": "政策主题",
    "scope": "适用对象/范围",
    "as_of_date": "政策时间点",
    "knowledge_scope": "知识库范围",
    "answer_depth": "回答深度",
    "goal": "处理目标",
    "source_sheet": "源工作表",
    "header_row": "表头行",
    "mapping_notes": "字段映射",
    "calculation_rules": "计算规则",
    "filter_sort_rules": "筛选与排序规则",
    "data_validation_rules": "数据校验规则",
    "summary_rules": "汇总规则",
    "chart_type": "图表类型",
    "target_year": "目标年度",
    "rule_notes": "规则说明",
    "document_type": "文种",
    "title": "标题",
    "addressee": "主送/参会对象",
    "issuer": "落款单位",
    "date_text": "成文日期",
    "tone": "语气",
}


@dataclass(frozen=True)
class CompiledTaskRequest:
    original_message: str
    display_message: str
    execution_message: str
    task_brief: TaskBrief
    locked_constraints: list[str]


class TaskBriefCompiler:
    """Validate rich task input and expose one stable request to downstream modules."""

    def compile(
        self,
        *,
        message: str,
        task_mode: str,
        task_brief: TaskBrief | dict[str, Any] | None,
    ) -> CompiledTaskRequest:
        clean_message = message.strip()
        expected_kind = _MODE_KIND.get(task_mode, TaskKind.GENERAL)
        brief = (
            task_brief
            if isinstance(task_brief, TaskBrief)
            else TaskBrief.model_validate(task_brief or {"kind": expected_kind})
        )
        if expected_kind != TaskKind.GENERAL and brief.kind not in {expected_kind, TaskKind.GENERAL}:
            raise ValueError("task_brief_mode_mismatch")
        if brief.kind == TaskKind.GENERAL and expected_kind != TaskKind.GENERAL:
            brief = brief.model_copy(update={"kind": expected_kind})

        objective = brief.objective or clean_message or _MODE_DEFAULT_OBJECTIVE[brief.kind]
        brief = brief.model_copy(update={"objective": objective})
        if not clean_message and not brief.mode_spec and objective == _MODE_DEFAULT_OBJECTIVE[brief.kind]:
            raise ValueError("message_or_task_brief_required")

        detail_lines = self._mode_spec_lines(brief.mode_spec)
        locked = [*brief.constraints, *detail_lines]
        summary_lines = [f"任务目标：{objective}"]
        if detail_lines:
            summary_lines.append("任务要求：")
            summary_lines.extend(f"- {item}" for item in detail_lines)
        if brief.constraints:
            summary_lines.append("锁定约束：")
            summary_lines.extend(f"- {item}" for item in brief.constraints)
        if brief.deliverables:
            summary_lines.append("期望产物：" + "、".join(brief.deliverables))
        if clean_message and clean_message != objective:
            summary_lines.append(f"用户补充：{clean_message}")
        display_message = "\n".join(summary_lines)
        execution_message = (
            f"[Task Brief v{brief.version} | {brief.kind.value}]\n{display_message}\n"
            "以上‘任务要求’与‘锁定约束’均来自用户输入；后续改写只能澄清表达，不得删除或降低其优先级。"
        )
        return CompiledTaskRequest(
            original_message=clean_message,
            display_message=display_message,
            execution_message=execution_message,
            task_brief=brief,
            locked_constraints=locked,
        )

    @staticmethod
    def _mode_spec_lines(mode_spec: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        for key, value in mode_spec.items():
            if value is None or value == "" or value == []:
                continue
            label = _FIELD_LABELS.get(key, key.replace("_", " "))
            if isinstance(value, list):
                rendered = "；".join(str(item).strip() for item in value if str(item).strip())
            elif isinstance(value, bool):
                rendered = "是" if value else "否"
            else:
                rendered = str(value).strip()
            if rendered:
                lines.append(f"{label}：{rendered[:2_000]}")
        return lines
