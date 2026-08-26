from __future__ import annotations

from collections import Counter
from socket import gethostname
from typing import Callable
from uuid import uuid4

from app.domains.identity import Principal

from .catalog import seed_default_quality_suites
from .contracts import (
    EvalCaseResult,
    EvalRun,
    EvaluationObservation,
    EvaluationRequest,
    EvaluationSnapshot,
    FeedbackCandidate,
    QualityReport,
    ReleaseDecision,
)
from .evaluator import DeterministicEvaluator
from .release_gate import QualityReporter, ReleaseGate
from .repository import QualityRepository
from .replay import ReplayAdapter


class QualityOperations:
    """Deep module for versioned replay, deterministic evidence and release decisions."""

    def __init__(
        self, repository: QualityRepository,
        replay_factory: Callable[[], ReplayAdapter],
        snapshot_factory: Callable[[], EvaluationSnapshot],
        *,
        worker_lease_seconds: int = 600,
    ) -> None:
        self.repository = repository
        self.replay_factory = replay_factory
        self.snapshot_factory = snapshot_factory
        self.evaluator = DeterministicEvaluator()
        self.reporter = QualityReporter()
        self.release_gate = ReleaseGate()
        self.worker_lease_seconds = max(30, int(worker_lease_seconds))

    def prepare(self, request: EvaluationRequest, principal: Principal) -> EvalRun:
        seed_default_quality_suites(self.repository, principal.tenant_id)
        if self.repository.has_active_run(principal.tenant_id):
            raise ValueError("quality_run_already_active")
        suite = self.repository.get_suite(request.suite_id, principal.tenant_id)
        cases = self.repository.list_cases(suite.id, principal.tenant_id, suite.version)
        if request.case_ids:
            selected = set(request.case_ids)
            cases = [item for item in cases if item.id in selected]
        if not cases:
            raise ValueError("evaluation_suite_has_no_cases")
        run = self.repository.start_run(
            tenant_id=principal.tenant_id, suite=suite,
            candidate_label=request.candidate_label,
            baseline_run_id=request.baseline_run_id,
            snapshot=self.snapshot_factory(), case_count=len(cases),
        )
        self.repository.enqueue_run(run, principal.model_dump(mode="json"))
        return run

    def execute(self, run_id: str, principal: Principal) -> EvalRun:
        """Compatibility entry point; production workers still claim the durable job."""
        worker_id = f"inline-{gethostname()}-{uuid4().hex[:8]}"
        job = self.repository.claim_job(
            worker_id, run_id=run_id, lease_seconds=self.worker_lease_seconds,
        )
        if job is None:
            return self.repository.get_run(run_id, principal.tenant_id)
        return self._execute_claimed(run_id, principal)

    def execute_next(self, worker_id: str) -> EvalRun | None:
        job = self.repository.claim_job(
            worker_id, lease_seconds=self.worker_lease_seconds,
        )
        if job is None:
            return None
        principal = Principal.model_validate(job.principal)
        return self._execute_claimed(job.run_id, principal)

    def _execute_claimed(self, run_id: str, principal: Principal) -> EvalRun:
        run = self.repository.get_run(run_id, principal.tenant_id)
        suite = self.repository.get_suite(run.suite_id, principal.tenant_id, run.suite_version)
        cases = self.repository.list_cases(suite.id, principal.tenant_id, suite.version)
        replay = self.replay_factory()
        results: list[EvalCaseResult] = []
        try:
            for case in cases:
                if self.repository.is_cancelled(run.id, principal.tenant_id):
                    return self.repository.get_run(run.id, principal.tenant_id)
                try:
                    observation = replay.replay(
                        case, principal, evaluation_run_id=run.id,
                    )
                except Exception as exc:  # evidence is persisted instead of losing the batch
                    observation = EvaluationObservation(runtime_error=f"{type(exc).__name__}:{exc}")
                result = self.evaluator.evaluate(case, observation, run_id=run.id)
                results.append(self.repository.save_result(result))
            completed = self.repository.complete_run(
                run.id, principal.tenant_id,
                passed_count=sum(item.passed for item in results),
            )
            report = self.report(completed.id, principal)
            self.repository.save_decision(self.release_gate.assess(report), principal.tenant_id)
            self.repository.complete_job(run.id, principal.tenant_id)
            return completed
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            self.repository.fail_job(run.id, principal.tenant_id, error)
            return self.repository.get_run(run.id, principal.tenant_id)
        finally:
            replay.close()

    def run(self, request: EvaluationRequest, principal: Principal) -> EvalRun:
        return self.execute(self.prepare(request, principal).id, principal)

    def report(self, run_id: str, principal: Principal) -> QualityReport:
        run = self.repository.get_run(run_id, principal.tenant_id)
        results = self.repository.list_results(run.id, principal.tenant_id)
        baseline = (
            self.repository.list_results(run.baseline_run_id, principal.tenant_id)
            if run.baseline_run_id else None
        )
        return self.reporter.report(run, results, baseline)

    def assess(self, run_id: str, principal: Principal) -> ReleaseDecision:
        decision = self.release_gate.assess(self.report(run_id, principal))
        return self.repository.save_decision(decision, principal.tenant_id)

    def capture_feedback(
        self, *, feedback_id: str, source_message_id: str, prompt: str,
        correction: str, principal: Principal,
    ) -> FeedbackCandidate:
        return self.repository.create_feedback_candidate(
            tenant_id=principal.tenant_id, feedback_id=feedback_id,
            source_message_id=source_message_id,
            scrubbed_prompt=_scrub(prompt, principal),
            expected_correction=_scrub(correction, principal),
        )

    def overview(self, principal: Principal) -> dict[str, object]:
        seed_default_quality_suites(self.repository, principal.tenant_id)
        runs = self.repository.list_runs(principal.tenant_id)
        latest = runs[0] if runs else None
        failure_counts: Counter[str] = Counter()
        if latest is not None:
            for result in self.repository.list_results(latest.id, principal.tenant_id):
                if result.root_failure:
                    failure_counts[result.root_failure.layer] += 1
        return {
            "suites": self.repository.list_suites(principal.tenant_id),
            "runs": runs,
            "latest_root_failures": dict(failure_counts),
            "feedback_candidates": self.repository.list_feedback_candidates(principal.tenant_id),
            "storage": self.repository.storage_status(),
            "queue": self.repository.queue_status(principal.tenant_id),
        }

    def review_feedback(
        self, candidate_id: str, *, accepted: bool, title: str,
        expectation: dict[str, object], principal: Principal,
    ) -> FeedbackCandidate:
        candidate = self.repository.get_feedback_candidate(candidate_id, principal.tenant_id)
        if not accepted:
            return self.repository.review_feedback_candidate(
                candidate.id, principal.tenant_id, "rejected",
            )
        try:
            suite = self.repository.get_suite("feedback-regression", principal.tenant_id)
        except KeyError:
            from .contracts import EvalSuite
            suite = self.repository.save_suite(EvalSuite(
                id="feedback-regression", tenant_id=principal.tenant_id,
                name="真实用户反馈回归", description="管理员确认后的脱敏失败案例。",
                domain="feedback", version=1, frozen=False,
            ))
        from .contracts import EvalCase, EvalExpectation
        case_id = f"feedback-{candidate.feedback_id}"
        existing = {
            item.id for item in self.repository.list_cases(
                suite.id, principal.tenant_id, suite.version,
            )
        }
        if case_id not in existing:
            self.repository.save_case(EvalCase(
                id=case_id, tenant_id=principal.tenant_id, suite_id=suite.id,
                suite_version=suite.version, title=title.strip() or "用户反馈回归",
                prompt=candidate.scrubbed_prompt,
                expectation=EvalExpectation.model_validate(expectation),
                criticality="standard", tags=["feedback", "real-user"],
                source=f"feedback:{candidate.feedback_id}",
            ))
        return self.repository.review_feedback_candidate(
            candidate.id, principal.tenant_id, "accepted",
        )


def _scrub(value: str, principal: Principal) -> str:
    clean = value
    for term in (principal.username, principal.display_name, principal.user_id):
        if term:
            clean = clean.replace(term, "[REDACTED]")
    return clean[:20_000]
