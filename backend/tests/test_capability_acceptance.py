from types import SimpleNamespace

from app.domains.capability_acceptance import (
    CapabilityAcceptanceEvaluator,
    CapabilityObservation,
    default_capability_smoke_cases,
    observe_skill_record,
    observe_workbench_turn,
)


def test_default_capability_smoke_cases_cover_six_user_visible_routes_with_simple_prompts():
    cases = default_capability_smoke_cases()

    assert [case.capability for case in cases] == [
        "policy", "document", "spreadsheet", "recruitment", "public_info", "skill",
    ]
    assert all(len(case.prompt) <= 36 for case in cases)
    assert all("请详细" not in case.prompt and "必须调用" not in case.prompt for case in cases)
    assert {case.priority for case in cases} == {"P0"}


def test_evaluator_reports_the_exact_failed_contract_instead_of_hiding_partial_success():
    observations = [
        CapabilityObservation(
            case_id="policy-simple-001", observed_route="policy_question",
            graph_path=["prepare", "execute", "quality_gate", "finalize"],
            trace_events=["policy.retrieve"], answer_visible=True,
        ),
        CapabilityObservation(
            case_id="document-simple-001", observed_route="document_generation",
            graph_path=["prepare", "execute", "quality_gate", "human_review_gate"],
            trace_events=["document.compose"], artifact_names=["会议通知.docx"],
            harness_passed=True, answer_visible=True,
        ),
        CapabilityObservation(
            case_id="spreadsheet-simple-001", observed_route="spreadsheet_processing",
            graph_path=["prepare", "execute", "quality_gate", "human_review_gate"],
            trace_events=["spreadsheet.create"], artifact_names=["招聘进度表.xlsx"],
            harness_passed=True, answer_visible=True,
        ),
        CapabilityObservation(
            case_id="recruitment-simple-001", observed_route="recruitment_screening",
            graph_path=["prepare", "execute", "quality_gate", "human_review_gate"],
            trace_events=["recruitment.evaluate"], artifact_names=["候选人报告.xlsx"],
            harness_passed=True, answer_visible=True,
        ),
        CapabilityObservation(
            case_id="public-info-simple-001", observed_route="public_current_info",
            graph_path=["prepare", "execute", "quality_gate", "finalize"],
            trace_events=["weather.current"], answer_visible=True,
        ),
        CapabilityObservation(
            case_id="skill-simple-001", observed_route="skill_create",
            graph_path=["author", "compile", "static_validate", "harness", "human_review_gate"],
            trace_events=["skill.draft.created"], harness_passed=True, answer_visible=True,
        ),
    ]

    report = CapabilityAcceptanceEvaluator().evaluate(observations)

    assert report.status == "failed"
    assert report.passed_count == 5
    assert report.pass_rate == 5 / 6
    policy = next(item for item in report.results if item.case_id == "policy-simple-001")
    assert policy.issues == ["missing_trace_event:query.rewritten"]


def test_workbench_adapter_observes_only_the_public_turn_response_contract():
    response = SimpleNamespace(
        interpretation=SimpleNamespace(business_intent=SimpleNamespace(value="document_generation")),
        run=SimpleNamespace(graph_path=["prepare", "execute", "quality_gate", "human_review_gate"]),
        trace=[
            {"event": "query.rewritten", "status": "completed"},
            {"event": "document.compose", "status": "completed"},
        ],
        artifacts=[SimpleNamespace(
            name="会议通知.docx",
            metadata={"harness": {"passed": True}},
        )],
        assistant_message=SimpleNamespace(content="会议通知已经生成，请人工复核。"),
    )

    observation = observe_workbench_turn("document-simple-001", response, duration_ms=321)

    assert observation.observed_route == "document_generation"
    assert observation.artifact_names == ["会议通知.docx"]
    assert observation.harness_passed is True
    assert observation.trace_statuses["document.compose"] == "completed"
    assert observation.answer_visible is True
    assert observation.duration_ms == 321


def test_required_tool_event_must_succeed_not_merely_appear_in_trace():
    evaluator = CapabilityAcceptanceEvaluator([
        next(case for case in default_capability_smoke_cases() if case.capability == "public_info")
    ])
    observation = CapabilityObservation(
        case_id="public-info-simple-001",
        observed_route="public_current_info",
        graph_path=["prepare", "execute", "quality_gate", "finalize"],
        trace_events=["weather.current"],
        trace_statuses={"weather.current": "failed"},
        answer_visible=True,
    )

    report = evaluator.evaluate([observation])

    assert report.status == "failed"
    assert report.results[0].issues == [
        "trace_event_not_completed:weather.current:failed"
    ]


def test_skill_adapter_observes_a_real_draft_and_authoring_graph_without_requiring_publish():
    record = SimpleNamespace(
        id="skill_demo",
        status="draft",
        display_name="每月招聘进度汇总",
        metadata={
            "authoring": {
                "status": "waiting_review",
                "graph_path": ["author", "compile", "static_validate", "harness", "human_review_gate"],
            },
            "validation": {"publishable": True},
            "eval_suite": {"status": "passed"},
        },
    )

    observation = observe_skill_record("skill-simple-001", record, duration_ms=450)

    assert observation.observed_route == "skill_create"
    assert observation.graph_path[-1] == "human_review_gate"
    assert observation.trace_events == ["skill.draft.created"]
    assert observation.harness_passed is True
    assert observation.answer_visible is True
