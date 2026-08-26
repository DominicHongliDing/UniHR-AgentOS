from app.domains.assistant.contracts import (
    BusinessIntent, ExecutionMode, ExecutionRoute, TurnAction, TurnInterpretation,
)
from app.domains.assistant.rewrite_evaluation import RewriteEvaluationCase, evaluate_query_rewrite


class FixedRouter:
    def interpret(self, message, **kwargs):
        return TurnInterpretation(
            business_intent=BusinessIntent.RECRUITMENT_SCREENING,
            execution_route=ExecutionRoute.CONVERSATION,
            confidence=0.9,
            reason_code="test",
            matched_layer="governed_model_first:test",
            turn_action=TurnAction.COMPARE_EXISTING,
            execution_mode=ExecutionMode.REUSE_RESULT,
            resolved_request="基于产业拓展实习生已有排名，最终选择3名进入面试。",
        )


def test_rewrite_evaluator_measures_context_and_hard_fact_fidelity():
    report = evaluate_query_rewrite(FixedRouter(), [RewriteEvaluationCase(
        case_id="one", title="续问", message="那就定三个吧",
        active_context="岗位为产业拓展实习生，最终进入面试3名。",
        has_active_result=True, expected_intent="recruitment_screening",
        expected_action="compare_existing", expected_mode="reuse_result",
        expected_terms=["产业拓展", "3名"],
    )])

    assert report.status == "passed"
    assert report.context_recall == 1
    assert report.fidelity_rate == 1
    assert report.cases[0].unexpected_hard_facts == []


def test_rewrite_fidelity_understands_colloquial_selection_but_ignores_artifact_count():
    class ArtifactRouter(FixedRouter):
        def interpret(self, message, **kwargs):
            result = super().interpret(message, **kwargs)
            return result.model_copy(update={
                "business_intent": BusinessIntent.DOCUMENT_GENERATION,
                "turn_action": TurnAction.START_NEW,
                "execution_mode": ExecutionMode.EXECUTE,
                "resolved_request": "根据现有会议信息撰写一份会议通知。",
            })

    report = evaluate_query_rewrite(ArtifactRouter(), [RewriteEvaluationCase(
        case_id="artifact", title="公文", message="照这个出个通知",
        active_context="会议时间已确认。", expected_intent="document_generation",
        expected_action="start_new", expected_mode="execute", expected_terms=[],
    )])

    assert report.fidelity_rate == 1


def test_rewrite_evaluator_treats_chinese_and_arabic_people_counts_as_equivalent():
    report = evaluate_query_rewrite(FixedRouter(), [RewriteEvaluationCase(
        case_id="count-normalization", title="口语数量", message="那就定三个吧",
        active_context="岗位为产业拓展实习生。", has_active_result=True,
        expected_intent="recruitment_screening", expected_action="compare_existing",
        expected_mode="reuse_result", expected_terms=["3人"],
    )])

    assert report.context_recall == 1
    assert report.fidelity_rate == 1
