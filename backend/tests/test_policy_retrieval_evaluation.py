from app.domains.knowledge import (
    PolicyRetrievalCase,
    PolicyRetrievalEvaluator,
    RetrievalBundle,
    SearchHit,
)


class FixedRetriever:
    def retrieve(self, query, **kwargs):
        titles = ["无关政策.txt", "目标政策.txt"] if "命中" in query else ["无关政策.txt"]
        hits = [
            SearchHit(
                chunk_id=f"chunk-{index}",
                source_id=f"source-{index}",
                title=title,
                collection="政策库",
                text=title,
                page_number=1,
                section="",
                score=1.0 / index,
            )
            for index, title in enumerate(titles, start=1)
        ]
        return RetrievalBundle(hits, [query], 0.5, "sufficient")


def test_policy_retrieval_evaluator_reports_hit_recall_and_mrr():
    report = PolicyRetrievalEvaluator(FixedRetriever()).evaluate([
        PolicyRetrievalCase("hit", "命中问题", ("目标政策.txt",)),
        PolicyRetrievalCase("miss", "未找到", ("另一政策.txt",)),
    ], top_k=5)

    assert report.case_count == 2
    assert report.hit_rate == 0.5
    assert report.recall_at_k == 0.5
    assert report.mean_reciprocal_rank == 0.25


class GovernedFixtureRetriever:
    def retrieve(self, query, **kwargs):
        assert kwargs["as_of_date"] == "2025-06-30"
        return RetrievalBundle([
            SearchHit(
                chunk_id="new", source_id="new", title="新办法.pdf", collection="政策库",
                text="缴存比例为12%", page_number=2, section="标准", score=0.9,
                policy_status="active", effective_date="2025-01-01",
            ),
            SearchHit(
                chunk_id="old", source_id="old", title="旧办法.pdf", collection="政策库",
                text="缴存比例为10%", page_number=1, section="标准", score=0.8,
                policy_status="repealed", expiry_date="2024-12-31",
            ),
        ], [query], 0.8, "sufficient")


def test_policy_evaluator_reports_ndcg_citation_temporal_misuse_and_conflict_recall():
    report = PolicyRetrievalEvaluator(GovernedFixtureRetriever()).evaluate([
        PolicyRetrievalCase(
            case_id="conflict", question="现行比例是多少", expected_sources=("新办法.pdf", "旧办法.pdf"),
            relevance_by_source={"新办法.pdf": 3, "旧办法.pdf": 1},
            forbidden_sources=("旧办法.pdf",), as_of_date="2025-06-30",
            expected_conflict=True,
        ),
    ], top_k=5)

    assert report.ndcg_at_k == 1.0
    assert report.citation_coverage == 1.0
    assert report.effective_date_misuse_rate == 1.0
    assert report.conflict_evidence_recall == 1.0
    assert report.results[0].retrieved_forbidden_sources == ("旧办法.pdf",)


def test_policy_evaluator_penalizes_bad_order_missing_citation_and_missing_conflict_side():
    class WeakRetriever:
        def retrieve(self, query, **kwargs):
            class UncitedHit:
                def __init__(self, title):
                    self.title = title
                    self.citation = ""
            return RetrievalBundle([
                UncitedHit("旧办法.pdf"),
                UncitedHit("新办法.pdf"),
            ], [query], 0.5, "sufficient")

    report = PolicyRetrievalEvaluator(WeakRetriever()).evaluate([
        PolicyRetrievalCase(
            case_id="ranking", question="按哪个", expected_sources=("新办法.pdf", "配套细则.pdf"),
            relevance_by_source={"新办法.pdf": 3, "配套细则.pdf": 2}, expected_conflict=True,
        ),
    ], top_k=5)

    assert 0 < report.ndcg_at_k < 1
    assert report.citation_coverage == 0
    assert report.conflict_evidence_recall == 0


def test_policy_evaluator_distinguishes_same_title_versions_by_source_id():
    class SameTitleVersionRetriever:
        def retrieve(self, query, **kwargs):
            return RetrievalBundle([
                SearchHit(
                    chunk_id="v1", source_id="archive-20250113", title="同名薪酬方案.docx",
                    collection="薪酬政策", text="副科主持工作按0.8系数确定。",
                    page_number=1, section="岗位绩效B", score=0.9,
                ),
                SearchHit(
                    chunk_id="v2", source_id="archive-20250301", title="同名薪酬方案.docx",
                    collection="薪酬政策", text="副科主持工作按0.85系数确定。",
                    page_number=1, section="岗位绩效B", score=0.88,
                ),
            ], [query], 0.8, "sufficient")

    report = PolicyRetrievalEvaluator(SameTitleVersionRetriever()).evaluate([
        PolicyRetrievalCase(
            case_id="same-title-conflict", question="系数到底是多少", expected_sources=(),
            expected_source_ids=("archive-20250113", "archive-20250301"),
            relevance_by_source_id={"archive-20250113": 3, "archive-20250301": 3},
            expected_conflict=True,
        ),
    ], top_k=5)

    assert report.hit_rate == 1
    assert report.recall_at_k == 1
    assert report.conflict_evidence_recall == 1
    assert report.results[0].matched_source_ids == (
        "archive-20250113", "archive-20250301",
    )
