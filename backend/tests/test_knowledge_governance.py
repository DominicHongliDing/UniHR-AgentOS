from dataclasses import replace

from app.domains.knowledge import KnowledgeAccessContext, KnowledgeVectorIndex
from app.domains.knowledge.retriever import PolicyKnowledgeRetriever


class FlatEmbedder:
    model_name = "governance-flat-v1"
    dimensions = 2

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class ReverseReranker:
    name = "reverse-test-reranker"

    def rerank(self, query, hits, *, limit):
        return [replace(item, rerank_score=0.9, rerank_model=self.name) for item in reversed(hits)][:limit]


class WeakReranker:
    name = "weak-test-reranker"

    def rerank(self, query, hits, *, limit):
        return [replace(item, score=0.42, rerank_score=0.42, rerank_model=self.name) for item in hits][:limit]


class RelativeReranker:
    name = "relative-test-reranker"

    def rerank(self, query, hits, *, limit):
        scores = [0.92, 0.41]
        return [
            replace(item, score=scores[index], rerank_score=scores[index], rerank_model=self.name)
            for index, item in enumerate(hits[:2])
        ]


def _source_root(tmp_path):
    root = tmp_path / "sources"
    collection = root / "policy-library"
    collection.mkdir(parents=True)
    policy = collection / "博士后管理办法.txt"
    policy.write_text(
        "第一条 适用范围\n本办法适用于学校博士后研究人员。\n"
        "第二条 申请材料\n申请人应提交申请表和联合培养协议。",
        encoding="utf-8",
    )
    return root, policy


def test_draft_version_is_not_retrievable_until_acl_publication(tmp_path):
    root, _ = _source_root(tmp_path)
    index = KnowledgeVectorIndex(tmp_path / "index.db", FlatEmbedder(), "tenant-a")
    result = index.index(root, tenant_id="tenant-a", actor_id="admin-a", auto_publish=False)
    user_access = KnowledgeAccessContext(tenant_id="tenant-a", user_id="user-a", role="user")

    assert result["pending_versions"] == 1
    assert index.search("博士后申请材料", access=user_access) == []
    draft = index.list_versions(tenant_id="tenant-a", include_unpublished=True)[0]
    assert draft.lifecycle_status == "draft"

    admin_only = index.publish_version(
        draft.version_id,
        tenant_id="tenant-a",
        actor_id="admin-a",
        allowed_roles=("admin",),
    )
    assert admin_only.lifecycle_status == "published"
    assert index.search("博士后申请材料", access=user_access) == []
    admin_hits = index.search(
        "博士后申请材料",
        access=KnowledgeAccessContext(tenant_id="tenant-a", user_id="admin-a", role="admin"),
    )
    assert admin_hits and admin_hits[0].version_id == draft.version_id

    index.publish_version(
        draft.version_id,
        tenant_id="tenant-a",
        actor_id="admin-a",
        allowed_roles=("admin", "user"),
    )
    user_hits = index.search("博士后申请材料", access=user_access)
    assert user_hits
    assert user_hits[0].publication_id
    assert "行" in user_hits[0].citation


def test_new_version_is_immutable_and_does_not_replace_published_projection(tmp_path):
    root, policy = _source_root(tmp_path)
    index = KnowledgeVectorIndex(tmp_path / "index.db", FlatEmbedder(), "tenant-a")
    index.index(root, tenant_id="tenant-a", actor_id="admin-a", auto_publish=True)
    access = KnowledgeAccessContext(tenant_id="tenant-a", user_id="user-a", role="user")
    first = index.search("联合培养协议", access=access)[0]

    policy.write_text(
        "第一条 适用范围\n本办法适用于学校博士后研究人员。\n"
        "第二条 申请材料\n申请人应提交新版申请表、诚信承诺书和联合培养协议。",
        encoding="utf-8",
    )
    index.index(root, tenant_id="tenant-a", actor_id="admin-a", auto_publish=False)
    versions = index.list_versions(tenant_id="tenant-a", include_unpublished=True)

    assert len(versions) == 2
    assert len({item.version_id for item in versions}) == 2
    assert "诚信承诺书" not in index.search("申请材料", access=access)[0].text
    draft = next(item for item in versions if item.lifecycle_status == "draft")
    index.publish_version(
        draft.version_id,
        tenant_id="tenant-a",
        actor_id="admin-a",
        allowed_roles=("admin", "user"),
    )
    after = index.search("诚信承诺书", access=access)[0]
    assert after.version_id != first.version_id
    assert "诚信承诺书" in after.text
    historical = index.search("申请材料", access=access, include_withdrawn=True)
    assert {item.version_id for item in historical} == {first.version_id, after.version_id}
    comparison = index.compare_versions(
        first.version_id,
        after.version_id,
        tenant_id="tenant-a",
    )
    assert comparison["added_chunk_count"] >= 1
    assert any("诚信承诺书" in item for item in comparison["added_samples"])
    assert comparison["similarity"] < 1


def test_same_title_archived_snapshots_can_be_compared_as_one_policy_family(tmp_path):
    root = tmp_path / "sources"
    old_dir = root / "policy-library" / "20250113"
    new_dir = root / "policy-library" / "20250301"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    title = "行政管理岗位聘任及薪酬方案（试行）.txt"
    (old_dir / title).write_text(
        "第一条 岗位薪酬标准\n副科主持工作按正科相应档级0.8系数执行，并由人事部门复核。",
        encoding="utf-8",
    )
    (new_dir / title).write_text(
        "第一条 岗位薪酬标准\n副科主持工作按正科相应档级0.85系数执行，并由人事部门复核。",
        encoding="utf-8",
    )
    index = KnowledgeVectorIndex(tmp_path / "index.db", FlatEmbedder(), "tenant-a")
    index.index(root, tenant_id="tenant-a", actor_id="admin-a", auto_publish=True)
    versions = index.list_versions(tenant_id="tenant-a", include_unpublished=True)

    assert len(versions) == 2
    comparison = index.compare_versions(
        versions[0].version_id, versions[1].version_id, tenant_id="tenant-a",
    )
    assert comparison["similarity"] < 1
    assert "|" in comparison["source_id"]


def test_retriever_uses_injected_rerank_adapter(tmp_path):
    root = tmp_path / "sources" / "policy-library"
    root.mkdir(parents=True)
    (root / "甲政策.txt").write_text(
        "第一条 甲政策办理流程需要提交申请表并审核。材料齐全后由人事部门登记，并在规定期限内完成复核。",
        encoding="utf-8",
    )
    (root / "乙政策.txt").write_text(
        "第一条 乙政策办理流程需要提交材料并复核。受理部门核对适用范围以后，按照权限完成审批备案。",
        encoding="utf-8",
    )
    index = KnowledgeVectorIndex(tmp_path / "index.db", FlatEmbedder())
    index.index(tmp_path / "sources")
    raw = index.search("办理流程", limit=2)

    bundle = PolicyKnowledgeRetriever(index, ReverseReranker()).retrieve(
        "办理流程", limit=2, candidate_limit=10,
    )

    assert bundle.rerank_model == "reverse-test-reranker"
    assert bundle.hits[0].chunk_id == raw[-1].chunk_id
    assert bundle.hits[0].rerank_model == "reverse-test-reranker"


def test_low_coverage_and_weak_rerank_is_treated_as_no_evidence(tmp_path):
    root, _ = _source_root(tmp_path)
    index = KnowledgeVectorIndex(tmp_path / "index.db", FlatEmbedder())
    index.index(root)

    bundle = PolicyKnowledgeRetriever(index, WeakReranker()).retrieve(
        "学院食堂今天午餐有什么菜？", limit=4, candidate_limit=10,
    )

    assert bundle.hits == []
    assert bundle.status == "insufficient"
    assert bundle.relevance_filtered_count > 0


def test_strong_rerank_cluster_drops_relative_outlier(tmp_path):
    root = tmp_path / "sources" / "policy-library"
    root.mkdir(parents=True)
    (root / "甲政策.txt").write_text(
        "第一条 办理材料包括申请表、身份证明和业务部门审核意见，材料齐全后进入复核程序。",
        encoding="utf-8",
    )
    (root / "乙政策.txt").write_text(
        "第一条 其他待遇按照年度预算执行，财务部门负责核对金额并完成资金发放工作，年度结束后统一整理预算执行情况。",
        encoding="utf-8",
    )
    index = KnowledgeVectorIndex(tmp_path / "index.db", FlatEmbedder())
    index.index(tmp_path / "sources")

    bundle = PolicyKnowledgeRetriever(index, RelativeReranker()).retrieve(
        "办理需要提交哪些材料？", limit=4, candidate_limit=10,
    )

    assert len(bundle.hits) == 1
    assert bundle.rerank_filtered_count == 1
