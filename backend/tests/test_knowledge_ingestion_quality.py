import pytest

from app.domains.knowledge import KnowledgeVectorIndex
from app.domains.knowledge.ingestion import (
    IngestedChunk, IngestionQualityReport, IngestionResult,
)


class FlatEmbedder:
    model_name = "quality-flat-v1"

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class ReviewExtractor:
    def extract(self, _path):
        return IngestionResult((IngestedChunk(
            "预算上限 ?0 万元", page_number=1, anchor_kind="bbox",
            bbox=(100, 200, 600, 280), model_confidence=0.61,
            needs_review=True, evidence_kind="field",
        ),), IngestionQualityReport(
            status="review_required", score=50, extractor="vision_ocr",
            page_count=1, evidence_block_count=1, review_block_count=1,
            review_page_count=1, issues=("evidence_requires_human_review",),
            checks={"text_extracted": True, "evidence_localized": True,
                    "all_pages_processed": True, "no_review_flags": False},
        ))


def test_review_required_source_cannot_publish_until_admin_approves(tmp_path):
    root = tmp_path / "sources"
    folder = root / "policy-library"
    folder.mkdir(parents=True)
    (folder / "扫描政策.txt").write_text("placeholder", encoding="utf-8")
    index = KnowledgeVectorIndex(
        tmp_path / "index.db", FlatEmbedder(), "tenant-a",
        document_extractor=ReviewExtractor(),
    )

    index.index(root, tenant_id="tenant-a", actor_id="admin-a", auto_publish=False)
    version = index.list_versions(tenant_id="tenant-a", include_unpublished=True)[0]

    assert version.ingestion_status == "review_required"
    assert version.quality_score == 50
    with pytest.raises(ValueError, match="knowledge_quality_review_required"):
        index.publish_version(version.version_id, tenant_id="tenant-a", actor_id="admin-a")

    index.review_quality(version.version_id, actor_id="admin-a", approved=True)
    published = index.publish_version(version.version_id, tenant_id="tenant-a", actor_id="admin-a")
    assert published.lifecycle_status == "published"
    evidence = index.evidence(version.version_id)
    assert evidence["blocks"][0]["bbox"] == [100, 200, 600, 280]
    assert evidence["blocks"][0]["needs_review"] is True


def test_failed_source_can_be_retried_without_reprocessing_the_batch(tmp_path):
    root = tmp_path / "sources"
    folder = root / "policy-library"
    folder.mkdir(parents=True)
    failed_file = folder / "政策失败样例.txt"
    failed_file.write_text("", encoding="utf-8")
    stable_file = folder / "政策正常样例.txt"
    stable_file.write_text("正常政策内容。" * 10, encoding="utf-8")
    index = KnowledgeVectorIndex(tmp_path / "index.db", FlatEmbedder(), "tenant-a")

    index.index(root, tenant_id="tenant-a", auto_publish=False)
    failure = index.list_failures()[0]
    failed_file.write_text("修复后的政策正文，包含适用范围、办理条件和审批要求。" * 5, encoding="utf-8")
    relative_path = index.prepare_failure_retry(str(failure["source_id"]))
    events = []

    result = index.index(
        root, tenant_id="tenant-a", auto_publish=False,
        only_relative_paths=(relative_path,), progress_callback=events.append,
    )

    assert result["indexed_now"] == 1, (result, index.list_failures(), events)
    assert index.list_failures() == []
    assert all(event.get("total") == 1 for event in events)
    assert not any("政策正常样例" in str(event.get("current_file")) for event in events)
