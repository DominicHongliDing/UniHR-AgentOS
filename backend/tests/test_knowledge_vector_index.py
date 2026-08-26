from app.domains.knowledge.vector_index import KnowledgeVectorIndex, SearchHit
from app.domains.knowledge.retriever import PolicyKnowledgeRetriever


class KeywordEmbedder:
    model_name = "keyword-test-v1"
    dimensions = 3

    def embed(self, texts):
        result = []
        for text in texts:
            result.append([
                1.0 if "公积金" in text else 0.0,
                1.0 if "博士后" in text else 0.0,
                0.2,
            ])
        return result


def test_vector_index_only_ingests_policy_candidates_and_returns_citation(tmp_path):
    root = tmp_path / "sources"
    collection = root / "calculate-annual-housing-fund"
    collection.mkdir(parents=True)
    (collection / "公积金政策.txt").write_text(
        "第一条 适用范围\n本办法适用于学校在职职工住房公积金年度基数调整。\n"
        "第二条 复核要求\n正式申报前应由人事和财务共同复核。",
        encoding="utf-8",
    )
    (collection / "人员名单.txt").write_text("张三 个人基数 8000", encoding="utf-8")
    index = KnowledgeVectorIndex(tmp_path / "index.db", KeywordEmbedder())

    stats = index.index(root)
    hits = index.search("公积金年度调整", limit=3)

    assert stats["indexed_sources"] == 1
    assert stats["indexed_chunks"] >= 1
    assert hits[0].title == "公积金政策.txt"
    assert "公积金" in hits[0].text
    assert "人员名单" not in {hit.title for hit in hits}
    assert "《公积金政策.txt》" in hits[0].citation


class FlatEmbedder:
    model_name = "flat-test-v1"
    dimensions = 2

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def test_hybrid_search_prioritizes_exact_policy_terms_when_vectors_are_ambiguous(tmp_path):
    root = tmp_path / "sources"
    collection = root / "policy-library"
    collection.mkdir(parents=True)
    (collection / "博士后政策.txt").write_text(
        "博士后进站备案管理办法\n申请人应提交进站备案表和联合培养协议，材料齐全后进入复核。",
        encoding="utf-8",
    )
    (collection / "公积金政策.txt").write_text(
        "住房公积金年度调整办法\n年度基数调整应复核缴存基数、比例和人员范围。",
        encoding="utf-8",
    )
    index = KnowledgeVectorIndex(tmp_path / "index.db", FlatEmbedder())
    index.index(root)

    hits = index.search("博士后进站备案需要哪些材料", limit=2)

    assert hits[0].title == "博士后政策.txt"
    assert hits[0].lexical_score > 0
    assert hits[0].retrieval_method == "hybrid"


def test_ingestion_failures_are_persisted_for_governance(tmp_path):
    root = tmp_path / "sources"
    collection = root / "policy-library"
    collection.mkdir(parents=True)
    (collection / "空白政策.txt").write_text("", encoding="utf-8")
    (collection / "旧版政策.doc").write_bytes(b"legacy-office")
    index = KnowledgeVectorIndex(tmp_path / "index.db", FlatEmbedder())

    result = index.index(root)
    stats = index.stats()

    assert result["failed_now"] == 2
    assert stats["failed_sources"] == 2
    assert stats["failure_reasons"] == {
        "empty_or_scanned": 1,
        "unsupported_legacy_format": 1,
    }


def test_current_policy_retrieval_filters_repealed_sources_and_keeps_metadata(tmp_path):
    root = tmp_path / "sources"
    collection = root / "policy-library"
    collection.mkdir(parents=True)
    (collection / "公积金调整办法（已废止）.txt").write_text(
        "第一条 调整依据\n2022年1月1日起施行。住房公积金缴存比例为旧标准，申报前由经办人员复核人员范围。",
        encoding="utf-8",
    )
    (collection / "公积金调整办法（2026年修订）.txt").write_text(
        "第一条 调整依据\n本办法自2026年1月1日起施行。住房公积金年度调整应按现行基数复核，并核对缴存人员范围。",
        encoding="utf-8",
    )
    index = KnowledgeVectorIndex(tmp_path / "index.db", FlatEmbedder())
    index.index(root)

    bundle = PolicyKnowledgeRetriever(index).retrieve(
        "现行公积金调整办法",
        current_only=True,
        limit=6,
        candidate_limit=40,
    )

    assert bundle.hits
    assert all(hit.policy_status != "repealed" for hit in bundle.hits)
    assert bundle.hits[0].effective_date == "2026-01-01"
    assert bundle.hits[0].source_version == "2026年修订"
    assert bundle.filtered_count >= 1


def test_as_of_retrieval_excludes_documents_published_after_target_date():
    class PublishedLaterIndex:
        def search(self, query, **kwargs):
            return [
                SearchHit(
                    chunk_id="future-notice", source_id="notice-2013",
                    title="2013年度培训通知", collection="政策库",
                    text="2013年度培训班于2月开班。", page_number=1, section="安排",
                    score=0.95, publication_date="2013-01-06",
                ),
                SearchHit(
                    chunk_id="future-named-policy", source_id="policy-2025",
                    title="示例学院发〔2025〕2号薪酬方案", collection="政策库",
                    text="2025年度岗位薪酬标准。", page_number=1, section="标准",
                    score=0.90, source_version="示例学院发〔2025〕2号",
                ),
                SearchHit(
                    chunk_id="undated-unrelated", source_id="undated-attachment",
                    title="附件：工作站博士后考核细则", collection="政策库",
                    text="博士后出站前应完成材料复核。", page_number=1, section="考核",
                    score=0.88,
                ),
            ]

    bundle = PolicyKnowledgeRetriever(PublishedLaterIndex()).retrieve(
        "截至2012年12月31日培训班如何安排",
        as_of_date="2012-12-31",
    )

    assert bundle.hits == []
    assert bundle.filtered_count == 3


def test_as_of_retrieval_keeps_relevant_historical_evidence():
    class HistoricalIndex:
        def search(self, query, **kwargs):
            return [SearchHit(
                chunk_id="historical-notice", source_id="notice-2012",
                title="人事处2012年度职称外语考试培训通知", collection="政策库",
                text="职称外语考试培训班于2012年10月开班。", page_number=1, section="培训安排",
                score=0.93, publication_date="2012-08-01",
            )]

    bundle = PolicyKnowledgeRetriever(HistoricalIndex()).retrieve(
        "截至2012年12月31日，人事处职称外语考试培训班如何安排",
        as_of_date="2012-12-31",
    )

    assert [hit.source_id for hit in bundle.hits] == ["notice-2012"]
