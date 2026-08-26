import sqlite3
import json

from app.domains.knowledge import (
    PolicyRagEngine,
    PolicyRagRequest,
    RetrievalBundle,
    SearchHit,
)
from app.models.gateway import ModelReply
from app.domains.runs import RunPayloadStore, RunPointer
from app.observability import tool_progress_scope


class QueueGateway:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return ModelReply(self.replies.pop(0), kwargs.get("model") or "fake", 5, {})


class RetryingRetriever:
    def __init__(self):
        self.calls = []

    def retrieve(self, query, **kwargs):
        self.calls.append((query, kwargs))
        material_hit = SearchHit(
            chunk_id="materials",
            source_id="policy-1",
            title="博士后进站管理办法",
            collection="博士后政策",
            text="申请人应提交进站备案表和联合培养协议，材料齐全后进入复核。",
            page_number=3,
            section="申请材料",
            score=0.91,
            policy_status="active",
            authority_level="institution",
        )
        hits = [material_hit]
        coverage = 0.28
        if len(self.calls) > 1:
            hits.append(SearchHit(
                chunk_id="process",
                source_id="policy-1",
                title="博士后进站管理办法",
                collection="博士后政策",
                text="人事部门受理材料后进行审核，审核通过后办理进站备案。",
                page_number=4,
                section="办理流程",
                score=0.82,
                policy_status="active",
                authority_level="institution",
            ))
            coverage = 0.46
        return RetrievalBundle(
            hits=hits,
            queries=list(kwargs.get("query_variants") or []),
            coverage=coverage,
            status="sufficient" if len(hits) > 1 else "insufficient",
            candidate_count=len(hits),
        )


def test_policy_rag_retries_missing_dimension_and_repairs_unsupported_claim():
    retriever = RetryingRetriever()
    gateway = QueueGateway([
        "根据现有受控证据，结论如下：\n"
        "申请人应提交进站备案表和联合培养协议 [E1]。\n"
        "鉴于人事部门负责受理，可能由其完成最终审批 [E2]。\n"
        "办理期限为5个工作日。",
    ])
    engine = PolicyRagEngine(retriever, gateway, answer_model="qwen-test")

    result = engine.run(PolicyRagRequest(
        question="博士后进站需要哪些材料，办理流程是什么？",
        resolved_question="浙江省博士后进站备案需要哪些材料及办理流程",
        query_variants=["博士后 进站备案 材料"],
    ))

    assert result.attempts == 2
    assert result.sufficiency.sufficient is True
    assert len(retriever.calls) == 2
    assert retriever.calls[0][1]["candidate_limit"] >= 40
    assert any(item["event"] == "evidence.retry" for item in result.trace)
    assert any(item["event"] == "claim_evidence.align" for item in result.trace)
    assert "当前证据未直接支持" in result.answer
    assert "该表述包含间接推断" in result.answer
    assert "结论如下： 【" not in result.answer
    assert "[E1]" in result.answer


def test_policy_rag_streams_governed_progress_without_query_or_evidence_text():
    retriever = RetryingRetriever()
    gateway = QueueGateway(["申请材料见证据 [E1]，办理流程见证据 [E2]。"])
    engine = PolicyRagEngine(retriever, gateway, answer_model="qwen-test")
    progress: list[tuple[str, str, dict]] = []

    with tool_progress_scope(
        lambda event, summary, details: progress.append((event, summary, details))
    ):
        engine.run(PolicyRagRequest(
            question="CONFIDENTIAL_QUERY 博士后进站需要什么材料？",
            resolved_question="博士后进站材料和办理流程",
        ))

    names = [event for event, _summary, _details in progress]
    assert names[0:2] == ["tool.policy.plan.started", "tool.policy.plan.completed"]
    assert "tool.policy.sufficiency.completed" in names
    assert "tool.policy.rewrite.completed" in names
    assert "tool.policy.answer.started" in names
    assert "tool.policy.answer.completed" in names
    assert "tool.policy.temporal.completed" in names
    assert "tool.policy.conflict.completed" in names
    assert "tool.policy.alignment.completed" in names
    assert names[-1] == "tool.policy.finalize.completed"
    serialized = json.dumps(progress, ensure_ascii=False)
    assert "CONFIDENTIAL_QUERY" not in serialized
    assert "联合培养协议" not in serialized


def test_policy_rag_offloads_evidence(tmp_path):
    database = tmp_path / "unihr.db"
    store = RunPayloadStore(database)
    pointer = RunPointer(run_id="run_policy", tenant_id="tenant_a", owner_user_id="user_a")
    engine = PolicyRagEngine(
        RetryingRetriever(),
        QueueGateway(["申请材料见政策证据 [E1]。办理流程见政策证据 [E2]。"]),
        answer_model="qwen-test",
        payload_store=store,
    )

    result = engine.run(PolicyRagRequest(
        question="博士后进站需要哪些材料，办理流程是什么？",
        payload_scope=pointer,
    ))

    assert result.evidence_count >= 1
    assert any(item["event"] == "graph.payload.offloaded" for item in result.trace)
    with sqlite3.connect(database) as db:
        row = db.execute("SELECT kind FROM run_payloads WHERE run_id=?", (pointer.run_id,)).fetchone()
    assert row[0] == "policy.evidence"


def test_policy_rag_returns_governed_fallback_when_no_evidence_exists():
    class EmptyRetriever:
        def retrieve(self, query, **kwargs):
            return RetrievalBundle([], [query], 0.0, "insufficient")

    gateway = QueueGateway(["不应调用模型"])
    engine = PolicyRagEngine(EmptyRetriever(), gateway, answer_model="qwen-test")

    result = engine.run(PolicyRagRequest(question="现行公积金调整比例是多少？"))

    assert "不会根据常识补写政策结论" in result.answer
    assert gateway.calls == []
    assert result.claim_review.status == "no_evidence"


def test_policy_conflict_gate_detects_different_hard_values_and_requires_review():
    class ConflictRetriever:
        def retrieve(self, query, **kwargs):
            return RetrievalBundle([
                SearchHit(
                    chunk_id="old-rate", source_id="old-policy", title="公积金缴存办法（旧）",
                    collection="薪酬政策", text="职工住房公积金缴存比例为10%。",
                    page_number=2, section="缴存标准", score=0.92,
                    policy_status="unknown", authority_level="institution",
                ),
                SearchHit(
                    chunk_id="new-rate", source_id="new-policy", title="公积金缴存办法（新）",
                    collection="薪酬政策", text="职工住房公积金缴存比例为12%。",
                    page_number=3, section="缴存标准", score=0.90,
                    policy_status="unknown", authority_level="institution",
                ),
            ], [query], 0.8, "sufficient")

    gateway = QueueGateway([
        "旧办法规定缴存比例为10% [E1]；新办法规定为12% [E2]。"
        "两份证据存在冲突，需核验有效期和正式适用版本。",
    ])
    result = PolicyRagEngine(
        ConflictRetriever(), gateway, answer_model="qwen-test",
    ).run(PolicyRagRequest(question="公积金缴存比例现在按多少执行？"))

    assert result.conflict_review.status == "detected"
    assert result.conflict_review.requires_human_review is True
    assert result.conflict_review.items[0].kind == "hard_value"
    assert result.answer.startswith("**可以先把现有方案讲清楚；最终执行口径仍需核验。**")
    assert "不推荐优先采用其中任何一版" in result.answer
    assert any(item["event"] == "policy.conflict_gate" for item in result.trace)
    assert "确定性 Conflict Gate 已发现冲突" in gateway.calls[0][0][0]["content"]


def test_conflict_guard_keeps_model_grounded_explanation_but_removes_precedence_advice():
    class ConflictRetriever:
        def retrieve(self, query, **kwargs):
            return RetrievalBundle([
                SearchHit(
                    chunk_id="old", source_id="old-policy", title="行政岗薪酬方案（旧）",
                    collection="薪酬政策", text="副科主持工作按0.8系数执行。",
                    page_number=2, section="岗位系数", score=0.92,
                ),
                SearchHit(
                    chunk_id="new", source_id="new-policy", title="行政岗薪酬方案（新）",
                    collection="薪酬政策", text="副科主持工作按0.85系数执行。",
                    page_number=2, section="岗位系数", score=0.91,
                ),
            ], [query], 0.8, "sufficient")

    model_answer = (
        "现有方案有两种表述：旧方案为0.8系数 [E1]；新方案为0.85系数 [E2]。"
        "建议优先采用新方案执行 [E2]。以新方案0.85系数为准或需进一步核验 [E2]。"
        "依据最新完整表格 [E2]，应指0.85系数。"
    )
    result = PolicyRagEngine(
        ConflictRetriever(), QueueGateway([model_answer]), answer_model="qwen-test",
    ).run(PolicyRagRequest(question="行政岗副科主持工作按什么系数？"))

    assert "**基于现有证据的方案梳理**" in result.answer
    assert "旧方案为0.8系数 [E1]" in result.answer
    assert "新方案为0.85系数 [E2]" in result.answer
    assert "建议优先采用" not in result.answer
    assert "以新方案0.85系数为准" not in result.answer
    assert "最新完整表格" not in result.answer
    assert "应指0.85系数" not in result.answer


def test_policy_conflict_gate_detects_coefficient_changes():
    class CoefficientRetriever:
        def retrieve(self, query, **kwargs):
            return RetrievalBundle([
                SearchHit(
                    chunk_id="old-coefficient", source_id="old-salary", title="行政岗位薪酬方案",
                    collection="薪酬政策", text="副科主持工作按正科相应档级0.8系数执行。",
                    page_number=2, section="岗位系数", score=0.92,
                ),
                SearchHit(
                    chunk_id="new-coefficient", source_id="new-salary", title="行政岗位薪酬方案",
                    collection="薪酬政策", text="副科主持工作按正科相应档级0.85系数执行。",
                    page_number=2, section="岗位系数", score=0.91,
                ),
            ], [query], 0.8, "sufficient")

    result = PolicyRagEngine(
        CoefficientRetriever(),
        QueueGateway(["两份方案分别写明0.8系数 [E1] 与0.85系数 [E2]，需人工核验版本。"]),
        answer_model="qwen-test",
    ).run(PolicyRagRequest(question="行政岗位方案里的副科主持工作系数是多少？"))

    assert result.conflict_review.status == "detected"
    assert result.conflict_review.items[0].left_values == ("0.8系数",)
    assert result.conflict_review.items[0].right_values == ("0.85系数",)
    assert "岗位系数：检出 0.8系数、0.85系数 的不同表述（涉及 [E1]、[E2]）" in result.answer


def test_version_ambiguity_still_discloses_each_existing_scheme_before_requesting_review():
    class SalaryVersionRetriever:
        def retrieve(self, query, **kwargs):
            return RetrievalBundle([
                SearchHit(
                    chunk_id="salary-draft-a", source_id="salary-source-a",
                    version_id="salary-version-a", title="行政管理岗位聘任及薪酬方案（试行）",
                    collection="薪酬政策",
                    text="岗位绩效按月发放，新入职员工自聘任次月起享受相应待遇。",
                    page_number=1, section="岗位绩效", score=0.94,
                    policy_status="trial", authority_level="institution",
                ),
                SearchHit(
                    chunk_id="salary-draft-b", source_id="salary-source-b",
                    version_id="salary-version-b", title="行政管理岗位聘任及薪酬方案（试行）",
                    collection="薪酬政策",
                    text="岗位绩效按月发放，校内调入人员自聘任之日起发放。",
                    page_number=1, section="岗位绩效", score=0.93,
                    policy_status="trial", authority_level="institution",
                ),
            ], [query], 0.8, "sufficient")

    result = PolicyRagEngine(
        SalaryVersionRetriever(),
        QueueGateway(["存在两个待核验版本。"]),
        answer_model="qwen-test",
    ).run(PolicyRagRequest(question="我想了解行政管理岗的薪酬方案"))

    assert result.conflict_review.items[0].kind == "version_ambiguity"
    assert "**当前可确认的方案内容**" in result.answer
    assert "[E1]" in result.answer and "新入职员工自聘任次月起" in result.answer
    assert "[E2]" in result.answer and "校内调入人员自聘任之日起" in result.answer
    assert "最终执行口径仍需核验" in result.answer


def test_conflict_answer_scopes_to_user_field_and_never_recommends_an_unverified_version():
    class MixedSalaryRetriever:
        def retrieve(self, query, **kwargs):
            return RetrievalBundle([
                SearchHit(
                    chunk_id="old-coefficient", source_id="salary-2025-01", title="行政岗位薪酬方案",
                    collection="薪酬政策",
                    text=("副科（主持工作）按正科相应档级0.8系数执行。"
                          "岗位绩效按月发放，任职满2年可调整档级。"),
                    page_number=2, section="岗位绩效", score=0.92,
                ),
                SearchHit(
                    chunk_id="new-coefficient", source_id="salary-2025-03", title="行政岗位薪酬方案",
                    collection="薪酬政策",
                    text=("副科（主持工作）按正科相应档级0.85系数执行。"
                          "岗位绩效按月发放，任职满4年可调整档级。"),
                    page_number=2, section="岗位绩效", score=0.91,
                ),
                SearchHit(
                    chunk_id="performance-b", source_id="salary-2025-official", title="行政岗位薪酬方案",
                    collection="薪酬政策",
                    text="处职及科职绩效B各分2档，任职满6年进入下一档。",
                    page_number=3, section="绩效B", score=0.85,
                ),
            ], [query], 0.8, "sufficient")

    result = PolicyRagEngine(
        MixedSalaryRetriever(),
        QueueGateway(["建议优先按0.85系数执行 [E2]。"]),
        answer_model="qwen-test",
    ).run(PolicyRagRequest(question="行政岗薪酬方案里，副科主持工作的系数到底按多少？"))

    assert result.conflict_review.conflict_count == 1
    assert result.conflict_review.items[0].left_values == ("0.8系数",)
    assert result.conflict_review.items[0].right_values == ("0.85系数",)
    assert "建议优先" not in result.answer
    assert "不推荐优先采用其中任何一版" in result.answer
    assert "岗位系数：检出 0.8系数、0.85系数 的不同表述（涉及 [E1]、[E2]）" in result.answer


def test_policy_conflict_gate_does_not_treat_complementary_sources_as_conflict():
    class ComplementaryRetriever:
        def retrieve(self, query, **kwargs):
            return RetrievalBundle([
                SearchHit(
                    chunk_id="materials", source_id="materials-policy", title="申请材料细则",
                    collection="博士后政策", text="申请人应提交备案表和培养协议。",
                    page_number=1, section="材料", score=0.9,
                ),
                SearchHit(
                    chunk_id="process", source_id="process-policy", title="办理流程细则",
                    collection="博士后政策", text="人事部门受理后进行审核并办理备案。",
                    page_number=2, section="流程", score=0.85,
                ),
            ], [query], 0.5, "sufficient")

    result = PolicyRagEngine(
        ComplementaryRetriever(),
        QueueGateway(["申请材料见 [E1]，办理流程见 [E2]。"]),
        answer_model="qwen-test",
    ).run(PolicyRagRequest(question="申请材料和办理流程是什么？"))

    assert result.conflict_review.status == "none"
    assert result.conflict_review.requires_human_review is False


def test_policy_conflict_gate_does_not_compare_unrelated_numbers_across_sections():
    class FormalAndAttachmentRetriever:
        def retrieve(self, query, **kwargs):
            return RetrievalBundle([
                SearchHit(
                    chunk_id="notice", source_id="formal-policy",
                    title="关于印发工作站博士后考核及管理实施细则的通知",
                    collection="博士后政策",
                    text="本细则自2024年10月起实施，退站违约金为3万元。",
                    page_number=1, section="印发通知", score=0.92,
                    policy_status="unknown", authority_level="institution",
                ),
                SearchHit(
                    chunk_id="exit-review", source_id="policy-attachment",
                    title="附件：工作站博士后考核及管理实施细则",
                    collection="博士后政策",
                    text="出站考核须提交2封推荐信，并在3个月内完成材料复核。",
                    page_number=6, section="出站考核", score=0.88,
                    policy_status="unknown", authority_level="institution",
                ),
            ], [query], 0.7, "sufficient")

    result = PolicyRagEngine(
        FormalAndAttachmentRetriever(),
        QueueGateway(["正式通知说明实施时间 [E1]，附件说明出站材料要求 [E2]。"]),
        answer_model="qwen-test",
    ).run(PolicyRagRequest(question="工作站博士后管理细则现在还能直接按吗？"))

    assert result.conflict_review.status == "none"
    assert result.conflict_review.requires_human_review is False


def test_effective_date_gate_rejects_current_validity_claim_when_metadata_is_unknown():
    class UnknownStatusRetriever:
        def retrieve(self, query, **kwargs):
            return RetrievalBundle([
                SearchHit(
                    chunk_id="notice", source_id="formal-policy",
                    title="关于印发工作站博士后考核及管理实施细则的通知",
                    collection="博士后政策",
                    text="本细则自公布之日起实施。",
                    page_number=1, section="印发通知", score=0.93,
                    source_version="示例学院发〔2024〕8号",
                    policy_status="unknown", authority_level="institution",
                ),
            ], [query], 0.8, "sufficient")

    result = PolicyRagEngine(
        UnknownStatusRetriever(),
        QueueGateway(["该细则当前有效并可直接执行 [E1]。"]),
        answer_model="qwen-test",
    ).run(PolicyRagRequest(question="工作站博士后管理细则现在还能直接按吗？"))

    assert result.temporal_review.status == "unverified"
    assert result.temporal_review.requires_human_review is True
    assert result.answer.startswith("**结论：目前不能确认")
    assert "没有检出失效记录不等于" in result.answer
    assert any(item["event"] == "policy.effective_date_gate" for item in result.trace)


def test_effective_date_gate_keeps_grounded_policy_content_while_blocking_validity_claim():
    class UnknownStatusRetriever:
        def retrieve(self, query, **kwargs):
            return RetrievalBundle([
                SearchHit(
                    chunk_id="notice", source_id="formal-policy", title="行政岗位薪酬方案",
                    collection="薪酬政策",
                    text="岗位绩效按月发放，新入职员工自聘任次月起享受待遇。",
                    page_number=1, section="岗位绩效", score=0.93,
                    source_version="示例学院发〔2024〕8号",
                    policy_status="unknown", authority_level="institution",
                ),
            ], [query], 0.8, "sufficient")

    model_answer = (
        "文件写明岗位绩效按月发放，新入职员工自聘任次月起享受待遇 [E1]。"
        "该方案目前有效，可以直接执行 [E1]。"
    )
    result = PolicyRagEngine(
        UnknownStatusRetriever(), QueueGateway([model_answer]), answer_model="qwen-test",
    ).run(PolicyRagRequest(question="这个行政岗薪酬方案现在还能直接按吗？"))

    assert "**文件中当前可以确认的内容**" in result.answer
    assert "岗位绩效按月发放" in result.answer
    assert "目前有效" not in result.answer
    assert "可以直接执行 [E1]" not in result.answer
