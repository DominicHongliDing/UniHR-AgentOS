from __future__ import annotations

from app.domains.context_lifecycle import ContextOperationsModule, ContextService
from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal
from app.models.gateway import ModelReply


class EvalGateway:
    supports_tools = True

    def complete(self, messages, **kwargs):
        prompt = messages[-1]["content"]
        if "B2:C6" in prompt:
            arguments = {"resource_id": "att_excel", "sheet": "测算", "cell_range": "B2:C6"}
        elif "第4页" in prompt:
            arguments = {"resource_id": "att_pdf", "page_start": 4, "page_end": 4}
        elif "基数上限" in prompt:
            arguments = {"resource_id": "att_large", "query": "公积金 基数 上限", "max_chars": 2000}
        elif "公开会议通知" in prompt:
            arguments = {"resource_id": "att_notice", "section": "会议安排"}
        else:
            return ModelReply("无需读取", "eval-model", 2, {})
        return ModelReply("", "eval-model", 3, {}, tool_calls=[{
            "id": "call_eval",
            "type": "function",
            "function": {"name": "resource_workspace_query", "arguments": arguments},
        }])


def _principal() -> Principal:
    return Principal(
        user_id="user_context_ops",
        username="context_ops",
        display_name="Context Ops",
        role="user",
        tenant_id="tenant_context_ops",
        permissions=[],
    )


def test_context_dashboard_reports_snapshots_hydration_and_resource_queries(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    principal = _principal()
    conversation = repository.create_conversation("上下文运营", principal)
    repository.add_message(conversation.id, "user", "请读取规则并回答", principal=principal)
    repository.add_message(
        conversation.id,
        "assistant",
        "已回答",
        trace=[
            {
                "event": "resource.query.executed",
                "status": "completed",
                "summary": "读取规则",
                "characters": 1200,
                "truncated": False,
            },
            {
                "event": "resource.query.rejected",
                "status": "failed",
                "summary": "拒绝越界资源",
            },
        ],
        principal=principal,
    )
    lifecycle = ContextService(repository, max_chars=4000)
    prepared = lifecycle.assemble(conversation.id, principal, query="规则")
    lifecycle.hydrate(lifecycle.to_checkpoint(prepared), principal)

    dashboard = ContextOperationsModule(repository, EvalGateway(), "eval-model").conversation_dashboard(
        conversation.id, principal
    )

    assert dashboard.snapshot_count == 1
    assert dashboard.raw_chars > 0
    assert dashboard.hydration_count == 1
    assert dashboard.resource_query.executed_calls == 1
    assert dashboard.resource_query.rejected_calls == 1
    assert dashboard.resource_query.characters == 1200
    assert dashboard.resource_query.locator_precision == 0.5


def test_resource_query_fixed_eval_checks_call_decision_resource_and_locator(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    report = ContextOperationsModule(repository, EvalGateway(), "eval-model").run_resource_query_eval()

    assert report.status == "passed"
    assert report.total == 6
    assert report.passed == 6
    assert report.decision_accuracy == 1
    assert report.resource_precision == 1
    assert report.locator_precision == 1
