from __future__ import annotations

from app.domains.assistant.contracts import (
    BusinessIntent,
    ExecutionRoute,
    TurnInterpretation,
)
from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal
from app.domains.orchestration import (
    WorkflowCaseRepository,
    WorkflowCaseService,
    WorkflowCaseStatus,
    WorkflowHandoff,
    WorkflowPlanner,
    WorkflowStepStatus,
)
from app.domains.task_briefs import TaskBrief
from app.models.gateway import ModelGatewayError, ModelReply


class UnavailablePlannerGateway:
    def complete(self, *_args, **_kwargs):
        raise ModelGatewayError("planner_unavailable")


class PlannerGateway:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        import json

        return ModelReply(
            json.dumps(self.payload, ensure_ascii=False),
            kwargs.get("model") or "planner-test-model",
            1,
            {},
        )


def principal(user_id: str = "user_a", tenant_id: str = "tenant_a") -> Principal:
    return Principal(
        user_id=user_id,
        username=user_id,
        display_name="HR 用户",
        role="user",
        tenant_id=tenant_id,
        permissions=[],
    )


def recruitment_interpretation() -> TurnInterpretation:
    return TurnInterpretation(
        business_intent=BusinessIntent.RECRUITMENT_SCREENING,
        execution_route=ExecutionRoute.HR_WORKFLOW,
        confidence=0.95,
        reason_code="test",
        matched_layer="test",
    )


def test_legacy_workflow_handoff_is_upgraded_to_shared_contract():
    handoff = WorkflowHandoff.model_validate({
        "id": "handoff_legacy",
        "from_step_id": "screen_candidates",
        "to_step_id": "confirm_shortlist",
        "run_ids": ["run_legacy"],
        "artifact_ids": ["artifact_legacy"],
        "recruitment_case_id": "case_legacy",
        "created_at": "2026-08-01T00:00:00+00:00",
    })

    assert handoff.source_id == "screen_candidates"
    assert handoff.target_id == "confirm_shortlist"
    assert handoff.run_refs == ["run_legacy"]
    assert handoff.artifact_refs == ["artifact_legacy"]
    assert handoff.business_refs["recruitment_case_id"] == "case_legacy"


def test_vague_goal_is_planned_without_prompt_specific_fields(tmp_path):
    planner = WorkflowPlanner(UnavailablePlannerGateway(), "fake")
    brief = TaskBrief(objective="这些人先过一遍，我点头以后把面试材料和汇总表弄好")

    assert planner.should_plan(brief.objective, brief, recruitment_interpretation())
    plan = planner.plan(brief.objective, brief, recruitment_interpretation())

    assert plan.source == "governed_fallback"
    assert [step.agent.value for step in plan.steps] == [
        "recruitment", "human_review", "document", "spreadsheet",
    ]
    assert plan.steps[2].depends_on == ["confirm_shortlist"]
    assert plan.steps[3].depends_on == ["confirm_shortlist"]


def test_colloquial_cross_agent_goal_reaches_planner_without_agent_names():
    planner = WorkflowPlanner(UnavailablePlannerGateway(), "fake")
    message = "这批人你先过一下，我看完名单再往下，面试那套东西和台账别漏。"
    brief = TaskBrief(objective=message)

    assert planner.should_plan(message, brief, recruitment_interpretation())
    plan = planner.plan(message, brief, recruitment_interpretation())

    assert {step.agent.value for step in plan.steps} == {
        "recruitment", "human_review", "document", "spreadsheet",
    }


def test_implicit_handoff_language_is_planned_but_plain_screening_is_not():
    planner = WorkflowPlanner(UnavailablePlannerGateway(), "fake")
    implicit = "先替我看看这些人，能不能进下一轮我来定，定了以后资料你接着办。"
    plain = "帮我筛一下这些简历。"

    assert planner.should_plan(
        implicit, TaskBrief(objective=implicit), recruitment_interpretation(),
    )
    assert not planner.should_plan(
        plain, TaskBrief(objective=plain), recruitment_interpretation(),
    )


def test_model_first_planner_understands_unseen_wording_without_lexical_gate():
    gateway = PlannerGateway({
        "is_multi_agent": True,
        "title": "候选人后续协同",
        "objective": "完成人工确认后的后半程交付",
        "needs_document": True,
        "needs_spreadsheet": True,
        "rationale": "用户要求拍板后继续完成后半程业务交付。",
    })
    planner = WorkflowPlanner(gateway, "planner-test-model")
    message = "这些申请人的第一关先替我走，我拍板之后把后半程都接过去。"
    brief = TaskBrief(objective=message)

    assert not planner.should_plan(message, brief, recruitment_interpretation())
    plan = planner.plan_if_needed(message, brief, recruitment_interpretation())

    assert plan is not None
    assert plan.source == "model:planner-test-model"
    assert [step.agent.value for step in plan.steps] == [
        "recruitment", "human_review", "document", "spreadsheet",
    ]
    assert gateway.calls[0][1]["purpose"] == "workflow_planning"


def test_model_first_planner_does_not_expand_plain_screening_into_workflow():
    gateway = PlannerGateway({
        "is_multi_agent": False,
        "title": "单次筛选",
        "objective": "筛选候选人",
        "needs_document": False,
        "needs_spreadsheet": False,
        "rationale": "用户只要求筛选。",
    })
    planner = WorkflowPlanner(gateway, "planner-test-model")
    message = "帮我看看这批简历。"

    assert planner.plan_if_needed(
        message, TaskBrief(objective=message), recruitment_interpretation(),
    ) is None


def test_workflow_case_persists_handoff_and_parallel_agent_results(tmp_path):
    database = tmp_path / "unihr.db"
    conversations = ConversationRepository(database)
    owner = principal()
    conversation = conversations.create_conversation("招聘协同", owner)
    planner = WorkflowPlanner(UnavailablePlannerGateway(), "fake")
    brief = TaskBrief(objective="简历看一下，合适的后面材料和表一起做")
    plan = planner.plan(brief.objective, brief, recruitment_interpretation())
    service = WorkflowCaseService(WorkflowCaseRepository(database))

    item = service.start(plan, conversation.id, owner)
    assert item.status == WorkflowCaseStatus.RUNNING
    assert item.current_step_id == "screen_candidates"

    item = service.complete_step(
        item.id,
        "screen_candidates",
        owner,
        run_ids=["run_recruitment"],
        artifact_ids=["artifact_ranking"],
        recruitment_case_id="recruitment_case_1",
        summary="筛选完成",
    )
    assert item.status == WorkflowCaseStatus.WAITING_HUMAN
    assert item.current_step_id == "confirm_shortlist"
    assert item.handoffs[-1].to_step_id == "confirm_shortlist"
    first_handoff = item.handoffs[-1]
    assert first_handoff.contract_version == "unihr.handoff/v1"
    assert first_handoff.source_kind == "workflow_step"
    assert first_handoff.source_id == "screen_candidates"
    assert first_handoff.target_id == "confirm_shortlist"
    assert first_handoff.run_refs == ["run_recruitment"]
    assert first_handoff.artifact_refs == ["artifact_ranking"]
    assert first_handoff.business_refs == {
        "recruitment_case_id": "recruitment_case_1",
    }

    item = service.approve_human_step(
        item.id,
        owner,
        comment="确认前两名进入面试",
        selected_candidate_ids=["candidate_1", "candidate_2"],
    )
    running = {step.id for step in item.steps if step.status == WorkflowStepStatus.RUNNING}
    assert running == {"prepare_interview_document", "prepare_candidate_workbook"}
    assert {handoff.to_step_id for handoff in item.handoffs[-2:]} == running

    item = service.complete_active_step(
        item.id,
        "prepare_interview_document",
        owner,
        run_id="run_document",
        artifact_ids=["artifact_docx"],
        artifact_kinds=["document"],
        summary="Word 完成",
    )
    assert item.status == WorkflowCaseStatus.RUNNING
    item = service.complete_active_step(
        item.id,
        "prepare_candidate_workbook",
        owner,
        run_id="run_spreadsheet",
        artifact_ids=["artifact_xlsx"],
        artifact_kinds=["spreadsheet"],
        summary="Excel 完成",
    )
    assert item.status == WorkflowCaseStatus.SUCCEEDED
    assert item.current_step_id == ""
    assert item.artifact_ids == ["artifact_ranking", "artifact_docx", "artifact_xlsx"]

    reopened = WorkflowCaseService(WorkflowCaseRepository(database)).get(item.id, owner)
    assert reopened.status == WorkflowCaseStatus.SUCCEEDED
    assert len(reopened.handoffs) == 3
    assert all(item.contract_version == "unihr.handoff/v1" for item in reopened.handoffs)
    assert reopened.decision_log[0]["selected_candidate_ids"] == ["candidate_1", "candidate_2"]


def test_required_artifact_step_cannot_report_false_success(tmp_path):
    database = tmp_path / "unihr.db"
    conversations = ConversationRepository(database)
    owner = principal()
    conversation = conversations.create_conversation("招聘协同", owner)
    planner = WorkflowPlanner(UnavailablePlannerGateway(), "fake")
    brief = TaskBrief(objective="简历看一下，我确认后把面试材料和表一起做")
    service = WorkflowCaseService(WorkflowCaseRepository(database))
    item = service.start(
        planner.plan(brief.objective, brief, recruitment_interpretation()),
        conversation.id,
        owner,
    )
    item = service.complete_step(
        item.id,
        "screen_candidates",
        owner,
        run_ids=["run_recruitment"],
        artifact_ids=["artifact_ranking"],
        recruitment_case_id="recruitment_case_1",
        summary="筛选完成",
    )
    item = service.approve_human_step(
        item.id,
        owner,
        comment="确认当前建议",
        selected_candidate_ids=["candidate_1"],
    )

    try:
        service.complete_active_step(
            item.id,
            "prepare_interview_document",
            owner,
            run_id="run_document",
            artifact_ids=[],
            artifact_kinds=[],
            summary="执行但没有文件",
        )
    except ValueError as exc:
        assert str(exc) == "workflow_required_artifact_missing"
    else:  # pragma: no cover - explicit completion-contract assertion
        raise AssertionError("artifact-producing step was allowed to report false success")

    reopened = service.get(item.id, owner)
    document_step = next(step for step in reopened.steps if step.id == "prepare_interview_document")
    assert document_step.status == WorkflowStepStatus.RUNNING
    assert reopened.status == WorkflowCaseStatus.RUNNING


def test_parallel_branch_failure_isolated_and_retryable(tmp_path):
    database = tmp_path / "unihr.db"
    conversations = ConversationRepository(database)
    owner = principal()
    conversation = conversations.create_conversation("招聘协同", owner)
    planner = WorkflowPlanner(UnavailablePlannerGateway(), "fake")
    brief = TaskBrief(objective="简历先筛，我确认后把面试材料和台账一起做")
    service = WorkflowCaseService(WorkflowCaseRepository(database))
    item = service.start(
        planner.plan(brief.objective, brief, recruitment_interpretation()),
        conversation.id,
        owner,
    )
    item = service.complete_step(
        item.id,
        "screen_candidates",
        owner,
        artifact_ids=["ranking.xlsx"],
        recruitment_case_id="recruitment_case_1",
    )
    item = service.approve_human_step(
        item.id,
        owner,
        comment="确认",
        selected_candidate_ids=["candidate_1"],
    )

    item = service.fail_step(
        item.id,
        "prepare_interview_document",
        owner,
        "document_model_unavailable",
        run_id="run_document_failed",
    )
    assert item.status == WorkflowCaseStatus.RUNNING
    assert item.current_step_id == "prepare_candidate_workbook"
    item = service.complete_active_step(
        item.id,
        "prepare_candidate_workbook",
        owner,
        run_id="run_spreadsheet",
        artifact_ids=["workbook.xlsx"],
        artifact_kinds=["spreadsheet"],
        summary="Excel 完成",
    )
    assert item.status == WorkflowCaseStatus.FAILED
    assert item.current_step_id == "prepare_interview_document"

    item = service.retry_failed_steps(item.id, owner, comment="模型恢复后重试 Word")
    assert item.status == WorkflowCaseStatus.RUNNING
    item = service.complete_active_step(
        item.id,
        "prepare_interview_document",
        owner,
        run_id="run_document_retry",
        artifact_ids=["interview.docx"],
        artifact_kinds=["document"],
        summary="Word 完成",
    )
    assert item.status == WorkflowCaseStatus.SUCCEEDED
    assert item.artifact_ids == ["ranking.xlsx", "workbook.xlsx", "interview.docx"]


def test_artifact_file_cannot_complete_step_when_agent_quality_gate_failed(tmp_path):
    database = tmp_path / "unihr.db"
    conversations = ConversationRepository(database)
    owner = principal()
    conversation = conversations.create_conversation("跨 Agent 质量门", owner)
    planner = WorkflowPlanner(UnavailablePlannerGateway(), "fake")
    brief = TaskBrief(objective="简历先看，我确认以后准备面试材料和跟进表")
    service = WorkflowCaseService(WorkflowCaseRepository(database))
    item = service.start(
        planner.plan(brief.objective, brief, recruitment_interpretation()),
        conversation.id,
        owner,
    )
    item = service.complete_step(
        item.id,
        "screen_candidates",
        owner,
        artifact_ids=["ranking.xlsx"],
        recruitment_case_id="recruitment_case_1",
    )
    item = service.approve_human_step(
        item.id,
        owner,
        comment="确认",
        selected_candidate_ids=["candidate_1"],
    )

    try:
        service.complete_active_step(
            item.id,
            "prepare_interview_document",
            owner,
            run_id="run_document_exhausted",
            artifact_ids=["draft.docx"],
            artifact_kinds=["document"],
            run_status="exhausted",
            quality_issues=["artifact_harness_failed:document"],
            summary="文件存在但质量门失败",
        )
    except ValueError as exc:
        assert str(exc) == "workflow_agent_quality_gate_failed"
    else:  # pragma: no cover
        raise AssertionError("failed Agent Run was allowed to complete the workflow step")

    reopened = service.get(item.id, owner)
    document_step = next(step for step in reopened.steps if step.id == "prepare_interview_document")
    assert document_step.status == WorkflowStepStatus.RUNNING


def test_workflow_case_is_tenant_and_owner_isolated(tmp_path):
    database = tmp_path / "unihr.db"
    conversations = ConversationRepository(database)
    owner = principal()
    conversation = conversations.create_conversation("招聘协同", owner)
    planner = WorkflowPlanner(UnavailablePlannerGateway(), "fake")
    brief = TaskBrief(objective="筛简历，确认后给我面试材料和表格")
    item = WorkflowCaseService(WorkflowCaseRepository(database)).start(
        planner.plan(brief.objective, brief, recruitment_interpretation()),
        conversation.id,
        owner,
    )

    repository = WorkflowCaseRepository(database)
    for outsider in (principal("user_b", "tenant_a"), principal("user_a", "tenant_b")):
        try:
            repository.get(item.id, outsider)
        except KeyError:
            pass
        else:  # pragma: no cover - explicit isolation assertion
            raise AssertionError("workflow case leaked across its security scope")
