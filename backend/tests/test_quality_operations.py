from __future__ import annotations

from pathlib import Path

import pytest

from app.domains.identity import Principal
from app.domains.quality_operations import (
    EvalCase,
    EvalExpectation,
    EvalSuite,
    EvaluationObservation,
    EvaluationRequest,
    EvaluationSnapshot,
    InMemoryReplayAdapter,
    QualityOperations,
    QualityRepository,
    QualityBudgetPolicy,
)


def _principal() -> Principal:
    return Principal(
        user_id="admin", username="admin", display_name="管理员", role="admin",
        tenant_id="tenant", permissions=["answer_history.manage"],
    )


def _module(tmp_path: Path, observations: dict[str, EvaluationObservation]) -> QualityOperations:
    return QualityOperations(
        QualityRepository(tmp_path / "quality.db"),
        lambda: InMemoryReplayAdapter(observations),
        lambda: EvaluationSnapshot(model_chain=["qwen3.8-max-preview", "qwen3.7-max"]),
    )


def test_repository_versions_suites_without_overwriting_history(tmp_path: Path):
    repository = QualityRepository(tmp_path / "quality.db")
    suite = repository.save_suite(EvalSuite(
        id="custom", tenant_id="tenant", name="自定义", version=1,
    ))
    repository.save_case(EvalCase(
        id="case-1", tenant_id="tenant", suite_id=suite.id, suite_version=1,
        title="简单表格", prompt="做张表", expectation=EvalExpectation(
            capability_id="spreadsheet_processing",
        ),
    ))

    created = repository.create_suite_version("custom", "tenant")

    assert created.version == 2
    assert repository.get_suite("custom", "tenant", 1).case_ids == ["case-1"]
    assert repository.get_suite("custom", "tenant", 2).case_ids == ["case-1"]
    assert repository.list_cases("custom", "tenant", 2)[0].version == 2


def test_quality_run_persists_root_failure_and_blocks_critical_release(tmp_path: Path):
    module = _module(tmp_path, {
        "critical-route": EvaluationObservation(
            capability_id="policy_evidence", business_intent="policy_question",
            turn_action="start_new", retrieval_mode="internal", execution_mode="execute",
            answer_visible=True,
        ),
    })
    module.repository.save_suite(EvalSuite(
        id="custom", tenant_id="tenant", name="关键路由", version=1, frozen=True,
    ))
    module.repository.save_case(EvalCase(
        id="critical-route", tenant_id="tenant", suite_id="custom", suite_version=1,
        title="不能误入政策", prompt="今天台风大吗", criticality="critical",
        expectation=EvalExpectation(
            capability_id="public_information", business_intent="public_current_info",
            retrieval_mode="web",
        ),
    ))

    run = module.run(EvaluationRequest(suite_id="custom"), _principal())
    report = module.report(run.id, _principal())
    decision = module.assess(run.id, _principal())

    assert run.status == "completed"
    assert run.passed_count == 0
    assert report.results[0].root_failure is not None
    assert report.results[0].root_failure.layer == "intent"
    assert decision.status == "blocked"
    assert "critical_case_failed:critical-route" in decision.blocking_failures


def test_relation_style_no_artifact_assertion_catches_unwanted_file(tmp_path: Path):
    module = _module(tmp_path, {
        "review-only": EvaluationObservation(
            capability_id="spreadsheet_processing", artifact_names=["错误生成.xlsx"],
            answer_visible=True,
        ),
    })
    module.repository.save_suite(EvalSuite(
        id="negative", tenant_id="tenant", name="困难反例", version=1,
    ))
    module.repository.save_case(EvalCase(
        id="review-only", tenant_id="tenant", suite_id="negative", suite_version=1,
        title="只分析不生成", prompt="不要生成文件，只告诉我哪里有问题",
        expectation=EvalExpectation(
            capability_id="spreadsheet_processing",
            forbidden_artifact_extensions=[".xlsx"],
        ),
    ))

    run = module.run(EvaluationRequest(suite_id="negative"), _principal())
    result = module.report(run.id, _principal()).results[0]

    assert result.passed is False
    assert result.root_failure is not None
    assert result.root_failure.code == "forbidden_artifact_created"


def test_delivery_mode_is_a_separate_release_blocking_contract(tmp_path: Path):
    module = _module(tmp_path, {
        "inline-polish": EvaluationObservation(
            capability_id="document_generation",
            business_intent="document_generation",
            execution_mode="execute",
            delivery_mode="create_artifact",
            artifact_names=["错误生成.docx"],
            answer_visible=True,
        ),
    })
    module.repository.save_suite(EvalSuite(
        id="delivery", tenant_id="tenant", name="交付形态", version=1,
    ))
    module.repository.save_case(EvalCase(
        id="inline-polish", tenant_id="tenant", suite_id="delivery", suite_version=1,
        title="聊天内润色不能生成文件", prompt="帮我润色这段话", criticality="critical",
        expectation=EvalExpectation(
            capability_id="document_generation",
            delivery_mode="inline_transform",
            forbidden_artifact_extensions=[".docx"],
        ),
    ))

    run = module.run(EvaluationRequest(suite_id="delivery"), _principal())
    report = module.report(run.id, _principal())
    decision = module.assess(run.id, _principal())

    result = report.results[0]
    assert result.passed is False
    assert any(item.code == "delivery_mode_mismatch" for item in result.assertions)
    assert next(item for item in report.metrics if item.key == "delivery_mode").value == 0
    assert decision.status == "blocked"


def test_negative_feedback_is_scrubbed_and_deduplicated(tmp_path: Path):
    module = _module(tmp_path, {})
    principal = _principal()

    first = module.capture_feedback(
        feedback_id="feedback-1", source_message_id="message-1",
        prompt="管理员说这个回答不对", correction="admin 要求复用上一轮", principal=principal,
    )
    second = module.capture_feedback(
        feedback_id="feedback-1", source_message_id="message-1",
        prompt="重复提交", correction="重复", principal=principal,
    )

    assert first.id == second.id
    assert "管理员" not in first.scrubbed_prompt
    assert "admin" not in first.expected_correction
    assert len(module.repository.list_feedback_candidates("tenant")) == 1


def test_tenant_cannot_start_overlapping_quality_runs(tmp_path: Path):
    module = _module(tmp_path, {})
    module.repository.save_suite(EvalSuite(
        id="serial", tenant_id="tenant", name="串行运行", version=1,
    ))
    module.repository.save_case(EvalCase(
        id="one", tenant_id="tenant", suite_id="serial", suite_version=1,
        title="第一条", prompt="你好", expectation=EvalExpectation(answer_required=False),
    ))

    module.prepare(EvaluationRequest(suite_id="serial"), _principal())

    with pytest.raises(ValueError, match="quality_run_already_active"):
        module.prepare(EvaluationRequest(suite_id="serial"), _principal())


def test_quality_run_is_durably_queued_then_claimed_and_completed(tmp_path: Path):
    module = _module(tmp_path, {
        "one": EvaluationObservation(answer_visible=True, duration_ms=25),
    })
    module.repository.save_suite(EvalSuite(
        id="queued", tenant_id="tenant", name="持久队列", version=1,
    ))
    module.repository.save_case(EvalCase(
        id="one", tenant_id="tenant", suite_id="queued", suite_version=1,
        title="队列任务", prompt="你好", expectation=EvalExpectation(),
    ))

    queued = module.prepare(EvaluationRequest(suite_id="queued"), _principal())
    before = module.repository.queue_status("tenant")
    completed = module.execute_next("worker-test")
    after = module.repository.queue_status("tenant")

    assert queued.status == "queued"
    assert before.queued == 1
    assert completed is not None and completed.status == "completed"
    assert after.queued == 0 and after.running == 0


def test_release_gate_blocks_functionally_correct_run_over_latency_budget(tmp_path: Path):
    module = QualityOperations(
        QualityRepository(tmp_path / "quality.db"),
        lambda: InMemoryReplayAdapter({
            "slow": EvaluationObservation(
                answer_visible=True, duration_ms=2_500, model_call_count=1,
                estimated_cost_cny=0.02, cost_pricing_configured=True,
            ),
        }),
        lambda: EvaluationSnapshot(budget_policy=QualityBudgetPolicy(
            max_p95_duration_ms=1_000, max_average_cost_cny=0.05,
            min_pricing_coverage=1, enforcement="blocking",
        )),
    )
    module.repository.save_suite(EvalSuite(
        id="budget", tenant_id="tenant", name="预算门", version=1,
    ))
    module.repository.save_case(EvalCase(
        id="slow", tenant_id="tenant", suite_id="budget", suite_version=1,
        title="功能正确但太慢", prompt="生成材料", expectation=EvalExpectation(),
    ))

    run = module.run(EvaluationRequest(suite_id="budget"), _principal())
    report = module.report(run.id, _principal())
    decision = module.assess(run.id, _principal())

    assert run.passed_count == 1
    assert report.p95_duration_ms == 2_500
    assert report.average_estimated_cost_cny == 0.02
    assert decision.status == "blocked"
    assert any(item.code == "p95_duration" and not item.passed for item in decision.budget_checks)


def test_expired_worker_lease_can_be_reclaimed_without_duplicate_job(tmp_path: Path):
    module = _module(tmp_path, {"one": EvaluationObservation(answer_visible=True)})
    module.repository.save_suite(EvalSuite(
        id="lease", tenant_id="tenant", name="租约恢复", version=1,
    ))
    module.repository.save_case(EvalCase(
        id="one", tenant_id="tenant", suite_id="lease", suite_version=1,
        title="恢复", prompt="恢复任务", expectation=EvalExpectation(),
    ))
    run = module.prepare(EvaluationRequest(suite_id="lease"), _principal())
    first = module.repository.claim_job("worker-one", run_id=run.id)
    assert first is not None and first.attempts == 1
    with module.repository._connect() as db:
        db.execute(
            "UPDATE quality_eval_jobs SET leased_until=? WHERE run_id=?",
            ("2000-01-01T00:00:00+00:00", run.id),
        )

    second = module.repository.claim_job("worker-two", run_id=run.id)

    assert second is not None
    assert second.id == first.id
    assert second.attempts == 2
    assert second.worker_id == "worker-two"


def test_admin_accepts_feedback_into_a_real_versioned_suite(tmp_path: Path):
    module = _module(tmp_path, {})
    principal = _principal()
    candidate = module.capture_feedback(
        feedback_id="feedback-2", source_message_id="message-2",
        prompt="就选前两个人", correction="必须复用已有排名", principal=principal,
    )

    reviewed = module.review_feedback(
        candidate.id, accepted=True, title="招聘结果续问",
        expectation={
            "capability_id": "recruitment_screening",
            "execution_mode": "reuse_result",
        }, principal=principal,
    )

    suite = module.repository.get_suite("feedback-regression", "tenant")
    cases = module.repository.list_cases(suite.id, "tenant", suite.version)
    assert reviewed.status == "accepted"
    assert cases[0].prompt == "就选前两个人"
    assert cases[0].expectation.execution_mode == "reuse_result"
