from __future__ import annotations

from .contracts import CapabilitySmokeCase


def default_capability_smoke_cases() -> list[CapabilitySmokeCase]:
    """Release smoke prompts deliberately written at ordinary HR-user detail level."""
    return [
        CapabilitySmokeCase(
            id="policy-simple-001", capability="policy",
            prompt="这个博士后管理办法现在还能直接按吗？", task_mode="自动选择",
            expected_route="policy_question",
            required_graph_nodes=["prepare", "execute", "quality_gate"],
            required_trace_events=["query.rewritten"],
        ),
        CapabilitySmokeCase(
            id="document-simple-001", capability="document",
            prompt="帮我写个8月12日下午开会的通知", task_mode="自动选择",
            expected_route="document_generation",
            required_graph_nodes=["prepare", "execute", "quality_gate"],
            required_trace_events=["document.compose"],
            required_artifact_extensions=[".docx"], harness_required=True,
        ),
        CapabilitySmokeCase(
            id="spreadsheet-simple-001", capability="spreadsheet",
            prompt="帮我做一张招聘进度表，要有完成率", task_mode="自动选择",
            expected_route="spreadsheet_processing",
            required_graph_nodes=["prepare", "execute", "quality_gate"],
            required_trace_events=["spreadsheet.create"],
            required_artifact_extensions=[".xlsx"], harness_required=True,
        ),
        CapabilitySmokeCase(
            id="recruitment-simple-001", capability="recruitment",
            prompt="帮我从这些产业拓展实习简历里选两个人面试", task_mode="自动选择",
            expected_route="recruitment_screening",
            required_graph_nodes=["prepare", "execute", "quality_gate"],
            required_trace_events=["recruitment.evaluate"],
            required_artifact_extensions=[".xlsx"], harness_required=True,
        ),
        CapabilitySmokeCase(
            id="public-info-simple-001", capability="public_info",
            prompt="帮我查一下杭州今天的天气", task_mode="自动选择",
            expected_route="public_current_info",
            required_graph_nodes=["prepare", "execute", "quality_gate"],
            required_trace_events=["weather.current"],
        ),
        CapabilitySmokeCase(
            id="skill-simple-001", capability="skill",
            prompt="做一个每月汇总招聘进度的Skill", task_mode="Skill 创建",
            expected_route="skill_create",
            required_graph_nodes=["author", "compile", "static_validate", "harness", "human_review_gate"],
            required_trace_events=["skill.draft.created"],
            harness_required=True,
        ),
    ]
