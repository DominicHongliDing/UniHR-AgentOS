from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from .contracts import (
    EvalCaseResult,
    EvalRun,
    QualityMetric,
    QualityReport,
    ReleaseBudgetCheck,
    ReleaseDecision,
)


class QualityReporter:
    def report(
        self, run: EvalRun, results: list[EvalCaseResult],
        baseline_results: list[EvalCaseResult] | None = None,
    ) -> QualityReport:
        baseline_by_case = {item.case_id: item for item in baseline_results or []}
        regressions = [
            item.case_id for item in results
            if not item.passed and baseline_by_case.get(item.case_id) is not None
            and baseline_by_case[item.case_id].passed
        ]
        metrics = [
            self._metric("case_pass", "案例通过", results, lambda item: item.passed,
                         baseline_results),
            self._metric("intent", "意图与本轮动作", results,
                         lambda item: self._dimension_passed(item, "intent"), baseline_results),
            self._metric("artifact", "产物与 Harness", results,
                         lambda item: self._dimension_passed(item, "artifact_harness"),
                         baseline_results),
            self._metric("delivery", "回答交付", results,
                         lambda item: self._dimension_passed(item, "delivery"), baseline_results),
            self._metric("delivery_mode", "交付形态", results,
                         lambda item: self._assertion_passed(item, "delivery_mode_mismatch"),
                         baseline_results),
        ]
        counts = Counter(
            item.root_failure.layer for item in results if item.root_failure is not None
        )
        durations = sorted(item.observation.duration_ms for item in results)
        cost_results = [
            item for item in results if item.observation.model_call_count > 0
        ]
        costs = [float(item.observation.estimated_cost_cny or 0) for item in cost_results]
        priced_count = sum(
            item.observation.cost_pricing_configured is True for item in cost_results
        )
        return QualityReport(
            run=run, results=results, metrics=metrics,
            root_failure_counts=dict(counts), regressions=regressions,
            p50_duration_ms=_percentile(durations, 0.50),
            p95_duration_ms=_percentile(durations, 0.95),
            total_estimated_cost_cny=round(sum(costs), 8),
            average_estimated_cost_cny=(
                round(sum(costs) / len(costs), 8) if costs else 0
            ),
            priced_case_count=priced_count,
            cost_observed_case_count=len(cost_results),
            pricing_coverage=(priced_count / len(cost_results) if cost_results else 1),
        )

    @staticmethod
    def _dimension_passed(result: EvalCaseResult, dimension: str) -> bool:
        relevant = [item for item in result.assertions if item.dimension == dimension]
        return all(item.passed for item in relevant) if relevant else True

    @staticmethod
    def _assertion_passed(result: EvalCaseResult, code: str) -> bool:
        relevant = [item for item in result.assertions if item.code == code]
        return all(item.passed for item in relevant) if relevant else True

    def _metric(self, key: str, label: str, results: list[EvalCaseResult], predicate,
                baseline_results: list[EvalCaseResult] | None) -> QualityMetric:
        numerator = sum(bool(predicate(item)) for item in results)
        denominator = len(results)
        value = numerator / denominator if denominator else None
        baseline_value = None
        if baseline_results:
            base_passed = sum(bool(predicate(item)) for item in baseline_results)
            baseline_value = base_passed / len(baseline_results)
        return QualityMetric(
            key=key, label=label, numerator=numerator, denominator=denominator,
            value=value, baseline_value=baseline_value,
            delta=(value - baseline_value) if value is not None and baseline_value is not None else None,
        )


class ReleaseGate:
    """Critical failures block; statistical drift is visible and requires a waiver."""

    def assess(self, report: QualityReport) -> ReleaseDecision:
        critical = [item for item in report.results if item.criticality == "critical"]
        critical_failures = [item.case_id for item in critical if not item.passed]
        blocking = [f"critical_case_failed:{item}" for item in critical_failures]
        for result in report.results:
            for assertion in result.assertions:
                if assertion.critical and not assertion.passed:
                    value = f"{result.case_id}:{assertion.code}"
                    if value not in blocking:
                        blocking.append(value)
        warnings = [f"regression:{case_id}" for case_id in report.regressions]
        case_metric = next((item for item in report.metrics if item.key == "case_pass"), None)
        if case_metric and case_metric.value is not None and case_metric.value < 0.95:
            warnings.append(f"case_pass_below_target:{case_metric.value:.3f}<0.950")
        budget_checks = self._budget_checks(report)
        for check in budget_checks:
            if check.passed:
                continue
            comparator = "<" if check.code == "pricing_coverage" else ">"
            message = (
                f"budget:{check.code}:{check.actual:g}{check.unit}"
                f"{comparator}{check.limit:g}{check.unit}"
            )
            (blocking if check.blocking else warnings).append(message)
        status = "blocked" if blocking else "waiver_required" if warnings else "passed"
        return ReleaseDecision(
            run_id=report.run.id, status=status, blocking_failures=blocking,
            warnings=warnings, passed_critical=len(critical) - len(critical_failures),
            total_critical=len(critical),
            budget_checks=budget_checks,
            decided_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _budget_checks(report: QualityReport) -> list[ReleaseBudgetCheck]:
        policy = report.run.snapshot.budget_policy
        blocking = policy.enforcement == "blocking"
        checks: list[ReleaseBudgetCheck] = []
        if policy.max_p95_duration_ms > 0:
            checks.append(ReleaseBudgetCheck(
                code="p95_duration", label="P95 响应时延",
                actual=report.p95_duration_ms, limit=policy.max_p95_duration_ms,
                unit="ms", passed=report.p95_duration_ms <= policy.max_p95_duration_ms,
                blocking=blocking,
            ))
        if policy.max_average_cost_cny > 0:
            checks.append(ReleaseBudgetCheck(
                code="average_cost", label="单案例平均成本",
                actual=report.average_estimated_cost_cny,
                limit=policy.max_average_cost_cny, unit="元",
                passed=(
                    report.cost_observed_case_count > 0
                    and report.average_estimated_cost_cny <= policy.max_average_cost_cny
                ),
                blocking=blocking,
            ))
        if policy.max_total_cost_cny > 0:
            checks.append(ReleaseBudgetCheck(
                code="total_cost", label="评测总成本",
                actual=report.total_estimated_cost_cny, limit=policy.max_total_cost_cny,
                unit="元", passed=report.total_estimated_cost_cny <= policy.max_total_cost_cny,
                blocking=blocking,
            ))
        if policy.min_pricing_coverage > 0:
            checks.append(ReleaseBudgetCheck(
                code="pricing_coverage", label="模型定价覆盖率",
                actual=report.pricing_coverage, limit=policy.min_pricing_coverage,
                unit="比例", passed=report.pricing_coverage >= policy.min_pricing_coverage,
                blocking=blocking,
            ))
        return checks


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    index = max(0, min(len(values) - 1, int((len(values) - 1) * ratio + 0.999999)))
    return int(values[index])
