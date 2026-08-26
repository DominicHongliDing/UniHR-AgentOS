from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.models.gateway import ModelGateway, parse_json_object

from .contracts import SkillDraft, SkillSource
from .harness import ALLOWED_SKILL_CAPABILITIES
from .orchestration import ensure_orchestration_metadata


_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SENSITIVE_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?:sk|ak)-[A-Za-z0-9_-]{12,}", re.IGNORECASE),
)


class SkillInputSpec(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=240)
    required: bool = True
    kind: Literal[
        "text", "task_brief", "attachment", "existing_result", "knowledge", "artifact", "confirmation",
    ] = "text"


class SkillExample(BaseModel):
    user_request: str = Field(min_length=1, max_length=300)
    expected_behavior: str = Field(min_length=1, max_length=500)


class SkillResourcePlan(BaseModel):
    kind: Literal["scripts", "references", "assets"]
    name: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=300)
    required: bool = False


class SkillBuildSpec(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=600)
    purpose: str = Field(min_length=1, max_length=1_000)
    trigger_phrases: list[str] = Field(min_length=1, max_length=16)
    capabilities: list[str] = Field(default_factory=list, max_length=16)
    inputs: list[SkillInputSpec] = Field(default_factory=list, max_length=20)
    workflow_steps: list[str] = Field(min_length=2, max_length=30)
    validations: list[str] = Field(min_length=1, max_length=20)
    guardrails: list[str] = Field(min_length=1, max_length=20)
    outputs: list[str] = Field(min_length=1, max_length=20)
    examples: list[SkillExample] = Field(min_length=1, max_length=8)
    non_examples: list[SkillExample] = Field(min_length=1, max_length=8)
    resource_plan: list[SkillResourcePlan] = Field(default_factory=list, max_length=20)
    risk_level: Literal["low", "medium", "high"] = "medium"
    data_classes: list[str] = Field(default_factory=list, max_length=12)
    approval_required: bool = True
    unresolved_questions: list[str] = Field(default_factory=list, max_length=10)

    @field_validator(
        "trigger_phrases", "capabilities", "workflow_steps", "validations",
        "guardrails", "outputs", "data_classes", "unresolved_questions",
    )
    @classmethod
    def clean_string_list(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = " ".join(str(value).split())[:500]
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned


@dataclass(frozen=True)
class SkillBuildResult:
    draft: SkillDraft
    validation: dict[str, Any]


class SkillPackageValidator:
    """Validate an Agent Skill package without executing bundled resources."""

    def validate(
        self,
        *,
        draft: SkillDraft,
        skill_markdown: str,
        openai_yaml: str,
        spec: SkillBuildSpec | None = None,
    ) -> dict[str, Any]:
        checks = {
            "valid_name": bool(_SKILL_NAME.fullmatch(draft.name)) and len(draft.name) <= 64,
            "frontmatter_only_name_description": _frontmatter_keys(skill_markdown) == {"name", "description"},
            "description_has_trigger_context": len(draft.description.strip()) >= 12,
            "has_trigger_phrases": bool(draft.trigger_phrases),
            "has_workflow": bool(draft.instructions.strip()) and (
                len(spec.workflow_steps) >= 2 if spec is not None else "## 工作流程" in draft.instructions
            ),
            "has_validation": bool(spec.validations) if spec is not None else "## 校验" in draft.instructions,
            "has_guardrails": bool(spec.guardrails) if spec is not None else "## 安全边界" in draft.instructions,
            "has_outputs": bool(spec.outputs) if spec is not None else "## 输出" in draft.instructions,
            "has_example": bool(spec.examples) if spec is not None else "## 示例" in draft.instructions,
            "has_non_example": bool(spec.non_examples) if spec is not None else "## 不应触发的示例" in draft.instructions,
            "openai_yaml_mentions_skill": f"${draft.name}" in openai_yaml,
            "progressive_disclosure_size": len(skill_markdown.splitlines()) <= 500,
            "no_obvious_sensitive_literals": not _contains_sensitive_literals(skill_markdown),
        }
        warnings: list[str] = []
        if spec is not None and spec.unresolved_questions:
            warnings.append("仍有待确认问题；启用前应在预览页复核")
        if spec is not None and any(item.required for item in spec.resource_plan):
            warnings.append("存在必需资源计划；上传对应资源前不应视为完整自动化")
        failed = [name for name, passed in checks.items() if not passed]
        blocking_issues = []
        if spec is not None:
            if spec.unresolved_questions:
                blocking_issues.append("unresolved_questions")
            if any(item.required for item in spec.resource_plan):
                blocking_issues.append("required_resources_missing")
        return {
            "passed": not failed,
            "publishable": not failed and not blocking_issues,
            "checks": checks,
            "failed_checks": failed,
            "blocking_issues": blocking_issues,
            "warnings": warnings,
            "score": round(100 * sum(checks.values()) / len(checks)),
        }


class SkillBuilderAgent:
    """Turn a short request or a scrubbed conversation into a complete Agent Skill draft."""

    def __init__(self, gateway: ModelGateway, model: str) -> None:
        self.gateway = gateway
        self.model = model
        self.validator = SkillPackageValidator()

    def build(
        self,
        *,
        task: str,
        material: str,
        preferred_name: str,
        source_type: SkillSource,
        source_conversation_id: str | None = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> SkillBuildResult:
        attempts: list[dict[str, Any]] = []
        last_error = ""
        for attempt in range(1, 3):
            try:
                spec, attempt_trace = self.author_spec(
                    task=task,
                    material=material,
                    preferred_name=preferred_name,
                    feedback=last_error,
                    attempt=attempt,
                )
                attempts.append(attempt_trace)
                result = self.compile_spec(
                    spec,
                    preferred_name=preferred_name,
                    source_type=source_type,
                    source_conversation_id=source_conversation_id,
                    source_metadata=source_metadata or {},
                    attempts=attempts,
                )
                if result.validation["passed"]:
                    return result
                last_error = ",".join(result.validation["failed_checks"])
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:1_000]
        raise ValueError("skill_generation_validation_failed:" + last_error[:300])

    def author_spec(
        self,
        *,
        task: str,
        material: str,
        preferred_name: str,
        feedback: str = "",
        attempt: int = 1,
    ) -> tuple[SkillBuildSpec, dict[str, Any]]:
        schema = SkillBuildSpec.model_json_schema()
        system_prompt = (
            "你是 UniHR Skill Builder Agent。你要把用户描述或一段成功工作对话整理成可复用的 Agent Skill，"
            "而不是只写一段普通提示词。只输出符合 JSON Schema 的单个 JSON 对象。"
            "遵循 Agent Skills/Codex Skill Creator 原则：名称使用小写英文连字符且不超过64字符；"
            "description 同时说明能力和触发场景；正文使用简洁祈使句；根据任务风险选择自由度；"
            "把稳定步骤写入 workflow_steps，把计算、结构、证据和人工复核写入 validations；"
            "把权限、隐私、公平性、原文件保护写入 guardrails；把可复用脚本、参考资料和模板分别规划到"
            "scripts、references、assets。不要写思维链，不要复制姓名、电话、证件号、薪酬、API 密钥或一次性业务结果。"
            "至少给出两个应触发示例、两个不应触发示例及预期行为；明确风险等级、数据类别和是否需要审批。"
            "每个 examples.user_request 必须逐字包含至少一个 trigger_phrases 中的短语；"
            "每个 non_examples.user_request 都不得包含任何 trigger_phrases，以便同一套生产匹配器直接回放。"
            f"capabilities 只能从以下已注册能力中选择，不需要工具时返回空数组：{sorted(ALLOWED_SKILL_CAPABILITIES)}。"
            "信息不足时仍生成草稿。只有会改变 Skill 本身流程、安全边界或输出合同、且无法给出安全默认值的问题，"
            "才写入 unresolved_questions；月份、文件、部门等每次运行时才提供的信息必须写入 inputs，不得阻断 Skill 创建。"
            "scripts、references、assets 都是可选的渐进式资源；只有用户明确要求绑定某个现有资源，或没有该资源就绝对无法执行时，"
            "才能标记 required=true。能够使用已注册 capability 或通用流程完成时必须标记 required=false。"
            f"JSON Schema：{schema}"
        )
        user_prompt = f"任务：{task}\n建议显示名称：{preferred_name}\n材料：\n{material[:40_000]}"
        if feedback:
            user_prompt += (
                "\n\n上一轮没有通过。请针对失败项修复完整蓝图，不得删减必要章节。"
                f"\n失败项：{feedback[:2_000]}"
            )
        reply = self.gateway.complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            purpose="skill_generation" if attempt == 1 else "skill_generation_repair",
            model=self.model,
            structured=True,
            max_tokens=4_800,
        )
        spec = SkillBuildSpec.model_validate(parse_json_object(reply.content))
        return spec, {"attempt": attempt, "model": reply.model, "latency_ms": reply.latency_ms}

    def compile_spec(
        self,
        spec: SkillBuildSpec,
        *,
        preferred_name: str,
        source_type: SkillSource,
        source_conversation_id: str | None,
        source_metadata: dict[str, Any],
        attempts: list[dict[str, Any]],
    ) -> SkillBuildResult:
        name = _slugify(spec.name)
        display_name = (preferred_name.strip() or spec.display_name.strip())[:80]
        instructions = render_skill_body(spec)
        skill_markdown = render_skill_markdown(name, spec.description, display_name, instructions)
        openai_yaml = render_openai_yaml(name, display_name, spec.description)
        draft = SkillDraft(
            name=name,
            display_name=display_name,
            description=spec.description,
            instructions=instructions,
            trigger_phrases=spec.trigger_phrases,
            capabilities=spec.capabilities,
            source_type=source_type,
            source_conversation_id=source_conversation_id,
            enabled=False,
            status="draft",
            metadata={},
        )
        validation = self.validator.validate(
            draft=draft, skill_markdown=skill_markdown, openai_yaml=openai_yaml, spec=spec,
        )
        metadata = {
            **source_metadata,
            "package_format": "agent-skills-v1",
            "skill_md": skill_markdown,
            "openai_yaml": openai_yaml,
            "resource_plan": [item.model_dump(mode="json") for item in spec.resource_plan],
            "unresolved_questions": spec.unresolved_questions,
            "examples": [item.model_dump(mode="json") for item in spec.examples],
            "non_examples": [item.model_dump(mode="json") for item in spec.non_examples],
            "eval_suite": {
                "positive_triggers": [item.user_request for item in spec.examples],
                "negative_triggers": [item.user_request for item in spec.non_examples],
                "status": "generated_not_run",
            },
            "inputs": [item.model_dump(mode="json") for item in spec.inputs],
            "outputs": spec.outputs,
            "risk_level": spec.risk_level,
            "data_classes": spec.data_classes,
            "approval_required": spec.approval_required,
            "validation": validation,
            "builder": {"agent": "unihr-skill-builder", "attempts": attempts},
            "scripts_executed": False,
        }
        metadata = ensure_orchestration_metadata(
            metadata,
            capabilities=draft.capabilities,
            inputs=metadata["inputs"],
            outputs=metadata["outputs"],
        )
        metadata["content_sha256"] = hashlib.sha256(
            (skill_markdown + "\n" + openai_yaml).encode("utf-8")
        ).hexdigest()
        return SkillBuildResult(draft=draft.model_copy(update={"metadata": metadata}), validation=validation)


def render_skill_body(spec: SkillBuildSpec) -> str:
    sections: list[str] = [f"# {spec.display_name}", "", spec.purpose.strip()]
    if spec.inputs:
        sections.extend(["", "## 输入", ""])
        sections.extend(
            f"- {'必须提供' if item.required else '可选提供'} `{item.name}`：{item.description}"
            for item in spec.inputs
        )
    sections.extend(["", "## 工作流程", ""])
    sections.extend(f"{index}. {step}" for index, step in enumerate(spec.workflow_steps, start=1))
    sections.extend(["", "## 校验", ""])
    sections.extend(f"- {item}" for item in spec.validations)
    sections.extend(["", "## 安全边界", ""])
    sections.extend(f"- {item}" for item in spec.guardrails)
    sections.extend(["", "## 输出", ""])
    sections.extend(f"- {item}" for item in spec.outputs)
    sections.extend(["", "## 示例", ""])
    for item in spec.examples:
        sections.extend([f"- 用户：{item.user_request}", f"  - 应执行：{item.expected_behavior}"])
    sections.extend(["", "## 不应触发的示例", ""])
    for item in spec.non_examples:
        sections.extend([f"- 用户：{item.user_request}", f"  - 应处理：{item.expected_behavior}"])
    if spec.resource_plan:
        sections.extend(["", "## 捆绑资源", ""])
        sections.extend(
            f"- `{item.kind}/{item.name}`：{item.purpose}（{'必需' if item.required else '可选'}）"
            for item in spec.resource_plan
        )
    if spec.unresolved_questions:
        sections.extend(["", "## 启用前确认", ""])
        sections.extend(f"- {item}" for item in spec.unresolved_questions)
    return "\n".join(sections).strip()


def render_skill_markdown(name: str, description: str, display_name: str, instructions: str) -> str:
    body = instructions.strip()
    if not body.startswith("#"):
        body = f"# {display_name}\n\n{body}"
    safe_description = " ".join(description.split()).replace('"', "'")
    return f'---\nname: {name}\ndescription: "{safe_description}"\n---\n\n{body}\n'


def render_openai_yaml(name: str, display_name: str, description: str) -> str:
    prompt = f"Use ${name} to complete this workflow with validation and a reviewable output."
    short = " ".join(description.split())[:64]
    return (
        "interface:\n"
        f"  display_name: {json.dumps(display_name, ensure_ascii=False)}\n"
        f"  short_description: {json.dumps(short, ensure_ascii=False)}\n"
        f"  default_prompt: {json.dumps(prompt, ensure_ascii=False)}\n"
        "policy:\n"
        "  allow_implicit_invocation: true\n"
    )


def refresh_package_metadata(draft: SkillDraft, metadata: dict[str, Any]) -> dict[str, Any]:
    if metadata.get("package_format") != "agent-skills-v1":
        return metadata
    metadata = ensure_orchestration_metadata(
        metadata,
        capabilities=draft.capabilities,
        inputs=metadata.get("inputs") if isinstance(metadata.get("inputs"), list) else None,
        outputs=metadata.get("outputs") if isinstance(metadata.get("outputs"), list) else None,
    )
    markdown = render_skill_markdown(draft.name, draft.description, draft.display_name, draft.instructions)
    openai_yaml = render_openai_yaml(draft.name, draft.display_name, draft.description)
    content_sha256 = hashlib.sha256((markdown + "\n" + openai_yaml).encode("utf-8")).hexdigest()
    previous_sha256 = str(metadata.get("content_sha256") or "")
    content_changed = bool(previous_sha256 and previous_sha256 != content_sha256)
    validation = SkillPackageValidator().validate(
        draft=draft, skill_markdown=markdown, openai_yaml=openai_yaml,
    )
    blocking_issues: list[str] = []
    warnings = list(validation.get("warnings") or [])
    if metadata.get("unresolved_questions"):
        blocking_issues.append("unresolved_questions")
        warnings.append("仍有待确认问题；启用前应在预览页复核")
    resources = metadata.get("resource_plan")
    bindings = metadata.get("resource_bindings")
    available = {
        (str(item.get("kind") or "").casefold(), str(item.get("name") or "").casefold())
        for item in bindings or []
        if isinstance(item, dict) and item.get("status") == "ready"
    }
    missing_resources = [
        item for item in resources or []
        if isinstance(item, dict) and item.get("required")
        and (str(item.get("kind") or "").casefold(), str(item.get("name") or "").casefold()) not in available
    ]
    if missing_resources:
        blocking_issues.append("required_resources_missing")
        warnings.append("存在尚未补齐的必需资源；上传同类同名资源后才能启用")
    if any(
        isinstance(item, dict) and item.get("kind") == "scripts" and item.get("status") == "quarantined"
        for item in bindings or []
    ):
        warnings.append("上传脚本已隔离保存且从未执行；需改用受控工具后才能参与自动流程")
    eval_suite = dict(metadata.get("eval_suite") or {})
    authoring = dict(metadata.get("authoring") or {})
    if content_changed:
        blocking_issues.append("harness_not_run")
        warnings.append("内容已修改；必须重新运行 Skill Harness 并人工确认")
        eval_suite.update({"status": "stale", "content_sha256": content_sha256})
        authoring.update({"status": "stale", "resumable": False})
    validation.update({
        "publishable": bool(validation.get("passed")) and not blocking_issues,
        "blocking_issues": blocking_issues,
        "warnings": list(dict.fromkeys(warnings)),
    })
    return {
        **metadata,
        "skill_md": markdown,
        "openai_yaml": openai_yaml,
        "validation": validation,
        "eval_suite": eval_suite,
        "authoring": authoring,
        "content_sha256": content_sha256,
    }


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", value.casefold()).strip("-")
    cleaned = re.sub(r"-{2,}", "-", cleaned)[:64]
    if not cleaned:
        raise ValueError("skill_name_must_use_lowercase_hyphen_case")
    return cleaned


def _frontmatter_keys(markdown: str) -> set[str]:
    if not markdown.startswith("---\n"):
        return set()
    parts = markdown.split("---", 2)
    if len(parts) != 3:
        return set()
    return {
        line.split(":", 1)[0].strip()
        for line in parts[1].splitlines()
        if ":" in line
    }


def _contains_sensitive_literals(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SENSITIVE_PATTERNS)
