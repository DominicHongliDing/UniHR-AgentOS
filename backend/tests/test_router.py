import pytest

from app.domains.assistant.contracts import BusinessIntent, ExecutionRoute
from app.domains.assistant.router import LayeredRouter, extract_weather_location


@pytest.mark.parametrize(
    ("message", "intent", "route"),
    [
        ("你好", BusinessIntent.CONVERSATION, ExecutionRoute.CONVERSATION),
        ("你好，帮我查一下公司制度", BusinessIntent.POLICY_QUESTION, ExecutionRoute.HR_EVIDENCE),
        ("杭州今天天气怎么样", BusinessIntent.PUBLIC_CURRENT_INFO, ExecutionRoute.PUBLIC_INFO),
        ("请联网查浙江省人社厅最近的公开人事政策动态并附网页来源", BusinessIntent.PUBLIC_CURRENT_INFO, ExecutionRoute.PUBLIC_INFO),
        ("今天天气怎么样", BusinessIntent.PUBLIC_CURRENT_INFO, ExecutionRoute.CLARIFY),
        ("任务目标：今天是礼拜几，是不是台风很大", BusinessIntent.PUBLIC_CURRENT_INFO, ExecutionRoute.CLARIFY),
        ("外面风挺吓人的，今天到底周几，台风到哪儿了", BusinessIntent.PUBLIC_CURRENT_INFO, ExecutionRoute.CLARIFY),
        ("帮我筛选这十份简历", BusinessIntent.RECRUITMENT_SCREENING, ExecutionRoute.HR_WORKFLOW),
        ("用去年模板和今年数据生成一份公积金 Excel", BusinessIntent.SPREADSHEET_PROCESSING, ExecutionRoute.HR_WORKFLOW),
    ],
)
def test_layered_router(message, intent, route):
    decision = LayeredRouter().interpret(message)

    assert decision.business_intent == intent
    assert decision.execution_route == route


def test_recruitment_cannot_hide_inside_greeting():
    decision = LayeredRouter().interpret("你好，随便聊聊这批候选人简历怎么淘汰")

    assert decision.execution_route == ExecutionRoute.HR_WORKFLOW
    assert "candidate_pii" in decision.risk_flags


def test_current_conditions_without_location_keep_the_domain_and_request_location():
    decision = LayeredRouter().interpret("今天几号，台风到哪了，风大不大")

    assert decision.business_intent == BusinessIntent.PUBLIC_CURRENT_INFO
    assert decision.execution_route == ExecutionRoute.CLARIFY
    assert decision.missing_slots == ["location"]
    assert decision.suggested_capabilities == ["weather.current"]


def test_weather_location_removes_stacked_polite_request_prefixes():
    assert extract_weather_location("帮我查一下杭州今天的天气") == "杭州"


def test_recruitment_web_sourcing_keeps_domain_and_adds_web_tools():
    decision = LayeredRouter().interpret("请联网搜一下合适的简历")

    assert decision.business_intent == BusinessIntent.RECRUITMENT_SCREENING
    assert decision.execution_route == ExecutionRoute.CLARIFY
    assert decision.retrieval_mode.value == "web"
    assert decision.requires_freshness is True
    assert decision.required_capabilities == []
    assert decision.suggested_capabilities == [
        "web.search",
        "web.open",
        "recruitment.source",
        "file.extract",
        "recruitment.evaluate",
    ]
    assert decision.missing_slots == ["岗位名称或岗位要求", "允许搜索的公开或已授权来源"]


def test_public_recruitment_method_research_does_not_become_candidate_screening():
    decision = LayeredRouter().interpret(
        "请联网搜索适合高校招聘管理的公开方法，不要筛选候选人"
    )

    assert decision.business_intent == BusinessIntent.PUBLIC_CURRENT_INFO
    assert decision.execution_route == ExecutionRoute.PUBLIC_INFO
    assert decision.retrieval_mode.value == "web"


@pytest.mark.parametrize(
    ("message", "intent", "retrieval_mode", "capabilities"),
    [
        (
            "请根据内部知识库的薪酬政策并联网核对最新公开口径，生成一份 Word 通知",
            BusinessIntent.DOCUMENT_GENERATION,
            "hybrid",
            ["web.search", "web.open", "knowledge.retrieve", "document.compose", "artifact.create"],
        ),
        (
            "请联网查询浙江最新最低工资标准并生成 Excel 汇总表",
            BusinessIntent.SPREADSHEET_PROCESSING,
            "web",
            ["web.search", "web.open", "spreadsheet.inspect", "spreadsheet.transform", "artifact.create"],
        ),
    ],
)
def test_artifact_request_keeps_artifact_owner_and_composes_evidence_tools(
    message, intent, retrieval_mode, capabilities,
):
    decision = LayeredRouter().interpret(message)

    assert decision.business_intent == intent
    assert decision.execution_route == ExecutionRoute.HR_WORKFLOW
    assert decision.retrieval_mode.value == retrieval_mode
    assert decision.required_capabilities == capabilities
