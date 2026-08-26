from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, Field


class AgentStageStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    WAITING = "waiting"
    FAILED = "failed"


class AgentStageState(BaseModel):
    """A checkpoint-safe summary of one real professional-Agent phase."""

    stage_id: str
    display_name: str
    status: AgentStageStatus
    source_events: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    summary: str = ""
    reusable: bool = True


_SPREADSHEET_STAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "inspect",
        "读取与理解输入",
        ("artifact.inspected", "tool.spreadsheet.inspect.completed"),
    ),
    (
        "plan",
        "形成受限操作计划",
        (
            "spreadsheet.plan.compiled", "model.spreadsheet_plan",
            "spreadsheet.plan.repaired", "spreadsheet.repair.plan_created",
            "artifact.plan.created", "tool.spreadsheet.plan.completed",
        ),
    ),
    (
        "execute",
        "执行工作簿操作",
        (
            "spreadsheet.create", "spreadsheet.transform",
            "tool.spreadsheet.build.completed",
        ),
    ),
    (
        "validate",
        "校验结构与公式",
        ("artifact.validation.completed",),
    ),
    (
        "harness",
        "真实重算与渲染",
        ("artifact.harness.completed", "tool.artifact.harness.completed"),
    ),
)

_DOCUMENT_STAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("plan", "规划公文与事实台账", ("document.stage.completed",)),
    ("fact_validate", "核对事实与来源", ("document.stage.completed",)),
    ("compose", "生成原生 Word", ("document.stage.completed",)),
    ("content_harness", "校验内容与约束", ("document.stage.completed",)),
    ("layout_harness", "校验版式与渲染", ("document.stage.completed",)),
    ("finalize", "固定交付版本", ("document.stage.completed",)),
)

_RECRUITMENT_STAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("prepare", "读取候选人材料", ("recruitment.stage.completed",)),
    ("deduplicate", "识别重复材料", ("recruitment.stage.completed",)),
    ("version_resolve", "归并简历版本", ("recruitment.stage.completed",)),
    ("rubric_lock", "锁定岗位评分标准", ("recruitment.stage.completed",)),
    ("batch_map", "分批证据评分", ("recruitment.stage.completed",)),
    ("reduce", "合并稳定排名", ("recruitment.stage.completed",)),
    ("fairness_guard", "执行公平性检查", ("recruitment.stage.completed",)),
    ("evidence_localize", "定位原文证据", ("recruitment.stage.completed",)),
    ("harness", "校验覆盖与稳定性", ("recruitment.stage.completed",)),
    ("quality_gate", "执行招聘质量门", ("recruitment.stage.completed",)),
    ("repair", "定向修复异常结果", ("recruitment.stage.completed",)),
    ("artifact_create", "生成复核报告", ("recruitment.stage.completed",)),
    ("finalize", "固定报告与复核边界", ("recruitment.stage.completed",)),
)


def compile_professional_stage_states(
    agent_kind: str,
    trace: Iterable[dict[str, Any]],
    *,
    artifact_refs: Iterable[str] = (),
) -> list[AgentStageState]:
    """Normalize verbose tool events behind one small, checkpoint-safe interface.

    This is intentionally a compiler, not a second execution engine.  Every
    completed stage must be backed by an event emitted by real code/tool work.
    It never invents a completed box merely because the requested Agent is a
    spreadsheet Agent.
    """
    if agent_kind == "spreadsheet":
        stage_contract = _SPREADSHEET_STAGES
    elif agent_kind == "document":
        stage_contract = _DOCUMENT_STAGES
    elif agent_kind == "recruitment":
        stage_contract = _RECRUITMENT_STAGES
    else:
        return []
    events = [dict(item) for item in trace]
    stages: list[AgentStageState] = []
    for stage_id, display_name, accepted_events in stage_contract:
        evidence = [
            item for item in events
            if str(item.get("event") or "") in accepted_events
            and (
                agent_kind not in {"document", "recruitment"}
                or str(item.get("stage_id") or "") == stage_id
            )
        ]
        if not evidence:
            continue
        last = evidence[-1]
        raw_status = str(last.get("status") or "completed")
        status = (
            AgentStageStatus.FAILED if raw_status == "failed"
            else AgentStageStatus.WAITING if raw_status in {"waiting", "paused"}
            else AgentStageStatus.COMPLETED
        )
        refs = _stage_output_refs(stage_id, evidence, artifact_refs)
        stages.append(AgentStageState(
            stage_id=stage_id,
            display_name=display_name,
            status=status,
            source_events=list(dict.fromkeys(str(item.get("event") or "") for item in evidence)),
            output_refs=refs,
            summary=str(last.get("summary") or display_name)[:500],
            reusable=status == AgentStageStatus.COMPLETED,
        ))
    return stages


def _stage_output_refs(
    stage_id: str,
    evidence: list[dict[str, Any]],
    artifact_refs: Iterable[str],
) -> list[str]:
    refs: list[str] = []
    for item in evidence:
        for key in ("inspection_payload_ref", "validation_payload_ref", "output_ref"):
            raw = item.get(key)
            if isinstance(raw, dict) and raw.get("path"):
                refs.append(str(raw["path"]))
            elif isinstance(raw, dict) and raw.get("payload_id"):
                refs.append(f"payload://{raw['payload_id']}")
            elif isinstance(raw, dict) and raw.get("id"):
                refs.append(f"payload://{raw['id']}")
    if stage_id in {
        "execute", "harness", "compose", "layout_harness", "artifact_create", "finalize",
    }:
        refs.extend(str(item) for item in artifact_refs if str(item).strip())
    return list(dict.fromkeys(refs))
