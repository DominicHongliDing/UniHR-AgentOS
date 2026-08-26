import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.domains.assistant.context import ContextService
from app.domains.assistant.contracts import BusinessIntent
from app.domains.assistant.model_router import GovernedModelRouter
from app.domains.conversations.contracts import AttachmentRecord
from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal
from app.models.gateway import ModelGatewayError, ModelReply


class QueueGateway:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return ModelReply(self.replies.pop(0), kwargs.get("model") or "qwen3.7-flash", 12, {})


class UnavailableGateway:
    def complete(self, messages, **kwargs):
        raise ModelGatewayError("router_unavailable")


class EscalatingGateway(QueueGateway):
    settings = SimpleNamespace(router_fallback_model="qwen-plus")


def test_context_assembler_keeps_previous_turns_and_reports_usage(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = repository.create_conversation("上下文测试")
    repository.add_message(conversation.id, "user", "我负责博士后管理。")
    repository.add_message(conversation.id, "assistant", "已了解。")
    repository.add_message(conversation.id, "user", "我刚才说我负责什么？")

    prepared = ContextService(repository, max_chars=8_000).assemble(conversation.id)

    assert [item["role"] for item in prepared.messages] == ["user", "assistant", "user"]
    assert "博士后管理" in prepared.messages[0]["content"]
    assert "我刚才说" in prepared.compact_summary


def test_long_context_creates_versioned_checkpoint_and_preserves_exact_constraints(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = repository.create_conversation("长对话压缩测试")
    for index in range(14):
        if index == 0:
            repository.add_message(
                conversation.id,
                "user",
                "请完成2026年候选人复核，最终最多选择2名，禁止使用年龄作为依据。",
                task_brief={
                    "objective": "完成候选人复核",
                    "constraints": ["最终最多选择2名", "禁止使用年龄作为依据"],
                },
            )
        else:
            repository.add_message(
                conversation.id,
                "assistant" if index % 2 else "user",
                f"第{index}轮处理进展。" + ("核对证据、约束和审批状态。" * 45),
            )

    service = ContextService(repository, max_chars=8_000, model_gateway=UnavailableGateway())
    prepared = service.assemble(conversation.id)

    assert prepared.compaction_applied is True
    assert prepared.summary_id.startswith("ctxsum_")
    assert prepared.snapshot_id.startswith("ctxsnap_")
    assert prepared.messages[0]["role"] == "system"
    assert len(prepared.messages) == 5
    assert "2026年" in prepared.compact_summary
    assert "最多选择2名" in prepared.compact_summary
    assert "禁止使用年龄" in prepared.compact_summary
    assert prepared.fidelity_status == "fallback"
    usage = service.explain_usage(prepared.snapshot_id)
    assert usage["summary_id"] == prepared.summary_id
    assert usage["injected_chars"] < prepared.raw_chars + len(prepared.compact_summary)


def test_compaction_reuses_version_when_stable_source_is_unchanged(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = repository.create_conversation("检查点复用测试")
    for index in range(12):
        repository.add_message(conversation.id, "user" if index % 2 == 0 else "assistant", f"消息{index}：保留原始依据。")
    service = ContextService(repository, max_chars=4_000)

    first = service.assemble(conversation.id)
    second = service.assemble(conversation.id)

    assert first.summary_id == second.summary_id
    assert first.snapshot_id != second.snapshot_id


def test_fidelity_harness_rejects_invented_critical_values(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = repository.create_conversation("压缩幻觉测试")
    for index in range(12):
        repository.add_message(
            conversation.id,
            "user" if index % 2 == 0 else "assistant",
            f"2026年任务第{index}轮。" + ("核对政策依据和精确数字。" * 50),
        )
    gateway = QueueGateway([json.dumps({
        "task_goal": "完成2027年任务",
        "locked_constraints": [],
        "completed_steps": [],
        "confirmed_decisions": [],
        "open_questions": [],
        "failures": [],
        "next_step": "继续",
    }, ensure_ascii=False)])

    prepared = ContextService(repository, max_chars=4_000, model_gateway=gateway).assemble(conversation.id)

    assert prepared.fidelity_status == "fallback"
    assert "2027年" not in prepared.compact_summary
    assert "2026年" in prepared.compact_summary


def test_checkpoint_payload_offloads_attachment_content_and_hydrates_on_demand(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = repository.create_conversation("引用水合测试")
    message = repository.add_message(conversation.id, "user", "请读取工资表并生成复核结果。")
    extracted_text = "员工工资明细\n" + ("敏感数据行\n" * 4_000)
    repository.add_attachment(AttachmentRecord(
        id="att_large",
        conversation_id=conversation.id,
        message_id=message.id,
        display_name="工资明细.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extension=".xlsx",
        byte_size=123_456,
        saved_path=str(tmp_path / "工资明细.xlsx"),
        extracted_text=extracted_text,
        summary={"sheets": [{"name": "工资明细"}]},
        created_at=datetime.now(timezone.utc).isoformat(),
    ))
    service = ContextService(repository, max_chars=8_000)
    prepared = service.assemble(conversation.id)

    checkpoint = service.to_checkpoint(prepared)
    serialized = json.dumps(checkpoint, ensure_ascii=False)
    legacy = json.dumps({
        "messages": prepared.messages,
        "attachments": [item.model_dump(mode="json") for item in prepared.attachments],
        "attachment_text": prepared.attachment_text,
    }, ensure_ascii=False)

    assert checkpoint["schema_version"] == "context_ref_v1"
    assert "工资明细" not in serialized
    assert "extracted_text" not in serialized
    assert len(serialized) < len(legacy) / 20
    hydrated = ContextService(repository, max_chars=8_000).hydrate(checkpoint)
    assert hydrated.attachments[0].id == "att_large"
    assert "员工工资明细" in hydrated.attachment_text
    assert hydrated.messages == prepared.messages


def test_checkpoint_hydration_enforces_conversation_owner_scope(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    owner = Principal(
        user_id="user_a", username="a", display_name="A", role="user",
        tenant_id="tenant_a", permissions=[],
    )
    other = Principal(
        user_id="user_b", username="b", display_name="B", role="user",
        tenant_id="tenant_a", permissions=[],
    )
    conversation = repository.create_conversation("隔离测试", owner)
    repository.add_message(conversation.id, "user", "仅用户A可读取", principal=owner)
    service = ContextService(repository, max_chars=8_000)
    checkpoint = service.to_checkpoint(service.assemble(conversation.id, owner))

    try:
        service.hydrate(checkpoint, other)
    except KeyError:
        pass
    else:
        raise AssertionError("cross-user snapshot hydration must fail")


def test_context_uses_resource_index_then_query_relevant_excerpt(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = repository.create_conversation("资源渐进披露")
    message = repository.add_message(conversation.id, "user", "公积金")
    now = datetime.now(timezone.utc).isoformat()
    for resource_id, name, body in (
        ("att_salary", "薪酬制度.txt", "薪酬制度正文。" * 300),
        ("att_fund", "公积金政策.txt", "公积金调整依据。" * 300),
    ):
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        repository.add_attachment(AttachmentRecord(
            id=resource_id, conversation_id=conversation.id, message_id=message.id,
            display_name=name, mime_type="text/plain", extension=".txt",
            byte_size=path.stat().st_size, saved_path=str(path), extracted_text=body,
            summary={"kind": "text", "characters": len(body)}, created_at=now,
        ))

    service = ContextService(repository, max_chars=2_000)
    prepared = service.assemble(conversation.id, query="公积金")
    restored = ContextService(repository, max_chars=2_000).hydrate(service.to_checkpoint(prepared))

    assert "att_salary | 薪酬制度.txt" in prepared.attachment_text
    assert "att_fund | 公积金政策.txt" in prepared.attachment_text
    assert "[资源摘录：公积金政策.txt" in prepared.attachment_text
    assert "[资源摘录：薪酬制度.txt" not in prepared.attachment_text
    assert restored.attachment_text == prepared.attachment_text
    assert any(item["kind"] == "resource_index" for item in prepared.usage)


def test_short_context_is_not_compacted_when_compaction_has_no_benefit(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = repository.create_conversation("短对话收益门")
    for index in range(12):
        text = "预算上限20万元，最终最多2项。" if index == 0 else f"短消息{index}。"
        repository.add_message(conversation.id, "user" if index % 2 == 0 else "assistant", text)

    prepared = ContextService(repository, max_chars=36_000).assemble(conversation.id)

    assert prepared.compaction_applied is False
    assert prepared.injected_chars == prepared.raw_chars
    assert "20万元" in "\n".join(item["content"] for item in prepared.messages)


def test_rolling_checkpoint_preserves_early_facts_through_300_messages(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    conversation = repository.create_conversation("三百轮滚动检查点")
    for index in range(120):
        facts = "2026年任务，预算上限20万元，最终最多2项，不得使用年龄，地区待核实。" if index == 0 else ""
        repository.add_message(
            conversation.id,
            "user" if index % 2 == 0 else "assistant",
            facts + f"第{index}轮。" + ("核对政策、证据、公式和审批状态。" * 22),
        )
    service = ContextService(repository, max_chars=36_000)
    first = service.assemble(conversation.id)
    assert first.compaction_applied is True

    for index in range(120, 300):
        change = "预算上限调整为25万元，以最新为准。" if index == 150 else ""
        repository.add_message(
            conversation.id,
            "user" if index % 2 == 0 else "assistant",
            change + f"第{index}轮。" + ("继续核对政策、证据、公式和审批状态。" * 22),
        )

    second = service.assemble(conversation.id)
    restored = ContextService(repository, max_chars=36_000).hydrate(service.to_checkpoint(second))
    latest = service._find_summary_by_id(second.summary_id, conversation.id)

    assert latest is not None
    assert latest.version >= 2
    assert latest.parent_summary_id == first.summary_id
    assert latest.covered_message_count >= 296
    for fact in ("2026年", "20万元", "2项", "不得使用年龄", "地区待核实"):
        assert fact in second.compact_summary
        assert fact in restored.compact_summary
    assert "预算上限调整为25万元" in second.compact_summary
    assert "以这里较新的明确表述为准" in second.compact_summary


def test_retired_annual_intent_is_migrated_to_generic_spreadsheet():
    gateway = QueueGateway([
        json.dumps({
            "primary_intent": "annual_adjustment",
            "secondary_intents": ["policy_question"],
            "context_mode": "uses_conversation",
            "missing_inputs": [],
            "risk_level": "low",
            "data_class": "public",
            "required_capabilities": ["spreadsheet.transform", "unknown.tool"],
            "confidence": 0.94,
            "reason_summary": "需要按新年度生成表格",
        }, ensure_ascii=False)
    ])

    result = GovernedModelRouter(gateway).interpret("按 2026 年数据更新公积金表")

    assert result.business_intent == BusinessIntent.SPREADSHEET_PROCESSING
    assert result.selected_capability_id == "spreadsheet_processing"
    assert "employee_compensation" not in result.risk_flags
    assert "unknown.tool" not in result.required_capabilities
    assert result.matched_layer.startswith("governed_model_first")


def test_model_first_router_separates_domain_from_followup_action():
    gateway = QueueGateway([
        json.dumps({
            "primary_intent": "recruitment_screening",
            "secondary_intents": [],
            "context_mode": "uses_conversation",
            "missing_inputs": [],
            "risk_level": "high",
            "data_class": "restricted_hr",
            "required_capabilities": ["recruitment.evaluate"],
            "confidence": 0.97,
            "reason_summary": "用户要求比较已有前两名，不要求重跑筛选",
            "turn_action": "compare_existing",
        }, ensure_ascii=False)
    ])

    result = GovernedModelRouter(gateway).interpret(
        "前两名必须选一个，你建议谁？",
        task_mode="简历筛选",
        active_context="assistant: 已完成六人排名",
        has_active_result=True,
    )

    assert result.business_intent.value == "recruitment_screening"
    assert result.turn_action.value == "compare_existing"
    assert result.execution_mode.value == "reuse_result"
    assert result.execution_route.value == "conversation"
    assert "recruitment.evaluate" not in result.required_capabilities
    assert result.required_capabilities == ["context.read", "result.explain"]


def test_router_refuses_to_reuse_when_no_result_exists():
    gateway = QueueGateway([
        json.dumps({
            "primary_intent": "document_generation",
            "secondary_intents": [],
            "context_mode": "uses_conversation",
            "missing_inputs": [],
            "risk_level": "medium",
            "data_class": "internal_hr",
            "required_capabilities": [],
            "confidence": 0.93,
            "reason_summary": "用户要求确认已有公文",
            "turn_action": "confirm_existing",
        }, ensure_ascii=False)
    ])

    result = GovernedModelRouter(gateway).interpret(
        "就按上一版定稿",
        task_mode="公文材料",
        has_active_result=False,
    )

    assert result.turn_action.value == "clarify"
    assert result.execution_mode.value == "clarify"
    assert result.execution_route.value == "clarify"


def test_product_help_explanation_does_not_require_an_existing_business_result():
    gateway = QueueGateway([
        json.dumps({
            "primary_intent": "product_help",
            "secondary_intents": [],
            "context_mode": "standalone",
            "missing_inputs": [],
            "risk_level": "low",
            "data_class": "public",
            "required_capabilities": ["conversation.respond"],
            "confidence": 0.97,
            "reason_summary": "用户询问模型调用归属会话和招聘 Case 的产品机制",
            "turn_action": "explain_existing",
            "resolved_request": "请用一句话说明模型调用记录为什么需要归属到会话和招聘 Case。",
        }, ensure_ascii=False)
    ])

    result = GovernedModelRouter(gateway).interpret(
        "P0-F 前端验收：请用一句话说明模型调用记录为什么需要归属到会话和招聘 Case。",
        has_active_result=False,
    )

    assert result.business_intent.value == "product_help"
    assert result.turn_action.value == "start_new"
    assert result.execution_mode.value == "execute"
    assert result.execution_route.value == "conversation"


def test_product_capability_question_is_not_hijacked_by_scanning_keyword():
    message = (
        "P1-A Slice 3 产品验收：请用产品经理能听懂的话说明，"
        "为什么扫描政策需要固定视觉评测、后台入库任务和单文件失败重试？"
    )
    misclassified = json.dumps({
        "primary_intent": "image_understanding",
        "secondary_intents": [],
        "context_mode": "standalone",
        "missing_inputs": [],
        "risk_level": "low",
        "data_class": "public",
        "required_capabilities": ["vision.understand"],
        "confidence": 0.91,
        "reason_summary": "用户提到了扫描政策",
        "turn_action": "start_new",
        "resolved_request": message,
        "retrieval_queries": [],
        "priority_constraints": [],
    }, ensure_ascii=False)

    model_result = GovernedModelRouter(QueueGateway([misclassified])).interpret(message)
    fallback_result = GovernedModelRouter(UnavailableGateway()).interpret(message)

    for result in (model_result, fallback_result):
        assert result.business_intent.value == "product_help"
        assert result.execution_route.value == "conversation"
        assert result.required_capabilities == []
        assert result.execution_mode.value == "execute"


def test_fallback_reuses_active_recruitment_result_for_final_candidate_selection():
    result = GovernedModelRouter(UnavailableGateway()).interpret(
        "请选择两名最终的候选人",
        task_mode="简历筛选",
        active_context="assistant: 已完成六位候选人的证据评分和排名",
        has_active_result=True,
    )

    assert result.business_intent.value == "recruitment_screening"
    assert result.turn_action.value == "compare_existing"
    assert result.execution_mode.value == "reuse_result"
    assert result.execution_route.value == "conversation"


def test_fallback_uses_context_for_short_referential_followup():
    result = GovernedModelRouter(UnavailableGateway()).interpret(
        "那就定三个吧",
        active_context="user: 岗位为产业拓展实习生，最终进入面试3名；assistant: 已完成6位候选人的证据评分和排名。",
        has_active_result=True,
    )

    assert result.business_intent.value == "recruitment_screening"
    assert result.turn_action.value == "compare_existing"
    assert result.execution_mode.value == "reuse_result"


def test_fallback_genuinely_unclear_request_asks_for_clarification():
    result = GovernedModelRouter(UnavailableGateway()).interpret("帮我处理一下")

    assert result.business_intent.value == "unclear"
    assert result.turn_action.value == "clarify"
    assert result.execution_mode.value == "clarify"


def test_question_about_retry_mechanism_is_not_a_regenerate_action():
    gateway = QueueGateway([json.dumps({
        "primary_intent": "product_help", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": [], "risk_level": "low",
        "data_class": "public", "required_capabilities": [], "confidence": 0.9,
        "reason_summary": "询问失败重试机制", "turn_action": "start_new",
        "resolved_request": "解释系统遇到材料读取失败时是否会自动重试。",
        "retrieval_queries": [], "priority_constraints": [],
    }, ensure_ascii=False)])

    result = GovernedModelRouter(gateway).interpret("材料读失败了，会不会自己一直重跑？")

    assert result.business_intent.value == "product_help"
    assert result.turn_action.value == "start_new"
    assert result.execution_mode.value == "execute"


def test_model_cannot_turn_final_selection_into_a_mutating_rerun():
    gateway = QueueGateway([json.dumps({
        "primary_intent": "recruitment_screening", "secondary_intents": [],
        "context_mode": "uses_conversation", "missing_inputs": [], "risk_level": "high",
        "data_class": "restricted_hr", "required_capabilities": ["recruitment.evaluate"],
        "confidence": 0.91, "reason_summary": "模型把最终选择误判为修改排名",
        "turn_action": "revise_existing", "resolved_request": "从已有排名中最终确定3名",
        "retrieval_queries": [], "priority_constraints": ["最终3名"],
    }, ensure_ascii=False)])

    result = GovernedModelRouter(gateway).interpret(
        "那就定三个吧", active_context="assistant: 已完成六人排名", has_active_result=True,
    )

    assert result.turn_action.value == "compare_existing"
    assert result.execution_mode.value == "reuse_result"
    assert result.execution_route.value == "conversation"


def test_explicit_spreadsheet_mode_respects_plain_language_read_only_guard():
    decision = json.dumps({
        "primary_intent": "spreadsheet_processing", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": [], "risk_level": "low",
        "data_class": "internal_hr", "required_capabilities": ["spreadsheet.inspect"],
        "confidence": 0.9, "reason_summary": "检查表格", "turn_action": "start_new",
        "resolved_request": "检查表格异常但不要修改", "retrieval_queries": [],
        "priority_constraints": [],
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "看看这张表哪里不对，先别改", task_mode="表格处理",
    )

    assert result.business_intent.value == "conversation"
    assert result.execution_route.value == "conversation"


def test_router_rewrite_preserves_user_numbers_and_priority_constraints():
    gateway = QueueGateway([
        json.dumps({
            "primary_intent": "recruitment_screening",
            "secondary_intents": [],
            "context_mode": "uses_conversation",
            "missing_inputs": [],
            "risk_level": "high",
            "data_class": "restricted_hr",
            "required_capabilities": [],
            "confidence": 0.95,
            "reason_summary": "基于已有排名选择最终人选",
            "turn_action": "compare_existing",
            "resolved_request": "基于已有六人排名选择最终候选人",
            "retrieval_queries": ["产业拓展候选人排名"],
            "priority_constraints": ["最终选择2名"],
        }, ensure_ascii=False)
    ])

    result = GovernedModelRouter(gateway).interpret(
        "请选择2名最终候选人",
        task_mode="简历筛选",
        active_context="assistant: 已完成六人排名",
        has_active_result=True,
    )

    assert result.resolved_request == "请选择2名最终候选人"
    assert result.retrieval_queries[0] == "请选择2名最终候选人"
    assert result.priority_constraints == ["最终选择2名"]


def test_attachment_information_extraction_cannot_be_escalated_to_document_generation():
    gateway = QueueGateway([
        json.dumps({
            "primary_intent": "document_generation",
            "secondary_intents": [],
            "context_mode": "standalone",
            "missing_inputs": [],
            "risk_level": "medium",
            "data_class": "internal_hr",
            "required_capabilities": ["document.compose", "artifact.create"],
            "confidence": 0.91,
            "reason_summary": "读取附件中的字段",
            "turn_action": "start_new",
            "resolved_request": "读取附件并列出目标年度、预算上限、最终项数和待核实条件",
            "retrieval_queries": [],
            "priority_constraints": [],
        }, ensure_ascii=False),
    ])

    result = GovernedModelRouter(gateway).interpret(
        "请读取附件并列出目标年度、预算上限、最终最多保留几项、禁止条件和待核实条件。",
        attachment_summaries=[{"display_name": "验收资料.txt", "extension": ".txt"}],
    )

    assert result.business_intent.value == "conversation"
    assert result.execution_route.value == "conversation"
    assert result.required_capabilities == []
    assert result.execution_mode.value == "execute"


def test_inline_text_transformations_never_inherit_artifact_side_effects():
    overreaching = json.dumps({
        "primary_intent": "document_generation",
        "secondary_intents": [], "context_mode": "standalone", "missing_inputs": [],
        "risk_level": "medium", "data_class": "internal_hr",
        "required_capabilities": ["document.compose", "artifact.create"],
        "confidence": 0.93, "reason_summary": "误把文字风格当成文件交付",
        "turn_action": "start_new", "delivery_mode": "create_artifact",
        "resolved_request": "改写用户提供的文字", "retrieval_queries": [],
        "priority_constraints": [],
    }, ensure_ascii=False)
    messages = [
        "把下面这段话换个更礼貌的说法",
        "帮我校对这段内容，直接给修改版",
        "把这段英文翻译成正式中文",
        "请精简这段通知措辞，意思不要变",
    ]

    for message in messages:
        result = GovernedModelRouter(QueueGateway([overreaching])).interpret(message)
        assert result.business_intent.value == "conversation"
        assert result.delivery_mode.value == "inline_transform"
        assert result.execution_route.value == "conversation"
        assert result.required_capabilities == []
        assert "artifact.create" not in result.suggested_capabilities


def test_explicit_word_delivery_preserves_document_artifact_route():
    decision = json.dumps({
        "primary_intent": "document_generation",
        "secondary_intents": [], "context_mode": "standalone", "missing_inputs": [],
        "risk_level": "medium", "data_class": "internal_hr",
        "required_capabilities": ["document.compose", "artifact.create"],
        "confidence": 0.96, "reason_summary": "用户明确要求 Word 文件",
        "turn_action": "start_new", "delivery_mode": "create_artifact",
        "resolved_request": "润色内容并生成 Word 文件", "retrieval_queries": [],
        "priority_constraints": [],
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "帮我润色这段文字并生成一个可下载的 Word 文件"
    )

    assert result.business_intent.value == "document_generation"
    assert result.delivery_mode.value == "create_artifact"
    assert result.execution_route.value == "hr_workflow"
    assert "artifact.create" in result.required_capabilities


def test_explicit_word_output_contract_overrides_model_policy_misroute():
    wrong_decision = json.dumps({
        "primary_intent": "policy_question",
        "secondary_intents": [], "context_mode": "standalone", "missing_inputs": [],
        "risk_level": "medium", "data_class": "internal_hr",
        "required_capabilities": ["knowledge.retrieve", "evidence.answer"],
        "confidence": 0.91, "reason_summary": "模型把通知主题误当成政策问题",
        "turn_action": "start_new", "delivery_mode": "inline_response",
        "resolved_request": "说明入职材料核验要求", "retrieval_queries": [],
        "priority_constraints": [],
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([wrong_decision] * 3)).interpret(
        "请生成一份正式 Word 通知：标题《关于开展2026年新员工入职材料核验工作的通知》。"
    )

    assert result.business_intent.value == "document_generation"
    assert result.delivery_mode.value == "create_artifact"
    assert result.execution_route.value == "hr_workflow"
    assert result.required_capabilities == ["document.compose", "artifact.create"]


def test_inline_draft_does_not_inherit_formal_document_required_fields():
    decision = json.dumps({
        "primary_intent": "document_generation",
        "secondary_intents": [], "context_mode": "uses_conversation",
        "missing_inputs": ["具体时间", "会议地点", "专家名单", "候选人名单"],
        "risk_level": "medium", "data_class": "internal_hr",
        "required_capabilities": ["document.compose", "artifact.create"],
        "confidence": 0.92, "reason_summary": "生成通知正文",
        "turn_action": "clarify", "delivery_mode": "inline_response",
        "resolved_request": "生成一段简洁正式的专家面试通知正文",
        "retrieval_queries": [], "priority_constraints": ["不要创建文件"],
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "先别生成 Word，也不要创建任何文件，只在聊天里给我一段简洁正式的通知正文。",
        active_context="上一条要求：明天下午安排专家面试，并提醒候选人认真准备。",
    )

    assert result.business_intent.value == "conversation"
    assert result.delivery_mode.value == "inline_response"
    assert result.execution_mode.value == "execute"
    assert result.execution_route.value == "conversation"
    assert result.missing_slots == []
    assert result.required_capabilities == []


def test_read_only_guard_covers_spreadsheets_but_preserves_explicit_artifact_requests():
    spreadsheet_decision = json.dumps({
        "primary_intent": "spreadsheet_processing",
        "secondary_intents": [], "context_mode": "standalone", "missing_inputs": [],
        "risk_level": "low", "data_class": "internal_hr",
        "required_capabilities": ["spreadsheet.inspect", "spreadsheet.transform"],
        "confidence": 0.9, "reason_summary": "查看表格字段", "turn_action": "start_new",
        "resolved_request": "读取Excel并列出所有列名", "retrieval_queries": [],
        "priority_constraints": [],
    }, ensure_ascii=False)
    document_decision = json.dumps({
        "primary_intent": "document_generation",
        "secondary_intents": [], "context_mode": "standalone", "missing_inputs": [],
        "risk_level": "medium", "data_class": "internal_hr",
        "required_capabilities": ["document.compose", "artifact.create"],
        "confidence": 0.94, "reason_summary": "生成会议通知", "turn_action": "start_new",
        "resolved_request": "根据附件生成正式会议通知", "retrieval_queries": [],
        "priority_constraints": [],
    }, ensure_ascii=False)

    read_only = GovernedModelRouter(QueueGateway([spreadsheet_decision])).interpret(
        "读取这个Excel并列出所有列名。",
        attachment_summaries=[{"display_name": "人员表.xlsx"}],
    )
    generated = GovernedModelRouter(QueueGateway([document_decision])).interpret(
        "请根据附件生成一份正式会议通知。",
        attachment_summaries=[{"display_name": "会议安排.txt"}],
    )
    explicit = GovernedModelRouter(QueueGateway([document_decision])).interpret(
        "读取附件中的安排并列出来。",
        task_mode="公文材料",
        attachment_summaries=[{"display_name": "会议安排.txt"}],
    )

    assert read_only.business_intent.value == "conversation"
    assert read_only.required_capabilities == []
    assert generated.business_intent.value == "document_generation"
    assert "artifact.create" in generated.required_capabilities
    assert explicit.business_intent.value == "document_generation"


def test_explicit_negative_artifact_instruction_overrides_generation_keyword():
    decision = json.dumps({
        "primary_intent": "document_generation",
        "secondary_intents": [], "context_mode": "standalone", "missing_inputs": [],
        "risk_level": "medium", "data_class": "internal_hr",
        "required_capabilities": ["document.compose", "artifact.create"],
        "confidence": 0.95, "reason_summary": "模型误识别了生成文件关键词",
        "turn_action": "start_new", "resolved_request": "读取预算上限和禁止条件",
        "retrieval_queries": [], "priority_constraints": [],
    }, ensure_ascii=False)
    message = "请读取附件中预算上限和禁止条件并告诉我，不要生成文件。"

    automatic = GovernedModelRouter(QueueGateway([decision])).interpret(
        message, attachment_summaries=[{"display_name": "验收.txt"}],
    )
    explicit_mode = GovernedModelRouter(QueueGateway([decision])).interpret(
        message, task_mode="公文材料", attachment_summaries=[{"display_name": "验收.txt"}],
    )

    assert automatic.business_intent.value == "conversation"
    assert automatic.required_capabilities == []
    assert explicit_mode.business_intent.value == "conversation"
    assert explicit_mode.required_capabilities == []


def test_content_safety_constraint_does_not_cancel_explicit_document_generation():
    decision = json.dumps({
        "primary_intent": "document_generation",
        "secondary_intents": [], "context_mode": "new_input", "missing_inputs": [],
        "risk_level": "medium", "data_class": "restricted_hr",
        "required_capabilities": ["document.compose", "artifact.create"],
        "confidence": 0.95, "reason_summary": "生成面试确认材料",
        "turn_action": "start_new", "resolved_request": "生成人工确认后的面试材料",
        "retrieval_queries": [], "priority_constraints": ["不得写入受保护属性"],
    }, ensure_ascii=False)
    message = (
        "请生成人工确认后的 Word 面试材料；仅使用确认快照中的事实，"
        "不得写入受保护属性，不得表述为自动录用决定。"
    )

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        message,
        task_mode="公文材料",
    )

    assert result.business_intent.value == "document_generation"
    assert "artifact.create" in result.required_capabilities


def test_negative_artifact_instruction_does_not_require_an_attachment_read_request():
    decision = json.dumps({
        "primary_intent": "document_generation", "secondary_intents": [],
        "context_mode": "new_input", "missing_inputs": [], "risk_level": "medium",
        "data_class": "internal_hr", "required_capabilities": ["document.compose", "artifact.create"],
        "confidence": 0.95, "reason_summary": "Prepare a meeting notice.",
        "turn_action": "start_new", "resolved_request": "Generate a meeting notice now.",
        "retrieval_queries": [], "priority_constraints": [],
    })
    message = (
        "我要准备一个会议通知。会议时间是2026年8月12日上午9点，"
        "地点是行政楼第一会议室。先记住这些信息，不要生成文件。"
    )

    result = GovernedModelRouter(QueueGateway([decision])).interpret(message)

    assert result.business_intent.value == "conversation"
    assert result.required_capabilities == []
    assert result.execution_route.value == "conversation"


def test_question_about_a_published_notice_is_policy_retrieval_not_document_generation():
    decision = json.dumps({
        "primary_intent": "document_generation", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": [], "risk_level": "medium",
        "data_class": "internal_hr", "required_capabilities": ["document.compose", "artifact.create"],
        "confidence": 0.91, "reason_summary": "误把来源名称中的通知当成公文生成",
        "turn_action": "start_new", "resolved_request": "回答扫描政策通知中的会议要素",
        "retrieval_queries": ["扫描政策通知 会议要素"], "priority_constraints": ["不要猜"],
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "根据知识库中刚发布的扫描政策通知，会议日期、地点和参会人数分别是什么？不要猜。"
    )

    assert result.business_intent.value == "policy_question"
    assert result.execution_route.value == "hr_evidence"
    assert "knowledge.retrieve" in result.required_capabilities
    assert "artifact.create" not in result.required_capabilities

    fallback_result = GovernedModelRouter(UnavailableGateway()).interpret(
        "根据知识库中刚发布的扫描政策通知，会议日期、地点和参会人数分别是什么？不要猜。"
    )
    assert fallback_result.business_intent.value == "policy_question"
    assert fallback_result.execution_route.value == "hr_evidence"


def test_colloquial_policy_applicability_question_overrides_product_help_misclassification():
    misclassified = json.dumps({
        "primary_intent": "product_help", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": [], "risk_level": "medium",
        "data_class": "internal_hr", "required_capabilities": [], "confidence": 0.81,
        "reason_summary": "误把口语中的直接按理解为产品功能",
        "turn_action": "start_new",
        "resolved_request": "询问工作站博士后管理细则是否仍可执行",
        "retrieval_queries": ["工作站博士后 管理细则 现行 有效"],
        "priority_constraints": [],
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([misclassified])).interpret(
        "工作站博士后管理细则现在还能直接按吗？"
    )

    assert result.business_intent.value == "policy_question"
    assert result.execution_route.value == "hr_evidence"
    assert "knowledge.retrieve" in result.required_capabilities


def test_policy_information_request_overrides_product_help_misclassification():
    misclassified = json.dumps({
        "primary_intent": "product_help", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": [], "risk_level": "medium",
        "data_class": "internal_hr", "required_capabilities": ["conversation.respond"],
        "confidence": 0.86, "reason_summary": "误把任务目标当作产品能力咨询",
        "turn_action": "start_new",
        "resolved_request": "了解行政管理岗位的薪酬方案",
        "retrieval_queries": [], "priority_constraints": [],
    }, ensure_ascii=False)

    message = "任务目标：我想了解行政管理岗的薪酬方案"
    result = GovernedModelRouter(QueueGateway([misclassified])).interpret(message)

    assert result.business_intent.value == "policy_question"
    assert result.execution_route.value == "hr_evidence"
    assert "knowledge.retrieve" in result.required_capabilities
    fallback_result = GovernedModelRouter(UnavailableGateway()).interpret(message)
    assert fallback_result.business_intent.value == "policy_question"
    assert fallback_result.execution_route.value == "hr_evidence"


def test_explicit_public_web_request_overrides_policy_topic_misclassification():
    misclassified = json.dumps({
        "primary_intent": "policy_question", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": [], "risk_level": "low",
        "data_class": "public", "required_capabilities": ["knowledge.retrieve"],
        "confidence": 0.86, "reason_summary": "主题包含政策，因此误分到内部知识库",
        "turn_action": "start_new",
        "resolved_request": "联网查询浙江省人社厅公开政策动态",
        "retrieval_queries": ["浙江省人社厅 政策动态"], "priority_constraints": [],
    }, ensure_ascii=False)
    message = "请联网查一下浙江省人社厅最近发布的公开人事政策动态，并附网页来源。"

    result = GovernedModelRouter(QueueGateway([misclassified])).interpret(message)

    assert result.business_intent.value == "public_current_info"
    assert result.execution_route.value == "public_info"
    assert result.required_capabilities == ["web.search", "web.open"]
    fallback_result = GovernedModelRouter(UnavailableGateway()).interpret(message)
    assert fallback_result.business_intent.value == "public_current_info"
    assert fallback_result.execution_route.value == "public_info"


def test_recruitment_web_request_composes_domain_and_web_capability():
    misclassified = json.dumps({
        "primary_intent": "public_current_info", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": [], "risk_level": "low",
        "data_class": "public", "required_capabilities": ["web.search", "web.open"],
        "confidence": 0.87, "reason_summary": "用户要求联网，因此误分到通用联网信息",
        "turn_action": "start_new", "resolved_request": "联网搜索合适的简历",
        "retrieval_queries": ["合适的简历"], "priority_constraints": [],
    }, ensure_ascii=False)
    message = "请联网搜一下合适的简历"

    result = GovernedModelRouter(QueueGateway([misclassified])).interpret(message)

    assert result.business_intent.value == "recruitment_screening"
    assert result.execution_route.value == "clarify"
    assert result.retrieval_mode.value == "web"
    assert result.required_capabilities == []
    assert result.suggested_capabilities == [
        "web.search", "web.open", "recruitment.source", "file.extract", "recruitment.evaluate",
    ]
    assert result.missing_slots == ["岗位名称或岗位要求", "允许搜索的公开或已授权来源"]

    fallback_result = GovernedModelRouter(UnavailableGateway()).interpret(message)
    assert fallback_result.business_intent.value == "recruitment_screening"
    assert fallback_result.execution_route.value == "clarify"
    assert fallback_result.retrieval_mode.value == "web"


def test_grounded_document_request_overrides_public_info_misclassification():
    misclassified = json.dumps({
        "primary_intent": "public_current_info", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": [], "risk_level": "low",
        "data_class": "public", "required_capabilities": ["web.search"],
        "confidence": 0.84, "reason_summary": "只识别了联网要求",
        "turn_action": "start_new", "resolved_request": "查询最新政策",
        "retrieval_queries": ["最新政策"], "priority_constraints": [],
    }, ensure_ascii=False)
    message = "根据内部知识库并联网核对最新公开口径，生成一份 Word 通知。"

    result = GovernedModelRouter(QueueGateway([misclassified])).interpret(message)

    assert result.business_intent.value == "document_generation"
    assert result.execution_route.value == "hr_workflow"
    assert result.retrieval_mode.value == "hybrid"
    assert result.required_capabilities == [
        "web.search", "web.open", "knowledge.retrieve", "document.compose", "artifact.create",
    ]


def test_concrete_domain_question_without_missing_slots_does_not_clarify():
    decision = json.dumps({
        "primary_intent": "policy_question", "secondary_intents": [],
        "context_mode": "uses_conversation", "missing_inputs": [], "risk_level": "low",
        "data_class": "internal_hr", "required_capabilities": ["knowledge.retrieve"],
        "confidence": 0.9, "reason_summary": "The referenced contribution base is clear from context.",
        "turn_action": "clarify", "resolved_request": "Explain how the housing-fund contribution base is calculated.",
        "retrieval_queries": ["housing fund contribution base calculation"], "priority_constraints": [],
    })

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "那这个基数到底怎么算？",
        active_context="请根据知识库核对2025年住房公积金缴费基数核算办法。",
    )

    assert result.business_intent.value == "policy_question"
    assert result.turn_action.value == "continue_with_tools"
    assert result.execution_mode.value == "execute"


def test_policy_question_retrieves_before_asking_user_for_source_material():
    decision = json.dumps({
        "primary_intent": "policy_question", "secondary_intents": [],
        "context_mode": "uses_conversation", "missing_inputs": ["具体政策条款"],
        "risk_level": "low", "data_class": "internal_hr",
        "required_capabilities": ["knowledge.retrieve"], "confidence": 0.88,
        "reason_summary": "The model wants the user to supply the policy text.",
        "turn_action": "clarify", "resolved_request": "Explain the referenced contribution-base rule.",
        "retrieval_queries": ["住房公积金 缴费基数 核算"], "priority_constraints": [],
    })

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "那这个基数到底怎么算？",
        active_context="请根据知识库核对2025年住房公积金缴费基数核算办法。",
    )

    assert result.turn_action.value == "continue_with_tools"
    assert result.execution_mode.value == "execute"
    assert result.execution_route.value == "hr_evidence"


def test_policy_followup_without_cached_result_falls_forward_to_retrieval():
    decision = json.dumps({
        "primary_intent": "policy_question", "secondary_intents": [],
        "context_mode": "uses_conversation", "missing_inputs": [], "risk_level": "low",
        "data_class": "internal_hr", "required_capabilities": ["knowledge.retrieve"],
        "confidence": 0.9, "reason_summary": "Explain the referenced rule.",
        "turn_action": "explain_existing", "resolved_request": "Explain the contribution-base rule.",
        "retrieval_queries": ["住房公积金 缴费基数"], "priority_constraints": [],
    })

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "那这个基数到底怎么算？",
        active_context="请根据知识库核对2025年住房公积金缴费基数核算办法。",
        has_active_result=False,
    )

    assert result.turn_action.value == "continue_with_tools"
    assert result.execution_mode.value == "execute"


def test_public_current_info_keeps_location_clarification_when_model_names_the_gap():
    decision = json.dumps({
        "primary_intent": "public_current_info", "secondary_intents": ["conversation"],
        "context_mode": "standalone", "missing_inputs": ["location"], "risk_level": "low",
        "data_class": "public", "required_capabilities": ["weather.current"],
        "confidence": 0.94, "reason_summary": "日期可直接回答，但台风影响需要用户所在地区。",
        "turn_action": "start_new", "resolved_request": "回答今天是星期几，并查询用户所在地的台风影响。",
        "retrieval_queries": ["当地 台风 实时预警"], "priority_constraints": [],
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "任务目标：今天是礼拜几，是不是台风很大"
    )

    assert result.business_intent.value == "public_current_info"
    assert result.execution_route.value == "clarify"
    assert result.execution_mode.value == "clarify"
    assert result.missing_slots == ["location"]
    assert result.task_intent is not None
    assert result.task_intent.domain.value == "public_current_info"
    assert result.selected_capability_id == "public_information"
    assert result.candidate_capability_ids == ["public_information", "general_conversation"]
    assert result.routing_fallback_used is False


def test_public_info_ignores_unsupported_location_gap_instead_of_changing_topics():
    decision = json.dumps({
        "primary_intent": "public_current_info", "secondary_intents": [],
        "context_mode": "new_session", "missing_inputs": ["location"], "risk_level": "low",
        "data_class": "public", "required_capabilities": ["web.search", "web.open"],
        "confidence": 0.91, "reason_summary": "热门内容可能按城市不同。",
        "turn_action": "clarify", "resolved_request": "查询近期热门电影推荐。",
        "retrieval_queries": ["近期热门电影"], "priority_constraints": [],
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "晚上好，我想看电影，查查最近有哪些热门大片"
    )

    assert result.business_intent.value == "public_current_info"
    assert result.execution_mode.value == "execute"
    assert result.missing_slots == []
    assert result.retrieval_mode.value == "web"
    assert result.resolved_request == "晚上好，我想看电影，查查最近有哪些热门大片"
    assert "地点缺口没有得到用户原话支持" in result.reason_summary


def test_public_current_info_fallback_preserves_domain_when_router_model_is_unavailable():
    result = GovernedModelRouter(UnavailableGateway()).interpret(
        "任务目标：今天是礼拜几，是不是台风很大"
    )

    assert result.business_intent.value == "public_current_info"
    assert result.execution_route.value == "clarify"
    assert result.missing_slots == ["location"]
    assert result.matched_layer == "deterministic_fallback"
    assert result.selected_capability_id == "public_information"
    assert result.routing_fallback_used is True


def test_social_closing_remains_conversation_when_router_model_is_unavailable():
    result = GovernedModelRouter(UnavailableGateway()).interpret("谢谢，先这样")

    assert result.business_intent.value == "conversation"
    assert result.turn_action.value == "social"
    assert result.execution_mode.value == "no_op"
    assert result.execution_route.value == "conversation"
    assert result.selected_capability_id == "general_conversation"
    assert result.routing_fallback_used is True


def test_unclear_successful_small_model_route_escalates_semantically():
    weak_decision = json.dumps({
        "primary_intent": "unclear", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": ["objective"],
        "risk_level": "low", "data_class": "public", "required_capabilities": [],
        "confidence": 0.42, "reason_summary": "任务目标不清楚。",
        "turn_action": "clarify", "resolved_request": "",
        "retrieval_queries": [], "priority_constraints": [],
    }, ensure_ascii=False)
    strong_decision = json.dumps({
        "primary_intent": "public_current_info", "secondary_intents": ["conversation"],
        "context_mode": "standalone", "missing_inputs": [],
        "risk_level": "low", "data_class": "public",
        "required_capabilities": ["web.search", "web.open"],
        "confidence": 0.93, "reason_summary": "答案依赖近期公开信息，应联网检索。",
        "turn_action": "start_new", "resolved_request": "推荐三部近期值得观看的院线电影。",
        "retrieval_queries": ["近期热门院线电影 推荐"], "priority_constraints": ["三部"],
    }, ensure_ascii=False)
    gateway = EscalatingGateway([weak_decision, strong_decision])

    result = GovernedModelRouter(gateway).interpret(
        "下周末想去影院，挑三部近期值得看的片子。"
    )

    assert len(gateway.calls) == 2
    assert gateway.calls[1][1]["model"] == "qwen-plus"
    assert result.business_intent.value == "public_current_info"
    assert result.execution_mode.value == "execute"
    assert result.missing_slots == []
    assert result.matched_layer == "governed_model_first:qwen-plus"


def test_malformed_small_model_route_escalates_before_rule_fallback():
    strong_decision = json.dumps({
        "primary_intent": "public_current_info", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": [],
        "risk_level": "low", "data_class": "public",
        "required_capabilities": ["web.search", "web.open"],
        "confidence": 0.9, "reason_summary": "需要近期公开信息。",
        "turn_action": "start_new", "resolved_request": "推荐近期公开活动。",
        "retrieval_queries": ["近期公开活动"], "priority_constraints": [],
    }, ensure_ascii=False)
    gateway = EscalatingGateway(["这不是合法 JSON", strong_decision])

    result = GovernedModelRouter(gateway).interpret("周末有什么新活动值得去？")

    assert len(gateway.calls) == 2
    assert gateway.calls[1][1]["model"] == "qwen-plus"
    assert result.business_intent.value == "public_current_info"
    assert result.routing_fallback_used is False


def test_semantically_unclear_strong_route_gets_one_bounded_repair_loop():
    unclear = json.dumps({
        "primary_intent": "unclear", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": ["objective"],
        "risk_level": "low", "data_class": "public", "required_capabilities": [],
        "confidence": 0.4, "reason_summary": "不确定。",
        "turn_action": "clarify", "resolved_request": "",
        "retrieval_queries": [], "priority_constraints": [],
    }, ensure_ascii=False)
    repaired = json.dumps({
        "primary_intent": "public_current_info", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": [],
        "risk_level": "low", "data_class": "public",
        "required_capabilities": ["web.search", "web.open"],
        "confidence": 0.92, "reason_summary": "需要近期公开信息。",
        "turn_action": "start_new", "resolved_request": "推荐近期影片。",
        "retrieval_queries": ["近期影片推荐"], "priority_constraints": ["三部"],
    }, ensure_ascii=False)
    gateway = EscalatingGateway([unclear, unclear, repaired])

    result = GovernedModelRouter(gateway).interpret("想看点最近的新片，挑三部。")

    assert len(gateway.calls) == 3
    assert result.business_intent.value == "public_current_info"
    assert result.execution_mode.value == "execute"


def test_exhausted_router_failure_does_not_ask_nonempty_request_for_objective():
    result = GovernedModelRouter(UnavailableGateway()).interpret(
        "请用通俗的话解释胜任力模型。"
    )

    assert result.business_intent.value == "conversation"
    assert result.execution_mode.value == "execute"
    assert result.missing_slots == []
    assert result.reason_code == "SEMANTIC_ROUTER_FALLBACK_CONVERSATION"


def test_nonempty_user_request_never_remains_missing_objective():
    weak_decision = json.dumps({
        "primary_intent": "conversation", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": ["objective"],
        "risk_level": "low", "data_class": "public", "required_capabilities": [],
        "confidence": 0.9, "reason_summary": "可以直接回答。",
        "turn_action": "start_new", "resolved_request": "解释这个概念。",
        "retrieval_queries": [], "priority_constraints": [],
    }, ensure_ascii=False)
    strong_decision = weak_decision.replace(
        '\"missing_inputs\": [\"objective\"]', '\"missing_inputs\": []'
    )
    gateway = EscalatingGateway([weak_decision, strong_decision])

    result = GovernedModelRouter(gateway).interpret(
        "用通俗的话解释一下什么是胜任力模型。"
    )

    assert result.missing_slots == []
    assert result.execution_mode.value == "execute"


def test_router_normalizes_empty_object_for_list_fields_without_losing_semantics():
    decision = json.dumps({
        "primary_intent": "public_current_info", "secondary_intents": {},
        "context_mode": "standalone", "missing_inputs": {},
        "risk_level": "low", "data_class": "public",
        "required_capabilities": "web.search",
        "confidence": 0.91, "reason_summary": "需要近期公开信息。",
        "turn_action": "start_new", "resolved_request": "推荐近期影片。",
        "retrieval_queries": "近期影片推荐", "priority_constraints": {},
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "推荐近期值得看的影片。"
    )

    assert result.business_intent.value == "public_current_info"
    assert result.execution_mode.value == "execute"
    assert "web.search" in result.required_capabilities


def test_existing_result_selection_overrides_fresh_task_missing_inputs():
    decision = json.dumps({
        "primary_intent": "recruitment_screening", "secondary_intents": [],
        "context_mode": "uses_conversation", "missing_inputs": ["候选人材料"],
        "risk_level": "high", "data_class": "restricted_hr",
        "required_capabilities": ["file.extract", "recruitment.evaluate"],
        "confidence": 0.9, "reason_summary": "用户要从既有排名确定两位候选人。",
        "turn_action": "clarify", "resolved_request": "从已有6人排名中确定两位候选人。",
        "retrieval_queries": [], "priority_constraints": ["选择2位"],
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "那就从里面定两位吧",
        active_context="上一轮已完成6位候选人的证据评分和排名。",
        has_active_result=True,
    )

    assert result.business_intent.value == "recruitment_screening"
    assert result.turn_action.value == "compare_existing"
    assert result.execution_mode.value == "reuse_result"
    assert result.retrieval_mode.value == "none"
    assert result.missing_slots == []
    assert result.required_capabilities == ["context.read", "result.explain"]


def test_model_router_receives_registry_descriptions_not_only_a_keyword_rulebook():
    decision = json.dumps({
        "primary_intent": "conversation", "secondary_intents": [],
        "context_mode": "standalone", "missing_inputs": [], "risk_level": "low",
        "data_class": "public", "required_capabilities": [], "confidence": 0.9,
        "reason_summary": "普通对话", "turn_action": "start_new",
        "resolved_request": "解释一个普通问题", "retrieval_queries": [],
        "priority_constraints": [],
    }, ensure_ascii=False)
    gateway = QueueGateway([decision])

    GovernedModelRouter(gateway).interpret("给我解释一下这个概念")

    user_payload = gateway.calls[0][0][1]["content"]
    assert "capability_registry" in user_payload
    assert "public_information" in user_payload
    assert "policy_evidence" in user_payload


def test_model_declared_evidence_capability_controls_composite_retrieval_mode():
    decision = json.dumps({
        "primary_intent": "document_generation", "secondary_intents": ["policy_question"],
        "context_mode": "standalone", "missing_inputs": [], "risk_level": "medium",
        "data_class": "internal_hr",
        "required_capabilities": ["knowledge.retrieve", "document.compose", "artifact.create"],
        "confidence": 0.94, "reason_summary": "需要内部政策证据支撑正式通知。",
        "turn_action": "start_new", "resolved_request": "依据内部调岗政策起草正式通知。",
        "retrieval_queries": ["内部调岗政策"], "priority_constraints": [],
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([decision])).interpret(
        "按内部调岗政策帮我写一份正式通知"
    )

    assert result.selected_capability_id == "document_generation"
    assert result.retrieval_mode.value == "internal"
    assert "knowledge.retrieve" in result.required_capabilities


def test_skill_authoring_is_a_registered_semantic_capability():
    decision = json.dumps({
        "primary_intent": "skill_authoring", "secondary_intents": [],
        "context_mode": "uses_conversation", "missing_inputs": [], "risk_level": "medium",
        "data_class": "internal_hr", "required_capabilities": ["skill.author", "skill.validate"],
        "confidence": 0.96, "reason_summary": "用户要沉淀当前流程。",
        "turn_action": "start_new", "resolved_request": "把当前对话沉淀为可复用 Skill。",
        "retrieval_queries": [], "priority_constraints": [],
    }, ensure_ascii=False)

    result = GovernedModelRouter(QueueGateway([decision])).interpret("把这段对话做成一个 Skill")

    assert result.business_intent.value == "skill_authoring"
    assert result.selected_capability_id == "skill_authoring"
    assert result.required_capabilities == ["skill.author", "skill.validate"]
