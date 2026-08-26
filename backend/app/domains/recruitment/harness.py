from __future__ import annotations

import re
from collections import Counter

from .contracts import RecruitmentHarnessReport, RecruitmentReport


_PROTECTED_PATTERN = re.compile(
    r"(?:性别|男性|女性|男生|女生|年龄|岁|民族|婚育|婚姻|已婚|未婚|照片|颜值|籍贯|住址|现居地|居住地)"
)


def remove_protected_attributes(value: str) -> tuple[str, bool]:
    clean = " ".join(str(value).split())
    if not _PROTECTED_PATTERN.search(clean):
        return clean, False
    return _PROTECTED_PATTERN.sub("[已排除受保护属性]", clean), True


class RecruitmentHarness:
    """Validate one recruitment report through its observable domain contract."""

    def inspect(self, report: RecruitmentReport) -> RecruitmentHarnessReport:
        issues: list[str] = []
        warnings: list[str] = []
        criteria = report.criteria
        criterion_ids = [item.id for item in criteria]
        weight_total = round(sum(item.weight for item in criteria), 2)
        if not 3 <= len(criteria) <= 8:
            issues.append("Rubric 维度数量必须为3到8项")
        if len(set(criterion_ids)) != len(criterion_ids):
            issues.append("Rubric 维度 ID 存在重复")
        if abs(weight_total - 100) > 0.1:
            issues.append("Rubric 权重合计不是100%")

        representative_sources = {
            item.source_name for item in report.source_profiles
            if not item.duplicate_of and item.version_status != "superseded"
        }
        evaluated_sources = {item.source_name for item in report.candidates}
        missing_sources = sorted(representative_sources - evaluated_sources)
        unknown_sources = sorted(evaluated_sources - representative_sources)
        if missing_sources:
            issues.append(f"{len(missing_sources)}份唯一材料没有候选人结果")
        if unknown_sources:
            issues.append(f"结果包含{len(unknown_sources)}个不属于当前 Case 的来源")

        incomplete_candidates = 0
        score_recalculation_mismatches = 0
        low_evidence_rows = 0
        total_evidence_rows = 0
        protected_mentions = 0
        located_evidence_rows = 0
        review_evidence_rows = 0
        unlocated_evidence_rows = 0
        scored_unlocated_rows = 0
        for candidate in report.candidates:
            expected_total = round(sum(
                row.score * row.weight / 100 for row in candidate.criterion_scores
            ), 1)
            if abs(candidate.total_score - expected_total) > 0.1:
                score_recalculation_mismatches += 1
            returned_ids = {item.criterion_id for item in candidate.criterion_scores}
            if returned_ids != set(criterion_ids):
                incomplete_candidates += 1
            for row in candidate.criterion_scores:
                total_evidence_rows += 1
                if row.confidence == "low" or "未找到明确证据" in row.evidence:
                    low_evidence_rows += 1
                if _PROTECTED_PATTERN.search(row.evidence):
                    protected_mentions += 1
                locator_status = row.locator.status if row.locator is not None else "unlocated"
                if locator_status == "located":
                    located_evidence_rows += 1
                elif locator_status == "review_required":
                    review_evidence_rows += 1
                else:
                    unlocated_evidence_rows += 1
                    if row.score > 0:
                        scored_unlocated_rows += 1
            protected_mentions += sum(
                1 for value in [*candidate.strengths, *candidate.gaps, *candidate.verification_questions]
                if _PROTECTED_PATTERN.search(value)
            )
        if incomplete_candidates:
            issues.append(f"{incomplete_candidates}位候选人缺少完整的逐维度评分")
        if score_recalculation_mismatches:
            issues.append(f"{score_recalculation_mismatches}位候选人的总分不能由已锁定 Rubric 重算")
        if protected_mentions:
            issues.append("评分输出仍包含受保护属性")
        if scored_unlocated_rows:
            warnings.append(f"{scored_unlocated_rows} 个非零评分行尚不能回到原始材料位置")
        if review_evidence_rows:
            warnings.append(f"{review_evidence_rows} 个评分证据定位需要人工核对原文")

        low_evidence_ratio = round(low_evidence_rows / max(1, total_evidence_rows), 4)
        if low_evidence_ratio >= 0.5:
            warnings.append("超过一半评分维度缺少高质量原文证据")
        elif low_evidence_ratio >= 0.25:
            warnings.append("部分评分维度证据较弱，建议重点人工核实")

        score_counts = Counter(round(item.total_score, 1) for item in report.candidates)
        tied_candidates = sum(count for count in score_counts.values() if count > 1)
        close_pairs = sum(
            1 for left, right in zip(report.candidates, report.candidates[1:])
            if abs(left.total_score - right.total_score) < 3
        )
        near_score_groups = _near_score_groups(report)
        if tied_candidates or close_pairs:
            warnings.append("候选人存在并列或近分组，排名不应替代人工判断")

        duplicate_count = sum(len(item.duplicate_sources) for item in report.duplicate_groups)
        semantic_merged_count = sum(
            max(0, len(item.version_sources) - 1)
            for item in report.version_groups if item.status == "merged"
        )
        version_review_groups = sum(
            item.status == "review_required" for item in report.version_groups
        )
        if version_review_groups:
            warnings.append(f"{version_review_groups} 组同名相似材料需要人工确认是否属于同一候选人")
        source_coverage = round(len(evaluated_sources & representative_sources) / max(1, len(representative_sources)), 4)
        evidence_coverage = round(1 - low_evidence_ratio, 4)
        score = 100.0
        score -= min(60, len(issues) * 20)
        score -= min(25, len(warnings) * 8)
        score = max(0, round(score, 1))
        status = "failed" if issues else "warning" if warnings else "passed"
        return RecruitmentHarnessReport(
            status=status,
            score=score,
            issues=issues,
            warnings=warnings,
            metrics={
                "source_count": len(report.source_profiles),
                "unique_source_count": len(representative_sources),
                "duplicate_source_count": duplicate_count,
                "semantic_merged_source_count": semantic_merged_count,
                "version_group_count": len(report.version_groups),
                "version_review_group_count": version_review_groups,
                "candidate_count": len(report.candidates),
                "source_coverage": source_coverage,
                "evidence_coverage": evidence_coverage,
                "low_evidence_rows": low_evidence_rows,
                "tied_candidates": tied_candidates,
                "close_ranking_pairs": close_pairs,
                "near_score_groups": near_score_groups,
                "score_recalculation_mismatches": score_recalculation_mismatches,
                "located_evidence_rows": located_evidence_rows,
                "review_evidence_rows": review_evidence_rows,
                "unlocated_evidence_rows": unlocated_evidence_rows,
                "scored_unlocated_rows": scored_unlocated_rows,
                "evidence_localization_rate": round(
                    located_evidence_rows / max(1, total_evidence_rows), 4
                ),
                "ranking_basis": "total_score_desc,evidence_coverage_desc,candidate_name,source_name",
                "ranking_stability": "review_required" if (tied_candidates or near_score_groups) else "stable",
            },
        )


def _near_score_groups(report: RecruitmentReport, threshold: float = 3.0) -> int:
    """Count contiguous near-score groups after deterministic ranking."""

    groups = 0
    active = False
    for left, right in zip(report.candidates, report.candidates[1:]):
        if abs(left.total_score - right.total_score) < threshold:
            if not active:
                groups += 1
            active = True
        else:
            active = False
    return groups
