from pathlib import Path

import pytest

from app.domains.knowledge import PolicyEvaluationRepository


def test_policy_evaluation_history_is_tenant_scoped(tmp_path: Path):
    repository = PolicyEvaluationRepository(tmp_path / "unihr.db")
    repository.save_evaluation(
        {"case_count": 12, "hit_rate": 1.0}, tenant_id="tenant-a", actor_id="admin-a",
        dataset_version="dataset-a", rerank_model="qwen3-rerank", duration_ms=123,
    )
    repository.save_evaluation(
        {"case_count": 3, "hit_rate": 0.5}, tenant_id="tenant-b", actor_id="admin-b",
        dataset_version="dataset-b", rerank_model="local", duration_ms=20,
    )

    history = repository.list_evaluations(tenant_id="tenant-a")

    assert len(history) == 1
    assert history[0]["case_count"] == 12
    assert history[0]["dataset_version"] == "dataset-a"
    assert history[0]["duration_ms"] == 123


def test_negative_policy_feedback_becomes_deidentified_draft_and_requires_admin_labels(tmp_path: Path):
    repository = PolicyEvaluationRepository(tmp_path / "unihr.db")
    candidate = repository.create_feedback_candidate(
        tenant_id="tenant-a", user_id="user-a", feedback_id="fb-1",
        conversation_id="conv-1", message_id="msg-1",
        question="HR 专员问：政策现在有效吗？手机号13812345678",
        answer="请联系zhang@example.com核验。", comment="没有回答有效期",
        redaction_terms=("HR 专员",),
        trace=[
            {"event": "policy.effective_date_gate"},
            {"event": "evidence.sources", "sources": [{
                "source_id": "source-v1", "title": "政策A", "source_version": "2025版",
                "policy_status": "unknown",
            }]},
        ],
    )

    assert candidate is not None
    assert candidate["status"] == "draft"
    assert "<USER>" in candidate["question"]
    assert "<PHONE>" in candidate["question"]
    assert "<EMAIL>" in candidate["answer_excerpt"]
    assert candidate["suggested_sources"][0]["source_id"] == "source-v1"

    with pytest.raises(ValueError, match="expected_sources_required"):
        repository.review_candidate(
            candidate["candidate_id"], tenant_id="tenant-a", reviewer_id="admin",
            approved=True,
        )

    approved = repository.review_candidate(
        candidate["candidate_id"], tenant_id="tenant-a", reviewer_id="admin",
        approved=True, expected_source_ids=("source-v1",), as_of_date="2026-08-09",
        forbidden_source_ids=("source-old",), notes="已核对来源",
    )

    assert approved["status"] == "approved"
    assert repository.approved_cases(tenant_id="tenant-a")[0]["expected_source_ids"] == ["source-v1"]


def test_non_policy_feedback_does_not_create_policy_candidate(tmp_path: Path):
    repository = PolicyEvaluationRepository(tmp_path / "unihr.db")

    candidate = repository.create_feedback_candidate(
        tenant_id="tenant-a", user_id="user-a", feedback_id="fb-2",
        conversation_id="conv-2", message_id="msg-2", question="你好", answer="你好",
        comment="", trace=[{"event": "model.answer"}],
    )

    assert candidate is None
    assert repository.list_candidates(tenant_id="tenant-a") == []
