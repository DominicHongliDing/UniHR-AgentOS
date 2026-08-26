from types import SimpleNamespace

from app.domains.assistant.contracts import RetrievalMode
from app.domains.evidence_orchestration import EvidenceSourceOrchestrator, focus_evidence_query
from app.domains.knowledge import KnowledgeAccessContext
from app.domains.public_info.contracts import PublicInfoResult


class FakeKnowledgeRetriever:
    def __init__(self):
        self.calls = []

    def retrieve(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return SimpleNamespace(
            hits=[SimpleNamespace(citation="《薪酬制度》第二条", text="行政岗位执行分级薪酬。")],
            coverage=0.82,
        )


class FakePublicInfo:
    def __init__(self):
        self.calls = []

    def answer(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return PublicInfoResult(
            answer="官网公开口径显示最低工资标准已经调整。[1]",
            citations=[{"index": "1", "title": "省人社厅公告", "url": "https://example.com/policy"}],
            capability_status="available",
            query=query,
            trace=[{"event": "web.search", "status": "completed", "summary": "完成搜索"}],
        )


def test_hybrid_evidence_is_collected_once_for_downstream_agents():
    knowledge = FakeKnowledgeRetriever()
    public = FakePublicInfo()
    module = EvidenceSourceOrchestrator(knowledge, public)

    result = module.collect(
        "根据政策和官网生成材料",
        mode=RetrievalMode.HYBRID,
        access=KnowledgeAccessContext(tenant_id="tenant-a", user_id="user-a", role="user"),
        resolved_query="核对内部政策与最新官网口径后生成材料",
        query_variants=["内部薪酬政策", "官网最新口径"],
    )

    assert len(knowledge.calls) == 1
    assert len(public.calls) == 1
    assert [item.source_kind for item in result.sources] == ["internal_policy", "public_web"]
    assert result.citations[0]["title"] == "省人社厅公告"
    assert result.trace[-1]["event"] == "evidence.orchestration.completed"
    assert result.trace[-1]["evidence_count"] == 2


def test_complex_artifact_request_is_rewritten_per_evidence_source():
    request = (
        "请先读取内部知识库现有的行政管理岗薪酬方案，再联网核对浙江省人社部门近期公开口径，"
        "然后生成一份 Word 通知并添加六列表格。"
    )

    assert focus_evidence_query(request, "internal") == "行政管理岗薪酬方案"
    assert focus_evidence_query(request, "web") == "浙江省人社部门近期公开口径"


def test_web_query_drops_from_scratch_artifact_instruction():
    request = "请联网核对浙江省当前公开的最低工资标准，并从零生成一份 Excel 工作簿"

    assert focus_evidence_query(request, "web") == "浙江省当前公开的最低工资标准"
