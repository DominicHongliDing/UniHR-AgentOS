from __future__ import annotations

from datetime import datetime, timezone
import json
import re

from langgraph.checkpoint.memory import InMemorySaver
from openpyxl import Workbook

from app.domains.context_lifecycle import ContextService
from app.domains.conversations.contracts import AttachmentRecord
from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal
from app.domains.resources import GovernedResourceQueryAgent, ResourceWorkspace
from app.domains.workbench import UploadPayload, WorkbenchService
from app.models.gateway import BailianModelGateway, ModelReply
from app.settings import Settings


class ToolPlanningGateway:
    supports_tools = True

    def __init__(self, calls: list[dict]):
        self.calls = calls
        self.invocations = []

    def complete(self, messages, **kwargs):
        self.invocations.append((messages, kwargs))
        if len(self.invocations) == 1:
            return ModelReply("", "qwen-test", 5, {}, tool_calls=self.calls)
        return ModelReply("定位完成", "qwen-test", 3, {})


class FakeToolResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_native",
                    "type": "function",
                    "function": {
                        "name": "resource_workspace_query",
                        "arguments": '{"resource_id":"att_native","page_start":2}',
                    },
                }],
            }}],
            "usage": {"total_tokens": 20},
        }


class FakeToolClient:
    def __init__(self):
        self.payload = {}

    def post(self, url, **kwargs):
        self.payload = kwargs["json"]
        return FakeToolResponse()


class WorkbenchToolGateway:
    supports_tools = True

    def __init__(self):
        self.resource_rounds = 0
        self.hr_answer_received = ""

    def complete(self, messages, **kwargs):
        purpose = kwargs.get("purpose")
        if purpose == "turn_interpretation":
            return ModelReply(json.dumps({
                "primary_intent": "conversation",
                "secondary_intents": [],
                "context_mode": "uses_conversation",
                "missing_inputs": [],
                "risk_level": "low",
                "data_class": "internal_hr",
                "required_capabilities": [],
                "confidence": 0.95,
                "reason_summary": "读取用户附件后回答",
                "turn_action": "start_new",
                "resolved_request": "请核对附件中的2026年执行规则",
                "retrieval_queries": [],
                "priority_constraints": [],
            }, ensure_ascii=False), "router", 5, {})
        if purpose == "resource_query_planning":
            self.resource_rounds += 1
            if self.resource_rounds == 1:
                index_text = str(messages[1]["content"])
                resource_id = re.search(r"att_[0-9a-f]+", index_text).group(0)
                return ModelReply("", "planner", 5, {}, tool_calls=[_tool_call({
                    "resource_id": resource_id, "query": "2026年执行规则", "max_chars": 1000,
                })])
            return ModelReply("已获得证据", "planner", 3, {})
        if purpose == "hr_answer":
            self.hr_answer_received = json.dumps(messages, ensure_ascii=False)
            return ModelReply("根据局部读取证据，规则自2026年7月执行。", "answer", 7, {})
        return ModelReply("{}", "fallback", 1, {})


def _principal() -> Principal:
    return Principal(
        user_id="user_resource", username="resource", display_name="Resource User",
        role="user", tenant_id="tenant_resource", permissions=[],
    )


def _tool_call(arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "resource_workspace_query", "arguments": arguments},
    }


def test_bailian_gateway_sends_and_parses_native_function_calls(tmp_path):
    client = FakeToolClient()
    gateway = BailianModelGateway(
        Settings(data_dir=tmp_path, model_api_key="test-key", primary_model="qwen-plus"),
        client=client,
    )
    tools = [{"type": "function", "function": {"name": "resource_workspace_query", "parameters": {"type": "object"}}}]

    reply = gateway.complete(
        [{"role": "user", "content": "读取第二页"}],
        purpose="resource_query_planning", tools=tools, tool_choice="auto",
    )

    assert client.payload["tools"] == tools
    assert client.payload["tool_choice"] == "auto"
    assert reply.content == ""
    assert reply.tool_calls[0]["function"]["arguments"] == {
        "resource_id": "att_native", "page_start": 2,
    }


def test_resource_query_agent_executes_excel_locator_and_enriches_context(tmp_path):
    principal = _principal()
    repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = repository.create_conversation("工具读取表格", principal)
    message = repository.add_message(conversation.id, "user", "读取调整额", principal=principal)
    path = tmp_path / "调整.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "测算"
    sheet.append(["姓名", "部门", "调整额"])
    sheet.append(["甲", "研发", 1200])
    sheet.append(["乙", "人事", 800])
    workbook.save(path)
    repository.add_attachment(AttachmentRecord(
        id="att_adjustment", conversation_id=conversation.id, message_id=message.id,
        display_name=path.name,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extension=".xlsx", byte_size=path.stat().st_size, saved_path=str(path),
        extracted_text="[Sheet: 测算]\n姓名 | 部门 | 调整额",
        summary={"kind": "workbook", "sheets": [{"name": "测算", "rows": 3, "columns": 3}]},
        created_at=datetime.now(timezone.utc).isoformat(),
    ), principal)
    context = ContextService(repository, max_chars=8_000).assemble(
        conversation.id, principal, query="调整额",
    )
    gateway = ToolPlanningGateway([_tool_call({
        "resource_id": "att_adjustment", "sheet": "测算", "cell_range": "B2:C3",
    })])

    outcome = GovernedResourceQueryAgent(
        gateway, ResourceWorkspace(repository), "qwen-test",
    ).enrich(
        request="请核对两名员工的调整额", conversation_id=conversation.id,
        principal=principal, context=context,
    )

    assert outcome.call_count == 1
    assert "研发 | 1200" in outcome.context.attachment_text
    assert "人事 | 800" in outcome.context.attachments[0].extracted_text
    assert any(item["event"] == "resource.query.executed" for item in outcome.trace)
    assert gateway.invocations[0][1]["tools"][0]["function"]["name"] == "resource_workspace_query"


def test_resource_query_agent_rejects_resource_outside_current_conversation(tmp_path):
    principal = _principal()
    repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = repository.create_conversation("越权请求", principal)
    message = repository.add_message(conversation.id, "user", "读取文件", principal=principal)
    path = tmp_path / "本地规则.txt"
    path.write_text("本对话文件", encoding="utf-8")
    repository.add_attachment(AttachmentRecord(
        id="att_local", conversation_id=conversation.id, message_id=message.id,
        display_name=path.name, mime_type="text/plain", extension=".txt",
        byte_size=path.stat().st_size, saved_path=str(path), extracted_text="本对话文件",
        summary={"kind": "text"}, created_at=datetime.now(timezone.utc).isoformat(),
    ), principal)
    context = ContextService(repository).assemble(conversation.id, principal)
    gateway = ToolPlanningGateway([_tool_call({"resource_id": "att_other", "line_start": 1})])

    outcome = GovernedResourceQueryAgent(
        gateway, ResourceWorkspace(repository), "qwen-test",
    ).enrich(
        request="读取其他文件", conversation_id=conversation.id,
        principal=principal, context=context,
    )

    assert outcome.call_count == 0
    assert outcome.context == context
    assert any(item["event"] == "resource.query.rejected" for item in outcome.trace)


def test_resource_query_agent_clamps_per_call_and_total_character_budget(tmp_path):
    principal = _principal()
    repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = repository.create_conversation("读取预算", principal)
    message = repository.add_message(conversation.id, "user", "查找规则", principal=principal)
    path = tmp_path / "规则.txt"
    body = "公积金调整规则与核验要求。" * 2_000
    path.write_text(body, encoding="utf-8")
    repository.add_attachment(AttachmentRecord(
        id="att_rules", conversation_id=conversation.id, message_id=message.id,
        display_name=path.name, mime_type="text/plain", extension=".txt",
        byte_size=path.stat().st_size, saved_path=str(path), extracted_text=body[:80_000],
        summary={"kind": "text", "characters": len(body)},
        created_at=datetime.now(timezone.utc).isoformat(),
    ), principal)
    context = ContextService(repository, max_chars=8_000).assemble(conversation.id, principal)
    gateway = ToolPlanningGateway([_tool_call({
        "resource_id": "att_rules", "line_start": 1, "line_end": 5000, "max_chars": 30000,
    })])

    outcome = GovernedResourceQueryAgent(
        gateway, ResourceWorkspace(repository), "qwen-test",
        max_chars_per_call=1_000, max_total_chars=1_500,
    ).enrich(
        request="读取规则", conversation_id=conversation.id,
        principal=principal, context=context,
    )

    assert outcome.call_count == 1
    assert outcome.characters <= 1_000
    executed = next(item for item in outcome.trace if item["event"] == "resource.query.executed")
    assert executed["locator"]["max_chars"] == 1_000


def test_workbench_uses_resource_function_call_before_general_attachment_answer(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "unihr.db",
        model_api_key="test-key",
    )
    repository = ConversationRepository(settings.database_path)
    gateway = WorkbenchToolGateway()
    workbench = WorkbenchService(
        settings, repository, model_gateway=gateway, workflow_checkpointer=InMemorySaver(),
    )

    response = workbench.handle_turn(
        message="请核对附件中的2026年执行规则",
        conversation_id=None,
        task_mode="自动选择",
        uploads=[UploadPayload(
            filename="执行规则.txt", mime_type="text/plain",
            content="第一条：一般规定。\n第二条：公积金调整自2026年7月执行。\n第三条：复核要求。".encode("utf-8"),
        )],
        document_font="宋体",
        document_font_size=14,
    )

    assert "2026年7月" in response.assistant_message.content
    assert "Function Call局部读取" in gateway.hr_answer_received
    assert any(item["event"] == "resource.query.executed" for item in response.trace)
    assert any(item["event"] == "resource.query.completed" for item in response.trace)
