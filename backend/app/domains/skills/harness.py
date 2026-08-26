from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, Field

from .contracts import SkillDraft
from .matching import score_skill_match
from .orchestration import SkillManifest


ALLOWED_SKILL_CAPABILITIES = {
    "artifact.create",
    "document.compose",
    "evidence.answer",
    "file.extract",
    "image.analyze",
    "knowledge.retrieve",
    "recruitment.evaluate",
    "skill.generate",
    "skill.publish",
    "skill.validate",
    "spreadsheet.inspect",
    "spreadsheet.transform",
    "weather.current",
    "web.search",
}

_REVIEW_REQUIRED_CAPABILITIES = {
    "artifact.create",
    "document.compose",
    "recruitment.evaluate",
    "skill.publish",
    "spreadsheet.transform",
}

_SENSITIVE_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?:sk|ak)-[A-Za-z0-9_-]{12,}", re.IGNORECASE),
)

_PROMPT_OVERRIDE_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous", re.IGNORECASE),
    re.compile(r"忽略(?:以上|此前|系统|安全|权限)"),
    re.compile(r"绕过(?:权限|审批|安全|规则)"),
)


class SkillHarnessReport(BaseModel):
    status: str
    passed: bool
    score: int = Field(ge=0, le=100)
    checks: dict[str, bool]
    failed_checks: list[str]
    positive_results: list[dict[str, Any]]
    negative_results: list[dict[str, Any]]
    warnings: list[str]
    content_sha256: str


class SkillHarness:
    """Deterministic dry-run harness; it never executes uploaded scripts or external tools."""

    def evaluate(self, draft: SkillDraft, metadata: dict[str, Any]) -> SkillHarnessReport:
        positive_requests = _requests(metadata, "examples", "positive_triggers")
        negative_requests = _requests(metadata, "non_examples", "negative_triggers")
        positive_results = [_trigger_result(draft, request, expected=True) for request in positive_requests]
        negative_results = [_trigger_result(draft, request, expected=False) for request in negative_requests]
        markdown = str(metadata.get("skill_md") or draft.instructions)
        openai_yaml = str(metadata.get("openai_yaml") or "")
        declared = set(draft.capabilities)
        high_risk = bool(declared.intersection(_REVIEW_REQUIRED_CAPABILITIES))
        approval_required = bool(metadata.get("approval_required", True))
        expected_hash = hashlib.sha256((markdown + "\n" + openai_yaml).encode("utf-8")).hexdigest()
        manifest = metadata.get("orchestration")
        try:
            parsed_manifest = SkillManifest.model_validate(manifest)
            orchestration_valid = set(draft.capabilities).issubset(set(parsed_manifest.provides))
        except Exception:
            orchestration_valid = False
        checks = {
            "positive_trigger_replay": bool(positive_results) and all(item["passed"] for item in positive_results),
            "negative_trigger_replay": bool(negative_results) and all(item["passed"] for item in negative_results),
            "capabilities_allowlisted": declared.issubset(ALLOWED_SKILL_CAPABILITIES),
            "high_risk_requires_review": not high_risk or approval_required,
            "no_sensitive_literals": not any(pattern.search(markdown) for pattern in _SENSITIVE_PATTERNS),
            "no_prompt_override": not any(pattern.search(markdown) for pattern in _PROMPT_OVERRIDE_PATTERNS),
            "uploaded_scripts_not_executed": not bool(metadata.get("scripts_executed")),
            "content_hash_bound": metadata.get("content_sha256") in {None, "", expected_hash},
            "orchestration_contract_valid": orchestration_valid,
        }
        failed = [name for name, passed in checks.items() if not passed]
        warnings: list[str] = []
        unsupported = sorted(declared.difference(ALLOWED_SKILL_CAPABILITIES))
        if unsupported:
            warnings.append("未注册能力：" + "、".join(unsupported))
        return SkillHarnessReport(
            status="passed" if not failed else "failed",
            passed=not failed,
            score=round(100 * sum(checks.values()) / len(checks)),
            checks=checks,
            failed_checks=failed,
            positive_results=positive_results,
            negative_results=negative_results,
            warnings=warnings,
            content_sha256=expected_hash,
        )


def merge_harness_metadata(
    metadata: dict[str, Any], report: SkillHarnessReport,
) -> dict[str, Any]:
    validation = dict(metadata.get("validation") or {})
    package_checks = dict(validation.get("checks") or {})
    combined_checks = {
        **package_checks,
        **{f"harness.{name}": passed for name, passed in report.checks.items()},
    }
    blocking = [
        item for item in list(validation.get("blocking_issues") or [])
        if item not in {"harness_not_run", "harness_failed"}
    ]
    if not report.passed:
        blocking.append("harness_failed")
    failed = [name for name, passed in combined_checks.items() if not passed]
    warnings = list(dict.fromkeys([
        *list(validation.get("warnings") or []),
        *report.warnings,
    ]))
    package_passed = bool(validation.get("passed"))
    validation.update({
        "checks": combined_checks,
        "failed_checks": failed,
        "blocking_issues": blocking,
        "warnings": warnings,
        "publishable": package_passed and report.passed and not blocking,
        "score": round(100 * sum(combined_checks.values()) / max(1, len(combined_checks))),
    })
    return {
        **metadata,
        "validation": validation,
        "eval_suite": {
            **dict(metadata.get("eval_suite") or {}),
            "status": report.status,
            "positive_results": report.positive_results,
            "negative_results": report.negative_results,
            "harness_checks": report.checks,
            "harness_score": report.score,
            "failed_checks": report.failed_checks,
            "content_sha256": report.content_sha256,
        },
    }


def _requests(metadata: dict[str, Any], rich_key: str, legacy_key: str) -> list[str]:
    rich = metadata.get(rich_key)
    if isinstance(rich, list):
        values = [
            str(item.get("user_request") or "").strip()
            for item in rich if isinstance(item, dict)
        ]
        if any(values):
            return [item for item in values if item]
    suite = metadata.get("eval_suite")
    if isinstance(suite, dict) and isinstance(suite.get(legacy_key), list):
        return [str(item).strip() for item in suite[legacy_key] if str(item).strip()]
    return []


def _trigger_result(draft: SkillDraft, request: str, *, expected: bool) -> dict[str, Any]:
    score, trigger = score_skill_match(draft, request)
    selected = score > 0
    return {
        "request": request,
        "expected_selected": expected,
        "selected": selected,
        "passed": selected is expected,
        "score": round(score, 3),
        "matched_trigger": trigger,
    }
