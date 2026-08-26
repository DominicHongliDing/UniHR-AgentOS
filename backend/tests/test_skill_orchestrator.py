from app.domains.assistant.contracts import BusinessIntent, TaskIntent
from app.domains.skills import (
    SkillDraft,
    SkillOrchestrator,
    SkillPlanningRequest,
    SkillRegistry,
)
from app.domains.skills.semantic_selection import (
    EmbeddingSkillSemanticRetriever,
    SemanticSkillBatch,
    SemanticSkillMatch,
    SkillRerankBatch,
    SkillRerankMatch,
)


def test_short_prompt_selects_skill_from_task_intent_without_skill_name(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    plan = SkillOrchestrator().plan(
        SkillPlanningRequest(
            query="帮我把这批排一下，给我能复核的表",
            task_intent=TaskIntent(
                objective="筛选候选人",
                domain=BusinessIntent.RECRUITMENT_SCREENING,
                deliverables=["可复核排名", "Excel"],
            ),
            required_capabilities=["file.extract", "recruitment.evaluate", "artifact.create"],
            available_input_kinds={"text", "task_brief", "attachment"},
        ),
        registry.list(),
    )

    assert plan.status == "ready"
    assert any("简历" in node.display_name for node in plan.nodes)
    assert "recruitment.evaluate" in plan.covered_capabilities
    assert all(node.skill_version >= 1 for node in plan.nodes)


def test_spreadsheet_document_request_selects_only_skills_that_add_required_capabilities(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    plan = SkillOrchestrator().plan(
        SkillPlanningRequest(
            query="按这份表格的字段规则处理新数据，再生成一份正式通知",
            task_intent=TaskIntent(
                objective="处理表格并形成通知",
                domain=BusinessIntent.SPREADSHEET_PROCESSING,
                deliverables=["Excel", "Word 通知"],
            ),
            required_capabilities=[
                "spreadsheet.inspect", "spreadsheet.transform", "document.compose", "artifact.create",
            ],
            available_input_kinds={"text", "task_brief", "attachment"},
        ),
        registry.list(),
    )

    names = [node.display_name for node in plan.nodes]
    assert plan.status == "ready"
    assert any("表格" in name for name in names)
    assert any("公文" in name or "会议" in name for name in names)
    assert set(["spreadsheet.transform", "document.compose"]).issubset(plan.covered_capabilities)
    assert len(plan.nodes) <= 5


def test_explicit_conflicting_skills_are_rejected(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    first = registry.create(SkillDraft(
        name="first-explicit-skill",
        display_name="第一流程",
        description="显式冲突测试的第一条流程。",
        instructions="执行第一流程。",
        capabilities=["document.compose"],
        enabled=True,
        status="active",
        metadata={"orchestration": {
            "intents": ["document_generation"],
            "provides": ["document.compose"],
            "conflicts_with": ["second-explicit-skill"],
        }},
    ))
    second = registry.create(SkillDraft(
        name="second-explicit-skill",
        display_name="第二流程",
        description="显式冲突测试的第二条流程。",
        instructions="执行第二流程。",
        capabilities=["artifact.create"],
        enabled=True,
        status="active",
    ))

    plan = SkillOrchestrator().plan(
        SkillPlanningRequest(
            query="同时执行两条流程",
            explicit_skill_ids=[first.id, second.id],
        ),
        registry.list(),
    )

    assert plan.status == "rejected"
    assert plan.conflicts == ["first-explicit-skill<->second-explicit-skill"]


def test_required_attachment_is_reported_without_simulated_execution(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    skill = registry.create(SkillDraft(
        name="attachment-required-skill",
        display_name="附件核验流程",
        description="只有读取真实附件后才能执行的核验流程。",
        instructions="读取真实附件并完成核验。",
        capabilities=["file.extract"],
        enabled=True,
        status="active",
        metadata={"orchestration": {
            "intents": ["recruitment_screening"],
            "provides": ["file.extract"],
            "inputs": [{
                "name": "source_files", "description": "待核验附件", "kind": "attachment", "required": True,
            }],
        }},
    ))

    plan = SkillOrchestrator().plan(
        SkillPlanningRequest(
            query="帮我核验",
            explicit_skill_ids=[skill.id],
            available_input_kinds={"text", "task_brief"},
        ),
        registry.list(),
    )

    assert plan.status == "needs_input"
    assert plan.missing_inputs == ["附件核验流程:source_files"]


def test_public_information_task_does_not_load_unrelated_hr_skills(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    plan = SkillOrchestrator().plan(
        SkillPlanningRequest(
            query="今天宁波天气怎么样",
            task_intent=TaskIntent(
                objective="查询天气",
                domain=BusinessIntent.PUBLIC_CURRENT_INFO,
            ),
            required_capabilities=["web.search", "weather.current"],
        ),
        registry.list(),
    )

    assert plan.status == "no_match"
    assert plan.nodes == []


class _SemanticRetriever:
    def __init__(self, scores: dict[str, float]):
        self.scores = scores

    def retrieve(self, query, candidates, *, task_intent, required_capabilities, limit):
        return SemanticSkillBatch(
            matches=[
                SemanticSkillMatch(skill_id=skill.id, score=self.scores[skill.name])
                for skill in candidates if skill.name in self.scores
            ],
            provider="test-embedding",
        )


class _CandidateReranker:
    def __init__(self, scores: dict[str, float]):
        self.scores = scores

    def rerank(self, query, candidates, *, limit):
        return SkillRerankBatch(
            matches=[
                SkillRerankMatch(skill_id=item.skill_id, score=self.scores.get(item.display_name, 0))
                for item in candidates
            ],
            provider="test-small-reranker",
        )


def test_semantic_recall_selects_paraphrased_skill_without_trigger_or_task_intent(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    spreadsheet = next(item for item in registry.list() if item.name == "build-reviewable-spreadsheet")
    plan = SkillOrchestrator(
        semantic_retriever=_SemanticRetriever({spreadsheet.name: 0.91}),
    ).plan(
        SkillPlanningRequest(query="把上一周期的做法套到新一批数据里，给我一份能复核的结果", max_nodes=1),
        registry.list(),
    )

    assert plan.status == "ready"
    assert plan.nodes[0].skill_id == spreadsheet.id
    assert plan.nodes[0].semantic_score == 0.91
    assert plan.nodes[0].selection_signals == ["semantic"]
    assert plan.candidate_strategy == "hybrid_semantic"


def test_small_model_rerank_changes_candidate_order_but_not_validator(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    spreadsheet = next(item for item in registry.list() if item.name == "build-reviewable-spreadsheet")
    document = next(item for item in registry.list() if item.name == "draft-hr-documents")
    plan = SkillOrchestrator(
        semantic_retriever=_SemanticRetriever({spreadsheet.name: 0.72, document.name: 0.70}),
        candidate_reranker=_CandidateReranker({spreadsheet.display_name: 0.05, document.display_name: 0.99}),
    ).plan(
        SkillPlanningRequest(query="按我的要求做成一份正式材料", max_nodes=1),
        registry.list(),
    )

    assert plan.nodes[0].skill_id == document.id
    assert plan.rerank_used is True
    assert plan.rerank_provider == "test-small-reranker"
    assert plan.nodes[0].selection_signals == ["semantic", "rerank"]


def test_semantic_selection_still_reports_required_inputs(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    skill = registry.create(SkillDraft(
        name="semantic-attachment-check",
        display_name="Evidence attachment check",
        description="Inspect a source attachment before producing evidence.",
        instructions="Read and inspect the attachment.",
        capabilities=["file.extract"],
        source_type="system",
        enabled=True,
        status="active",
        metadata={"orchestration": {
            "provides": ["file.extract"],
            "inputs": [{"name": "source", "kind": "attachment", "required": True}],
        }},
    ))
    plan = SkillOrchestrator(
        semantic_retriever=_SemanticRetriever({skill.name: 0.96}),
    ).plan(SkillPlanningRequest(query="please verify my source", max_nodes=1), registry.list())

    assert plan.nodes[0].skill_id == skill.id
    assert plan.status == "needs_input"
    assert plan.missing_inputs == ["Evidence attachment check:source"]


class _KeywordEmbeddingGateway:
    model_name = "test-embedding-v1"
    dimensions = 2

    def __init__(self):
        self.batch_sizes = []

    def embed(self, texts):
        self.batch_sizes.append(len(texts))
        result = []
        for text in texts:
            lowered = text.casefold()
            if "weather-only-negative" in lowered:
                result.append([1.0, 0.0])
            elif "resume-positive" in lowered:
                result.append([0.0, 1.0])
            else:
                result.append([0.0, 1.0])
        return result


def test_manifest_vector_index_is_persistent_and_reuses_skill_vectors(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    skill = registry.create(SkillDraft(
        name="semantic-resume-test",
        display_name="resume-positive",
        description="resume-positive evidence ranking",
        instructions="rank evidence",
        capabilities=["recruitment.evaluate"],
        source_type="system",
        enabled=True,
        status="active",
        metadata={"non_examples": [{"user_request": "weather-only-negative"}]},
    ))
    gateway = _KeywordEmbeddingGateway()
    retriever = EmbeddingSkillSemanticRetriever(tmp_path / "unihr.db", gateway)

    first = retriever.retrieve(
        "resume-positive", [skill], task_intent=None, required_capabilities=[], limit=5,
    )
    second = retriever.retrieve(
        "resume-positive", [skill], task_intent=None, required_capabilities=[], limit=5,
    )

    assert first.matches[0].skill_id == skill.id
    assert second.matches[0].skill_id == skill.id
    assert gateway.batch_sizes == [3, 1]


def test_semantic_similarity_cannot_expand_document_plan_with_unrelated_skills(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    scores = {item.name: 0.80 for item in registry.list()}
    plan = SkillOrchestrator(
        semantic_retriever=_SemanticRetriever(scores),
        candidate_reranker=_CandidateReranker({item.display_name: 0.75 for item in registry.list()}),
    ).plan(
        SkillPlanningRequest(
            query="make a signable people confirmation form",
            task_intent=TaskIntent(
                objective="create a Word confirmation form",
                domain=BusinessIntent.DOCUMENT_GENERATION,
                deliverables=["Word"],
            ),
        ),
        registry.list(),
    )

    assert [item.skill_id for item in plan.nodes] == [
        next(item.id for item in registry.list() if item.name == "draft-hr-documents")
    ]


def test_semantic_similarity_cannot_load_hr_skills_for_public_information(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    plan = SkillOrchestrator(
        semantic_retriever=_SemanticRetriever({item.name: 0.99 for item in registry.list()}),
    ).plan(
        SkillPlanningRequest(
            query="is it windy tomorrow",
            task_intent=TaskIntent(
                objective="check weather",
                domain=BusinessIntent.PUBLIC_CURRENT_INFO,
            ),
            required_capabilities=["web.search", "weather.current"],
        ),
        registry.list(),
    )

    assert plan.status == "no_match"
    assert plan.nodes == []


def test_declared_spreadsheet_domain_cannot_be_overridden_by_recruitment_similarity(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    recruitment = next(item for item in registry.list() if item.name == "screen-resumes-with-evidence")
    spreadsheet = next(item for item in registry.list() if item.name == "build-reviewable-spreadsheet")
    plan = SkillOrchestrator(
        semantic_retriever=_SemanticRetriever({recruitment.name: 0.78, spreadsheet.name: 0.70}),
        candidate_reranker=_CandidateReranker({
            recruitment.display_name: 0.76,
            spreadsheet.display_name: 0.60,
        }),
    ).plan(
        SkillPlanningRequest(
            query="rank these people and show reviewable reasons",
            task_intent=TaskIntent(
                objective="rank people",
                domain=BusinessIntent.SPREADSHEET_PROCESSING,
            ),
            max_nodes=2,
        ),
        registry.list(),
    )

    assert [item.skill_id for item in plan.nodes] == [spreadsheet.id]
    assert recruitment.id not in {item.skill_id for item in plan.nodes}


def test_high_confidence_secondary_skill_expands_real_composite_task(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    document = next(item for item in registry.list() if item.name == "draft-hr-documents")
    spreadsheet = next(item for item in registry.list() if item.name == "build-reviewable-spreadsheet")
    plan = SkillOrchestrator(
        semantic_retriever=_SemanticRetriever({document.name: 0.79, spreadsheet.name: 0.84}),
        candidate_reranker=_CandidateReranker({
            document.display_name: 0.84,
            spreadsheet.display_name: 0.60,
        }),
    ).plan(
        SkillPlanningRequest(
            query="apply last year's calculation and create a formal notice",
            task_intent=TaskIntent(
                objective="create a formal notice",
                domain=BusinessIntent.DOCUMENT_GENERATION,
                deliverables=["Word"],
            ),
            max_nodes=3,
        ),
        registry.list(),
    )

    assert {item.skill_id for item in plan.nodes} == {document.id, spreadsheet.id}


def test_plain_spreadsheet_task_selects_generic_sheet_skill_not_recruitment(tmp_path):
    registry = SkillRegistry(tmp_path / "unihr.db")
    generic = next(item for item in registry.list() if item.name == "build-reviewable-spreadsheet")
    recruitment = next(item for item in registry.list() if item.name == "screen-resumes-with-evidence")
    plan = SkillOrchestrator(
        semantic_retriever=_SemanticRetriever({
            generic.name: 0.80, recruitment.name: 0.74,
        }),
        candidate_reranker=_CandidateReranker({
            generic.display_name: 0.82,
            recruitment.display_name: 0.41,
        }),
    ).plan(
        SkillPlanningRequest(
            query="做一个三行数据的完成率 Excel",
            task_intent=TaskIntent(
                objective="生成完成率表", domain=BusinessIntent.SPREADSHEET_PROCESSING,
                deliverables=["Excel"],
            ),
            max_nodes=3,
        ),
        registry.list(),
    )

    assert [item.skill_id for item in plan.nodes] == [generic.id]
    assert recruitment.id not in {item.skill_id for item in plan.nodes}
    assert plan.missing_capabilities == []
