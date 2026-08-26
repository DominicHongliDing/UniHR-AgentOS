from __future__ import annotations

from app.domains.capability_acceptance import default_capability_smoke_cases
from app.domains.capability_operations import default_routing_golden_cases

from .contracts import EvalCase, EvalExpectation, EvalSuite
from .repository import QualityRepository


def seed_default_quality_suites(repository: QualityRepository, tenant_id: str) -> None:
    existing_suites = {item.id: item for item in repository.list_suites(tenant_id)}
    existing = set(existing_suites)
    routing_suite_version = 2
    if (
        "routing-core" not in existing
        or existing_suites["routing-core"].version < routing_suite_version
    ):
        suite = repository.save_suite(EvalSuite(
            id="routing-core", tenant_id=tenant_id, name="路由与本轮动作黄金集",
            description="短表达、闲聊、公开信息、跨能力组合、结果续问与交付形态。",
            domain="routing", version=routing_suite_version, frozen=True,
        ))
        for item in default_routing_golden_cases():
            repository.save_case(EvalCase(
                id=item.case_id, tenant_id=tenant_id, suite_id=suite.id,
                suite_version=suite.version, title=item.title, prompt=item.prompt,
                active_context=item.active_context, has_active_result=item.has_active_result,
                expectation=EvalExpectation(
                    capability_id=item.expected_capability_id,
                    turn_action=item.expected_action.value if item.expected_action else "",
                    retrieval_mode=(
                        item.expected_retrieval_mode.value if item.expected_retrieval_mode else ""
                    ),
                    execution_mode=(
                        item.expected_execution_mode.value if item.expected_execution_mode else ""
                    ),
                    delivery_mode=(
                        item.expected_delivery_mode.value if item.expected_delivery_mode else ""
                    ),
                    answer_required=False,
                ),
                criticality="critical" if "isolation" in item.case_id else "standard",
                tags=["routing", "ordinary-prompt", "delivery-mode"],
                source="routing-golden-v2",
            ))
    if "capability-smoke" not in existing:
        suite = repository.save_suite(EvalSuite(
            id="capability-smoke", tenant_id=tenant_id, name="六能力真实交付",
            description="正式 Workbench 的路由、Graph、Tool、Artifact 和 Harness。",
            domain="cross_agent", version=1, frozen=True,
        ))
        for item in default_capability_smoke_cases():
            fixtures = (
                ["tests/fixtures/resume_qa_a.txt", "tests/fixtures/resume_qa_b.txt"]
                if item.capability == "recruitment" else []
            )
            repository.save_case(EvalCase(
                id=item.id, tenant_id=tenant_id, suite_id=suite.id,
                suite_version=suite.version, title=f"{item.capability} 简单任务",
                prompt=item.prompt, task_mode=item.task_mode,
                attachment_fixtures=fixtures,
                expectation=EvalExpectation(
                    business_intent=item.expected_route,
                    required_graph_nodes=item.required_graph_nodes,
                    required_trace_events=item.required_trace_events,
                    required_artifact_extensions=item.required_artifact_extensions,
                    harness_required=item.harness_required,
                ),
                criticality="critical", tags=[item.capability, "artifact-smoke"],
                source="capability-smoke-v1",
            ))
    prompt_suite_version = 2
    if (
        "prompt-variations" not in existing
        or existing_suites["prompt-variations"].version < prompt_suite_version
    ):
        suite = repository.save_suite(EvalSuite(
            id="prompt-variations", tenant_id=tenant_id, name="简单表达与变体压力集",
            description="同一意图的口语、省略、错别字、否定和跨领域表达。",
            domain="routing", version=prompt_suite_version, frozen=True,
        ))
        for case in _prompt_variation_cases(tenant_id, suite.id, suite.version):
            repository.save_case(case)
    adversarial_suite_version = 3
    if (
        "adversarial-core" not in existing
        or existing_suites["adversarial-core"].version < adversarial_suite_version
    ):
        suite = repository.save_suite(EvalSuite(
            id="adversarial-core", tenant_id=tenant_id, name="困难反例与不应调用",
            description="否定副作用、主题隔离、组合任务、闲聊和结果复用。",
            domain="cross_agent", version=adversarial_suite_version, frozen=True,
        ))
        for case in _adversarial_cases(tenant_id, suite.id, suite.version):
            repository.save_case(case)
    skill_suite_version = 1
    if (
        "skill-selection-core" not in existing
        or existing_suites["skill-selection-core"].version < skill_suite_version
    ):
        suite = repository.save_suite(EvalSuite(
            id="skill-selection-core",
            tenant_id=tenant_id,
            name="Skill 语义选择黄金集",
            description="用短句、口语、省略表达和反例验证 Skill 语义召回、候选重排与最终安全裁决。",
            domain="skill_selection",
            version=skill_suite_version,
            frozen=True,
        ))
        for case in _skill_selection_cases(tenant_id, suite.id, suite.version):
            repository.save_case(case)


def _prompt_variation_cases(tenant_id: str, suite_id: str, suite_version: int) -> list[EvalCase]:
    definitions = [
        ("weather-colloquial", "外头风是不是很大，宁波现在有预警没", "public_information", "web"),
        ("weather-short", "明天杭州下不下雨", "public_information", "web"),
        ("weather-typo", "杭州今天天汽咋样", "public_information", "web"),
        ("public-movie", "最近院线有啥值得看的", "public_information", "web"),
        ("policy-colloquial", "行政岗工资这套到底咋算", "policy_evidence", "internal"),
        ("policy-ellipsis", "刚才那个博士后办法还作数不", "policy_evidence", "internal"),
        ("policy-typo", "公积金调正按哪个政策", "policy_evidence", "internal"),
        ("sheet-colloquial", "给我整张培训盘子，出勤率自动算", "spreadsheet_processing", "none"),
        ("sheet-short", "做个能统计完成率的表", "spreadsheet_processing", "none"),
        ("document-colloquial", "写个下周开会的正式东西", "document_generation", "none"),
        ("document-short", "起草一份调岗通知", "document_generation", "none"),
        ("document-table", "做份待入职确认单，要能填人的表格", "document_generation", "none"),
        ("recruitment-colloquial", "这些人里给我挑仨靠谱的", "recruitment_screening", "attachments"),
        ("recruitment-short", "简历排一下", "recruitment_screening", "attachments"),
        ("recruitment-evidence", "谁更合适，理由给我能复核的", "recruitment_screening", "attachments"),
        ("skill-colloquial", "刚才这套以后老要用，给我存成流程", "skill_authoring", "none"),
        ("skill-explicit", "把这段对话做成一个Skill", "skill_authoring", "none"),
        ("chat-thanks", "行，谢了先这样", "general_conversation", "none"),
        ("product-help", "你这个东西到底能替HR干啥", "product_help", "none"),
    ]
    return [EvalCase(
        id=f"variant-{case_id}", tenant_id=tenant_id, suite_id=suite_id,
        suite_version=suite_version, title=case_id, prompt=prompt,
        expectation=EvalExpectation(
            capability_id=capability, retrieval_mode=retrieval, answer_required=False,
        ),
        criticality="standard", tags=["prompt-variation", capability],
        source="routing-golden-variations-v1",
    ) for case_id, prompt, capability, retrieval in definitions]


def _adversarial_cases(tenant_id: str, suite_id: str, suite_version: int) -> list[EvalCase]:
    return [
        EvalCase(
            id="negative-sheet-no-file", tenant_id=tenant_id, suite_id=suite_id,
            suite_version=suite_version, title="只分析、不生成文件",
            prompt="不要生成文件，只告诉我这份表可能哪里有问题",
            expectation=EvalExpectation(
                capability_id="general_conversation",
                delivery_mode="inline_response",
                forbidden_artifact_extensions=[".xlsx"], answer_required=True,
            ), criticality="critical", tags=["negative", "side-effect"],
            source="adversarial-workbench-v1",
        ),
        EvalCase(
            id="inline-polish-no-document", tenant_id=tenant_id, suite_id=suite_id,
            suite_version=suite_version, title="正式语气润色仍在聊天内交付",
            prompt=(
                "王老师，这个视频主任说提醒候选人认真准备，我们周四周五找专家面试，"
                "帮我润色一下，要有正式通知的感觉"
            ),
            expectation=EvalExpectation(
                capability_id="document_generation",
                delivery_mode="inline_transform",
                forbidden_artifact_extensions=[".docx"],
                answer_required=True,
            ), criticality="critical", tags=["delivery-mode", "inline-transform"],
            source="adversarial-workbench-v2",
        ),
        EvalCase(
            id="isolation-web-recruitment-method", tenant_id=tenant_id, suite_id=suite_id,
            suite_version=suite_version, title="招聘主题不等于简历筛选",
            prompt="请联网搜索适合高校招聘管理的公开方法，不要筛选候选人",
            expectation=EvalExpectation(
                capability_id="public_information", retrieval_mode="web", answer_required=False,
            ), criticality="critical", tags=["topic-isolation", "web"],
            source="routing-golden-adversarial-v1",
        ),
        EvalCase(
            id="no-op-after-result", tenant_id=tenant_id, suite_id=suite_id,
            suite_version=suite_version, title="礼貌结束不重跑",
            prompt="谢谢", active_context="上一轮已完成六位候选人的评分和排名。",
            has_active_result=True,
            expectation=EvalExpectation(
                capability_id="general_conversation", execution_mode="no_op",
                retrieval_mode="none", answer_required=False,
            ), criticality="critical", tags=["context", "no-op"],
            source="routing-golden-adversarial-v1",
        ),
        EvalCase(
            id="reuse-two-candidates", tenant_id=tenant_id, suite_id=suite_id,
            suite_version=suite_version, title="已有排名只选两人",
            prompt="那最后就定前两个人", active_context="上一轮已完成六位候选人的证据评分和排名。",
            has_active_result=True,
            expectation=EvalExpectation(
                capability_id="recruitment_screening", execution_mode="reuse_result",
                retrieval_mode="none", answer_required=False,
            ), criticality="critical", tags=["context", "reuse"],
            source="routing-golden-adversarial-v1",
        ),
        EvalCase(
            id="composite-policy-document", tenant_id=tenant_id, suite_id=suite_id,
            suite_version=suite_version, title="政策证据服务于公文产物",
            prompt="按内部调岗政策帮我写一份正式通知",
            expectation=EvalExpectation(
                capability_id="document_generation", retrieval_mode="internal",
                answer_required=False,
            ), criticality="standard", tags=["composite", "document"],
            source="routing-golden-adversarial-v1",
        ),
    ]


def _skill_selection_cases(
    tenant_id: str,
    suite_id: str,
    suite_version: int,
) -> list[EvalCase]:
    builtins = [
        "build-reusable-skill",
        "screen-resumes-with-evidence",
        "build-reviewable-spreadsheet",
        "answer-policy-with-evidence",
        "draft-hr-documents",
    ]
    definitions = [
        (
            "skill-semantic-spreadsheet",
            "把这批数据按给定规则处理，留一份能核对的表给我",
            ["build-reviewable-spreadsheet"],
            [],
            ["spreadsheet", "paraphrase"],
        ),
        (
            "skill-semantic-document",
            "弄一份领导可以签字确认的正式材料，人员信息做成可填写的小表格",
            ["draft-hr-documents"],
            [],
            ["document", "paraphrase"],
        ),
        (
            "skill-semantic-recruitment",
            "这些人先帮我排一下，理由要能让我逐条复核",
            ["screen-resumes-with-evidence"],
            [],
            ["recruitment", "ellipsis"],
        ),
        (
            "skill-semantic-policy",
            "行政岗这套待遇口径到底怎么落，依据给我标清楚",
            ["answer-policy-with-evidence"],
            [],
            ["policy", "colloquial"],
        ),
        (
            "skill-composite-sheet-notice",
            "按我给的计算规则更新这批数据，再给我出一份正式通知",
            ["build-reviewable-spreadsheet", "draft-hr-documents"],
            [],
            ["composite", "multi-skill"],
        ),
        (
            "skill-negative-weather",
            "宁波明天风大不大，出门要不要带伞",
            [],
            builtins,
            ["negative", "weather"],
        ),
        (
            "skill-negative-recruitment-web",
            "联网找一些高校招聘管理的公开做法，不要筛选候选人",
            [],
            ["screen-resumes-with-evidence"],
            ["negative", "topic-isolation"],
        ),
    ]
    return [
        EvalCase(
            id=case_id,
            tenant_id=tenant_id,
            suite_id=suite_id,
            suite_version=suite_version,
            title=case_id,
            prompt=prompt,
            expectation=EvalExpectation(
                required_skill_names=required,
                forbidden_skill_names=forbidden,
                skill_semantic_required=bool(required),
                skill_rerank_required=len(required) > 1,
                answer_required=False,
            ),
            criticality="critical" if forbidden or len(required) > 1 else "standard",
            tags=["skill-selection", *tags],
            source="skill-selection-golden-v1",
        )
        for case_id, prompt, required, forbidden, tags in definitions
    ]
