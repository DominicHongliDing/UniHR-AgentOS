from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.gateway import ModelGateway, ModelGatewayError, parse_json_object

from .contracts import (
    BusinessIntent,
    DeliveryMode,
    ExecutionMode,
    ExecutionRoute,
    RetrievalMode,
    TaskIntent,
    TurnAction,
    TurnInterpretation,
)
from .capability_registry import TaskCapabilityRegistry, default_task_capability_registry
from .router import (
    LayeredRouter,
    artifact_evidence_capabilities,
    is_explicit_public_web_request,
    is_recruitment_web_request,
    is_policy_content_question,
    is_product_help_question,
    public_request_requires_location,
    recruitment_web_capabilities,
    recruitment_web_missing_slots,
    requested_artifact_intent,
    requested_evidence_mode,
)


_MODE_INTENTS = {
    "简历筛选": BusinessIntent.RECRUITMENT_SCREENING,
    "政策问答": BusinessIntent.POLICY_QUESTION,
    "公文材料": BusinessIntent.DOCUMENT_GENERATION,
    "表格处理": BusinessIntent.SPREADSHEET_PROCESSING,
    "图片理解": BusinessIntent.IMAGE_UNDERSTANDING,
    "联网信息": BusinessIntent.PUBLIC_CURRENT_INFO,
    "Skill 制作": BusinessIntent.SKILL_AUTHORING,
}

_ACTION_MODES = {
    TurnAction.START_NEW: ExecutionMode.EXECUTE,
    TurnAction.CONTINUE_WITH_TOOLS: ExecutionMode.EXECUTE,
    TurnAction.COMPARE_EXISTING: ExecutionMode.REUSE_RESULT,
    TurnAction.EXPLAIN_EXISTING: ExecutionMode.REUSE_RESULT,
    TurnAction.CONFIRM_EXISTING: ExecutionMode.REUSE_RESULT,
    TurnAction.REVISE_EXISTING: ExecutionMode.MUTATE,
    TurnAction.REGENERATE: ExecutionMode.MUTATE,
    TurnAction.EXPORT_EXISTING: ExecutionMode.REUSE_RESULT,
    TurnAction.SOCIAL: ExecutionMode.NO_OP,
    TurnAction.CANCEL: ExecutionMode.NO_OP,
    TurnAction.CLARIFY: ExecutionMode.CLARIFY,
}

_REUSE_CAPABILITIES = ["context.read", "result.explain"]


class _ModelDecision(BaseModel):
    # The provider may echo advisory fields such as execution_route. They are
    # ignored because UniHR deterministically derives route, permissions and
    # side effects below; rejecting the whole semantic result harms fallback
    # quality without adding any authorization safety.
    model_config = ConfigDict(extra="ignore")

    primary_intent: BusinessIntent
    secondary_intents: list[BusinessIntent] = Field(default_factory=list, max_length=4)
    context_mode: str = "standalone"
    missing_inputs: list[str] = Field(default_factory=list, max_length=8)
    risk_level: str = "low"
    data_class: str = "public"
    required_capabilities: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(ge=0, le=1)
    reason_summary: str = Field(min_length=1, max_length=300)
    turn_action: TurnAction = TurnAction.START_NEW
    delivery_mode: DeliveryMode = DeliveryMode.UNSPECIFIED
    resolved_request: str = Field(default="", max_length=4_000)
    retrieval_queries: list[str] = Field(default_factory=list, max_length=4)
    priority_constraints: list[str] = Field(default_factory=list, max_length=8)


class GovernedModelRouter:
    """Let the small model interpret semantics, then enforce deterministic policy."""

    def __init__(
        self,
        gateway: ModelGateway,
        fallback: LayeredRouter | None = None,
        capability_registry: TaskCapabilityRegistry | None = None,
    ) -> None:
        self.gateway = gateway
        self.fallback = fallback or LayeredRouter()
        self.capability_registry = capability_registry or default_task_capability_registry()

    def interpret(
        self,
        message: str,
        *,
        task_mode: str = "自动选择",
        attachment_summaries: list[dict[str, Any]] | None = None,
        active_context: str = "",
        has_active_result: bool = False,
    ) -> TurnInterpretation:
        explicit_intent = _MODE_INTENTS.get(task_mode)
        attachment_summaries = attachment_summaries or []
        prompt = {
            "message": message,
            "explicit_task_mode": task_mode,
            "attachment_summaries": attachment_summaries,
            "active_context_summary": active_context[-2_000:],
            "has_active_result": has_active_result,
            "allowed_primary_intents": [item.value for item in BusinessIntent],
            "allowed_turn_actions": [item.value for item in TurnAction],
            "allowed_delivery_modes": [item.value for item in DeliveryMode],
            "capability_registry": self.capability_registry.model_catalog(),
        }
        messages = [
                    {
                        "role": "system",
                        "content": (
                            "你是 UniHR 的 Turn Interpreter。只做语义路由，不回答用户问题，不调用工具。"
                            "输出严格 JSON，字段必须是 primary_intent, secondary_intents, context_mode, "
                            "missing_inputs, risk_level, data_class, required_capabilities, confidence, reason_summary, "
                            "product_help 表示用户询问 UniHR 的产品能力、机制、界面或技术说明；除非用户明确引用当前会话中的具体业务结果，"
                            "否则 turn_action 应为 start_new，而不是 explain_existing。"
                            "用户询问政策、制度、办法、细则、条例或通知本身是否有效、适用、还能否执行，属于 policy_question；"
                            "用户要求了解、查询、咨询、介绍某项政策、制度或薪酬等人事方案的具体内容，也属于 policy_question，"
                            "不能因为消息带有‘任务目标’或没有上传附件就解释成 product_help；"
                            "如果用户明确要求联网、搜索公开网页、查询官网或附网页来源，通常属于 public_current_info；"
                            "但联网搜索简历、候选人或人才属于 recruitment_screening，联网只是证据来源和工具方式，"
                            "不能把招聘业务降级为通用联网问答；"
                            "联网查询公开政策动态仍属于 public_current_info，而不是内部 policy_question；"
                            "口语中的‘还能按吗’、‘现在算不算’不能解释成询问 UniHR 产品功能。"
                            "turn_action, delivery_mode, resolved_request, retrieval_queries, priority_constraints。"
                            "delivery_mode 与业务领域相互独立：inline_response 是聊天内回答，inline_transform 是在聊天内"
                            "润色、改写、翻译、校对或摘要，create_artifact 才是创建 Word/Excel 等文件。"
                            "用户说‘正式通知的感觉’只是在描述文字风格，不等于要求创建通知文件；"
                            "除非用户明确要求 Word、Excel、文件、下载或导出，或显式选择产物任务模式，"
                            "不得选择 create_artifact。"
                            "business intent 表示业务领域；turn_action 只表示本轮动作。resolved_request 要结合上下文"
                            "把省略指代补全成可独立理解的请求，但不得新增事实、人数、日期、地区或筛选条件；"
                            "retrieval_queries 只给检索用的 1-4 个短查询；priority_constraints 保留用户明确说出的"
                            "必须、优先、加分、排除和输出数量要求。"
                            "用户要求比较、解释、确认、导出已有结果时，绝不能标为 start_new 或 regenerate；"
                            "只有明确要求重新计算、重新筛选、重新生成或改变规则时才允许 regenerate/revise_existing。"
                            "读取、查看、提取、列出、总结或回答附件中的信息属于只读问答，不是 document_generation，"
                            "也不是 spreadsheet_processing；只有用户明确要求撰写、生成、制作、修改或导出文件时，"
                            "才允许选择会创建或改变 Artifact 的 intent。"
                            "显式任务模式只限定业务领域，不代表每轮都重新执行。执行方式由系统根据 turn_action 治理，"
                            "不要自行输出执行器、MCP 或 execution_mode。"
                            "primary_intent 必须根据 capability_registry 的语义描述选择；新增能力会通过该注册表提供，"
                            "不要依赖固定关键词表。识别多意图和上下文续问；不要输出函数名、MCP 名或思维链。"
                        ),
                    },
                    {"role": "user", "content": str(prompt)},
                ]
        try:
            reply = self.gateway.complete(
                messages,
                purpose="turn_interpretation",
                structured=True,
                max_tokens=700,
            )
            try:
                decision = _parse_model_decision(reply.content)
            except (ValueError, ValidationError):
                # Transport success does not guarantee a usable structured
                # routing decision. Give the stronger semantic router a chance
                # to repair malformed or schema-invalid output before rules.
                fallback_model = _semantic_fallback_model(self.gateway, reply.model)
                if not fallback_model:
                    raise
                reply = self.gateway.complete(
                    messages,
                    purpose="turn_interpretation",
                    structured=True,
                    max_tokens=700,
                    model=fallback_model,
                )
                try:
                    decision = _parse_model_decision(reply.content)
                except (ValueError, ValidationError):
                    decision, reply = _repair_model_decision(
                        self.gateway,
                        original_message=message,
                        invalid_content=reply.content,
                        model=reply.model,
                    )
            # A technically successful small-model response can still be
            # semantically unusable. Escalate uncertain/contradictory routing
            # to the stronger semantic router before deterministic rules are
            # considered. This is domain-neutral and avoids growing a list of
            # prompt-specific keyword patches.
            if _needs_semantic_escalation(decision, message):
                fallback_model = _semantic_fallback_model(self.gateway, reply.model)
                if fallback_model:
                    stronger_reply = self.gateway.complete(
                        messages,
                        purpose="turn_interpretation",
                        structured=True,
                        max_tokens=700,
                        model=fallback_model,
                    )
                    try:
                        decision = _parse_model_decision(stronger_reply.content)
                        reply = stronger_reply
                    except (ValueError, ValidationError):
                        decision, reply = _repair_model_decision(
                            self.gateway,
                            original_message=message,
                            invalid_content=stronger_reply.content,
                            model=stronger_reply.model,
                        )
            if _needs_semantic_escalation(decision, message) and reply.model == getattr(
                getattr(self.gateway, "settings", None), "router_fallback_model", ""
            ):
                decision, reply = _repair_model_decision(
                    self.gateway,
                    original_message=message,
                    invalid_content=reply.content,
                    model=reply.model,
                )
            if explicit_intent is not None:
                decision = decision.model_copy(update={"primary_intent": explicit_intent})
            return self._govern(
                decision,
                model_name=reply.model,
                original_message=message,
                has_active_result=has_active_result,
                artifact_intent_authorized=explicit_intent is not None,
                has_attachments=bool(attachment_summaries),
            )
        except (ModelGatewayError, ValueError, ValidationError):
            return self._fallback(
                message,
                explicit_intent,
                active_context=active_context,
                has_active_result=has_active_result,
            )

    def _govern(
        self,
        decision: _ModelDecision,
        model_name: str,
        *,
        original_message: str,
        has_active_result: bool = False,
        artifact_intent_authorized: bool = False,
        has_attachments: bool = False,
    ) -> TurnInterpretation:
        intent = decision.primary_intent
        intent_overridden = False
        recruitment_web = is_recruitment_web_request(original_message)
        artifact_forbidden = _explicitly_forbids_artifact(original_message)
        artifact_intent = (
            None if artifact_forbidden else requested_artifact_intent(original_message)
        )
        artifact_delivery_authorized = artifact_intent_authorized or artifact_intent is not None
        inline_transform_guarded = (
            _is_inline_transformation_request(original_message)
            and not artifact_intent_authorized
            and artifact_intent is None
        )
        requested_mode = requested_evidence_mode(original_message)
        model_mode = _retrieval_mode_from_capabilities(decision.required_capabilities)
        if requested_mode == RetrievalMode.NONE:
            if not (
                artifact_intent is not None
                and decision.primary_intent != artifact_intent
            ):
                requested_mode = model_mode
        elif model_mode != RetrievalMode.NONE and model_mode != requested_mode:
            requested_mode = RetrievalMode.HYBRID
        artifact_composite = (
            artifact_intent is not None
            and requested_mode != RetrievalMode.NONE
            and not artifact_intent_authorized
        )
        if inline_transform_guarded:
            # A domain noun inside source text (for example “通知”) may only
            # describe tone. It is not permission to create a file.
            intent = BusinessIntent.CONVERSATION
            intent_overridden = intent != decision.primary_intent
        elif artifact_composite:
            intent = artifact_intent
            intent_overridden = intent != decision.primary_intent
        elif artifact_intent is not None:
            # The model selects semantics, but it cannot discard an explicit
            # output contract such as “generate Word/Excel”. This is a generic
            # artifact authorization invariant, not a prompt-specific patch.
            intent = artifact_intent
            intent_overridden = intent != decision.primary_intent
        elif recruitment_web and (
            not artifact_intent_authorized
            or decision.primary_intent == BusinessIntent.RECRUITMENT_SCREENING
        ):
            intent = BusinessIntent.RECRUITMENT_SCREENING
            intent_overridden = intent != decision.primary_intent
        elif is_product_help_question(original_message) and not artifact_intent_authorized:
            intent = BusinessIntent.PRODUCT_HELP
            intent_overridden = intent != decision.primary_intent
        elif is_explicit_public_web_request(original_message) and not artifact_intent_authorized:
            intent = BusinessIntent.PUBLIC_CURRENT_INFO
            intent_overridden = intent != decision.primary_intent
        elif is_policy_content_question(original_message) and not artifact_intent_authorized:
            # Domain evidence beats a model's speculative product-help reading.
            # This protects colloquial policy questions without special-casing
            # any one policy title or acceptance prompt.
            intent = BusinessIntent.POLICY_QUESTION
            intent_overridden = intent != decision.primary_intent
        # A source may itself be named “通知/方案”. Asking what that source says
        # is evidence retrieval, not authorization to create a new document.
        if (
            intent != BusinessIntent.PRODUCT_HELP
            and _is_explicit_knowledge_question(original_message)
            and not artifact_delivery_authorized
        ):
            intent = BusinessIntent.POLICY_QUESTION
            intent_overridden = intent != decision.primary_intent
        read_only_guarded = (
            intent in {BusinessIntent.DOCUMENT_GENERATION, BusinessIntent.SPREADSHEET_PROCESSING}
            and (
                artifact_forbidden
                or (
                    _is_read_only_attachment_request(original_message)
                    and not artifact_intent_authorized
                )
            )
        )
        if read_only_guarded:
            intent = BusinessIntent.CONVERSATION
        delivery_mode = decision.delivery_mode
        if inline_transform_guarded:
            delivery_mode = DeliveryMode.INLINE_TRANSFORM
        elif read_only_guarded:
            delivery_mode = DeliveryMode.INLINE_RESPONSE
        elif artifact_intent is not None:
            delivery_mode = DeliveryMode.CREATE_ARTIFACT
        action = decision.turn_action
        fallback_action = _fallback_action(original_message, has_active_result)
        if action == TurnAction.START_NEW and fallback_action != TurnAction.START_NEW:
            action = fallback_action
        elif (
            has_active_result
            and action != TurnAction.CANCEL
            and fallback_action in {
                TurnAction.COMPARE_EXISTING,
                TurnAction.EXPLAIN_EXISTING,
                TurnAction.CONFIRM_EXISTING,
                TurnAction.EXPORT_EXISTING,
            }
        ):
            # A semantic model may interpret “最终定三个” as mutating the prior
            # run. Selection/explanation/confirmation over an existing result
            # must reuse that result unless the user explicitly changes rules
            # or asks for a recalculation.
            action = fallback_action
        if read_only_guarded and action in {
            TurnAction.REVISE_EXISTING,
            TurnAction.REGENERATE,
        }:
            # The user is revising the requested delivery, not authorizing a
            # mutation of the previous file. Continue with a fresh inline
            # response while retaining conversational context.
            action = TurnAction.CONTINUE_WITH_TOOLS
        # A model sometimes asks for clarification merely because a natural
        # follow-up is short.  Clarification is valid only when it can name a
        # concrete missing input; otherwise the resolved domain request should
        # continue through its normal tools.
        retrieval_first_intents = {
            BusinessIntent.POLICY_QUESTION,
            BusinessIntent.PUBLIC_CURRENT_INFO,
            BusinessIntent.PRODUCT_HELP,
        }
        missing_slots = _canonical_missing_inputs(intent, decision.missing_inputs)
        if original_message.strip():
            # The current message is itself a usable objective. A model may
            # ask for richer constraints, but it must not claim that the task
            # has no objective when the user has already stated one.
            missing_slots = [item for item in missing_slots if item != "objective"]
        unsupported_location_gap_ignored = False
        if (
            intent == BusinessIntent.PUBLIC_CURRENT_INFO
            and "location" in missing_slots
            and not public_request_requires_location(original_message)
        ):
            # A model may over-generalize location-sensitive weather/search
            # behavior to every public-info request.  Scope requirements must
            # be supported by the current request, not by a missing-slot label.
            missing_slots = [item for item in missing_slots if item != "location"]
            unsupported_location_gap_ignored = True
        if (
            action == TurnAction.CLARIFY
            and intent != BusinessIntent.UNCLEAR
            and (not missing_slots or intent in {BusinessIntent.POLICY_QUESTION, BusinessIntent.PRODUCT_HELP})
        ):
            action = TurnAction.CONTINUE_WITH_TOOLS
        if intent == BusinessIntent.PUBLIC_CURRENT_INFO and "location" in missing_slots:
            # Missing geography changes the meaning of weather and emergency
            # information.  Do not search a random region even when the model
            # labelled the overall turn as a new task.
            action = TurnAction.CLARIFY
        execution_mode = _ACTION_MODES[action]
        if read_only_guarded:
            delivery_mode = DeliveryMode.INLINE_RESPONSE
        elif execution_mode == ExecutionMode.REUSE_RESULT:
            delivery_mode = DeliveryMode.REUSE_ARTIFACT
        elif execution_mode == ExecutionMode.MUTATE:
            delivery_mode = (
                DeliveryMode.MUTATE_ARTIFACT
                if artifact_intent_authorized or artifact_intent is not None
                else DeliveryMode.INLINE_RESPONSE
            )
        elif delivery_mode == DeliveryMode.UNSPECIFIED:
            delivery_mode = (
                DeliveryMode.CREATE_ARTIFACT
                if intent in {BusinessIntent.DOCUMENT_GENERATION, BusinessIntent.SPREADSHEET_PROCESSING}
                else DeliveryMode.INLINE_RESPONSE
            )
        if (
            delivery_mode in {DeliveryMode.INLINE_RESPONSE, DeliveryMode.INLINE_TRANSFORM}
            and (
                intent in {BusinessIntent.DOCUMENT_GENERATION, BusinessIntent.SPREADSHEET_PROCESSING}
                or read_only_guarded
                or inline_transform_guarded
            )
        ):
            intent = BusinessIntent.CONVERSATION
            intent_overridden = True
            # Formal artifact slots (issuer, exact venue, full roster, style
            # metadata, source workbook details) are not prerequisites for a
            # best-effort chat draft or transformation.  Once the user has
            # explicitly removed the file side effect, only a genuinely
            # missing objective may still justify clarification.
            missing_slots = [item for item in missing_slots if item == "objective"]
            if action == TurnAction.CLARIFY and not missing_slots:
                action = TurnAction.CONTINUE_WITH_TOOLS
                execution_mode = ExecutionMode.EXECUTE
        if recruitment_web:
            missing_slots = list(dict.fromkeys([
                *missing_slots,
                *recruitment_web_missing_slots(original_message),
            ]))
            if missing_slots:
                action = TurnAction.CLARIFY
                execution_mode = ExecutionMode.CLARIFY
        if (
            intent == BusinessIntent.PRODUCT_HELP
            and not has_active_result
            and execution_mode == ExecutionMode.REUSE_RESULT
        ):
            action = TurnAction.START_NEW
            execution_mode = ExecutionMode.EXECUTE
        if execution_mode == ExecutionMode.REUSE_RESULT and not has_active_result:
            if intent in retrieval_first_intents:
                action = TurnAction.CONTINUE_WITH_TOOLS
                execution_mode = ExecutionMode.EXECUTE
            else:
                action = TurnAction.CLARIFY
                execution_mode = ExecutionMode.CLARIFY
        risk_flags: list[str] = []
        data_class = decision.data_class
        if intent == BusinessIntent.RECRUITMENT_SCREENING:
            risk_flags.extend(["employment_decision", "candidate_pii", "human_review_required"])
            data_class = "restricted_hr"
        elif intent == BusinessIntent.DOCUMENT_GENERATION:
            risk_flags.append("formal_release_requires_approval")
            data_class = "internal_hr" if data_class == "public" else data_class
        effective_retrieval_mode = (
            RetrievalMode.WEB
            if recruitment_web
            else requested_mode
            if intent in {BusinessIntent.DOCUMENT_GENERATION, BusinessIntent.SPREADSHEET_PROCESSING}
            and requested_mode != RetrievalMode.NONE
            else RetrievalMode.WEB
            if intent == BusinessIntent.PUBLIC_CURRENT_INFO
            else RetrievalMode.INTERNAL
            if intent == BusinessIntent.POLICY_QUESTION
            else RetrievalMode.ATTACHMENTS
            if intent in {
                BusinessIntent.RECRUITMENT_SCREENING,
                BusinessIntent.IMAGE_UNDERSTANDING,
            }
            else RetrievalMode.ATTACHMENTS
            if intent == BusinessIntent.SPREADSHEET_PROCESSING and has_attachments
            else RetrievalMode.NONE
        )
        if execution_mode == ExecutionMode.REUSE_RESULT:
            # Existing-result actions read the persisted result instead of
            # pretending the user started a fresh attachment/retrieval task.
            missing_slots = []
            effective_retrieval_mode = RetrievalMode.NONE
        selected_descriptor = self.capability_registry.descriptor_for(intent)
        intended_capabilities = list(dict.fromkeys([
            *(
                recruitment_web_capabilities()
                if recruitment_web
                else artifact_evidence_capabilities(intent, effective_retrieval_mode)
                if intent in {BusinessIntent.DOCUMENT_GENERATION, BusinessIntent.SPREADSHEET_PROCESSING}
                and effective_retrieval_mode in {RetrievalMode.INTERNAL, RetrievalMode.WEB, RetrievalMode.HYBRID}
                else list(selected_descriptor.default_tools)
            ),
            *[item for item in ([] if intent_overridden else decision.required_capabilities) if item in {
                "knowledge.retrieve", "evidence.answer", "file.extract", "recruitment.evaluate",
                "recruitment.source", "spreadsheet.inspect", "spreadsheet.transform",
                "document.compose", "artifact.create", "vision.understand", "web.search",
                "web.open", "weather.current", "skill.author", "skill.validate",
            }],
        ]))
        if delivery_mode == DeliveryMode.INLINE_TRANSFORM or read_only_guarded:
            capabilities = []
        elif execution_mode == ExecutionMode.REUSE_RESULT:
            capabilities = list(_REUSE_CAPABILITIES)
        elif execution_mode in {ExecutionMode.NO_OP, ExecutionMode.CLARIFY}:
            capabilities = []
        else:
            capabilities = intended_capabilities
        route = selected_descriptor.route
        if execution_mode == ExecutionMode.REUSE_RESULT:
            route = ExecutionRoute.CONVERSATION
        elif execution_mode == ExecutionMode.NO_OP:
            route = ExecutionRoute.CONVERSATION
        elif execution_mode == ExecutionMode.CLARIFY:
            route = ExecutionRoute.CLARIFY
        if decision.confidence < 0.52 and intent == BusinessIntent.UNCLEAR:
            route = ExecutionRoute.CLARIFY
        resolved_request = _govern_resolved_request(
            original_message,
            decision.resolved_request,
        )
        if unsupported_location_gap_ignored:
            # The rewrite was built around a rejected scope assumption. Keep
            # the user-authored request as the canonical objective and make
            # the final trace describe the governed decision, not the model's
            # discarded clarification rationale.
            resolved_request = original_message.strip()
        retrieval_queries = _govern_retrieval_queries(
            original_message,
            resolved_request,
            decision.retrieval_queries,
        )
        candidates = self.capability_registry.candidates_for(intent, decision.secondary_intents)
        return TurnInterpretation(
            business_intent=intent,
            execution_route=route,
            confidence=decision.confidence,
            reason_code=(
                "DELIVERY_INLINE_TRANSFORM"
                if inline_transform_guarded
                else "READ_ONLY_ATTACHMENT_INSPECTION"
                if read_only_guarded
                else f"MODEL_FIRST_{intent.value.upper()}"
            ),
            missing_slots=missing_slots,
            risk_flags=risk_flags,
            requires_freshness=effective_retrieval_mode in {RetrievalMode.WEB, RetrievalMode.HYBRID},
            required_capabilities=capabilities,
            suggested_capabilities=intended_capabilities,
            retrieval_mode=effective_retrieval_mode,
            data_class=data_class,
            matched_layer=(
                f"governed_model_first:{model_name}:inline_delivery_guard"
                if inline_transform_guarded
                else f"governed_model_first:{model_name}:read_only_guard"
                if read_only_guarded
                else f"governed_model_first:{model_name}"
            ),
            secondary_intents=[item.value for item in decision.secondary_intents if item != intent],
            context_mode=decision.context_mode,
            reason_summary=(
                "用户要求对现有文字做文本到文本的转换，且没有授权创建文件；系统将在聊天内交付成品文字。"
                if inline_transform_guarded
                else "当前请求已足以执行公开信息查询；模型提出的地点缺口没有得到用户原话支持，"
                "系统已忽略该缺口并继续使用当前问题联网查询。"
                if unsupported_location_gap_ignored else decision.reason_summary
            ),
            turn_action=action,
            execution_mode=execution_mode,
            delivery_mode=delivery_mode,
            resolved_request=resolved_request,
            retrieval_queries=retrieval_queries,
            priority_constraints=[
                item.strip() for item in decision.priority_constraints if item.strip()
            ][:8],
            task_intent=TaskIntent(
                objective=resolved_request,
                domain=intent,
                action=action,
                delivery_mode=delivery_mode,
                deliverables=_infer_deliverables(intended_capabilities),
                constraints=[item.strip() for item in decision.priority_constraints if item.strip()][:8],
                requires_freshness=effective_retrieval_mode in {RetrievalMode.WEB, RetrievalMode.HYBRID},
                retrieval_mode=effective_retrieval_mode,
                context_mode=decision.context_mode,
                missing_inputs=missing_slots,
            ),
            candidate_capability_ids=[item.capability_id for item in candidates],
            selected_capability_id=selected_descriptor.capability_id,
            capability_selection_reason=(
                f"TaskIntent 的领域为 {intent.value}；由能力注册表选择 {selected_descriptor.label}。"
            ),
            routing_fallback_used=False,
        )

    def _fallback(
        self,
        message: str,
        explicit_intent: BusinessIntent | None,
        *,
        active_context: str = "",
        has_active_result: bool = False,
    ) -> TurnInterpretation:
        action = _fallback_action(message, has_active_result)
        resolved_request = _contextual_fallback_request(message, active_context)
        if explicit_intent is None:
            result = self.fallback.interpret(message)
            if (
                result.business_intent == BusinessIntent.UNCLEAR
                and active_context.strip()
                and _looks_context_dependent(message)
            ):
                contextual_result = self.fallback.interpret(f"{active_context}\n{message}")
                if contextual_result.business_intent != BusinessIntent.UNCLEAR:
                    result = contextual_result.model_copy(update={
                        "reason_code": "CONTEXTUAL_FALLBACK_DOMAIN",
                    })
            if result.business_intent == BusinessIntent.UNCLEAR and len(message.strip()) >= 12:
                # After semantic routing and its repair loop are exhausted, a
                # non-empty user request is still a valid conversational task.
                # Preserve usability instead of asking for an objective that
                # is visibly present. This branch does not infer tools or grant
                # side effects; it only falls back to read-only conversation.
                result = result.model_copy(update={
                    "business_intent": BusinessIntent.CONVERSATION,
                    "execution_route": ExecutionRoute.CONVERSATION,
                    "missing_slots": [],
                    "required_capabilities": [],
                    "suggested_capabilities": [],
                    "reason_code": "SEMANTIC_ROUTER_FALLBACK_CONVERSATION",
                    "data_class": "public",
                })
            if action == TurnAction.SOCIAL:
                result = result.model_copy(update={
                    "business_intent": BusinessIntent.CONVERSATION,
                    "execution_route": ExecutionRoute.CONVERSATION,
                    "missing_slots": [],
                    "required_capabilities": [],
                    "suggested_capabilities": [],
                    "reason_code": "SOCIAL_CLOSING_FALLBACK",
                    "data_class": "public",
                })
            elif result.execution_route == ExecutionRoute.CLARIFY:
                action = TurnAction.CLARIFY
            result = result.model_copy(update={
                "matched_layer": "deterministic_fallback",
                "reason_summary": "路由模型不可用，使用保守规则基线。",
                "turn_action": action,
                "execution_mode": _ACTION_MODES[action],
                "resolved_request": resolved_request,
                "retrieval_queries": list(dict.fromkeys([message.strip(), resolved_request]))[:4],
            })
            if _is_inline_transformation_request(message):
                result = result.model_copy(update={
                    "business_intent": BusinessIntent.CONVERSATION,
                    "execution_route": ExecutionRoute.CONVERSATION,
                    "delivery_mode": DeliveryMode.INLINE_TRANSFORM,
                    "required_capabilities": [],
                    "suggested_capabilities": [],
                    "risk_flags": [],
                    "reason_code": "DELIVERY_INLINE_TRANSFORM",
                    "reason_summary": "用户要求在聊天内改写现有文字，未授权创建文件。",
                    "matched_layer": "deterministic_fallback:inline_delivery_guard",
                })
            if (
                result.business_intent in {BusinessIntent.DOCUMENT_GENERATION, BusinessIntent.SPREADSHEET_PROCESSING}
                and _is_read_only_attachment_request(message)
            ):
                result = result.model_copy(update={
                    "business_intent": BusinessIntent.CONVERSATION,
                    "execution_route": ExecutionRoute.CONVERSATION,
                    "required_capabilities": [],
                    "suggested_capabilities": [],
                    "risk_flags": [],
                    "reason_code": "READ_ONLY_ATTACHMENT_INSPECTION",
                    "reason_summary": "用户只要求读取附件信息，未授权创建或修改文件。",
                    "matched_layer": "deterministic_fallback:read_only_guard",
                })
            return self._attach_capability_metadata(
                result,
                objective=resolved_request,
                fallback_used=True,
            )
        decision = _ModelDecision(
            primary_intent=explicit_intent,
            confidence=1.0,
            reason_summary="用户显式选择了任务模式。",
            risk_level="high" if explicit_intent == BusinessIntent.RECRUITMENT_SCREENING else "medium",
            data_class="restricted_hr" if explicit_intent == BusinessIntent.RECRUITMENT_SCREENING else "internal_hr",
            turn_action=action,
            resolved_request=resolved_request,
            retrieval_queries=list(dict.fromkeys([message.strip(), resolved_request]))[:4],
        )
        governed = self._govern(
            decision,
            model_name="explicit_mode",
            original_message=message,
            has_active_result=has_active_result,
            artifact_intent_authorized=True,
            has_attachments=False,
        )
        return governed.model_copy(update={
            "matched_layer": "explicit_mode_fallback",
            "routing_fallback_used": True,
        })

    def _attach_capability_metadata(
        self,
        interpretation: TurnInterpretation,
        *,
        objective: str,
        fallback_used: bool,
    ) -> TurnInterpretation:
        descriptor = self.capability_registry.descriptor_for(interpretation.business_intent)
        valid_values = {item.value for item in BusinessIntent}
        secondary = [
            BusinessIntent(item) for item in interpretation.secondary_intents if item in valid_values
        ]
        candidates = self.capability_registry.candidates_for(
            interpretation.business_intent,
            secondary,
        )
        return interpretation.model_copy(update={
            "task_intent": TaskIntent(
                objective=objective or interpretation.resolved_request,
                domain=interpretation.business_intent,
                action=interpretation.turn_action,
                delivery_mode=interpretation.delivery_mode,
                deliverables=_infer_deliverables(
                    interpretation.suggested_capabilities or interpretation.required_capabilities
                ),
                constraints=interpretation.priority_constraints,
                requires_freshness=interpretation.requires_freshness,
                retrieval_mode=interpretation.retrieval_mode,
                context_mode=interpretation.context_mode,
                missing_inputs=interpretation.missing_slots,
            ),
            "candidate_capability_ids": [item.capability_id for item in candidates],
            "selected_capability_id": descriptor.capability_id,
            "capability_selection_reason": (
                f"TaskIntent 的领域为 {interpretation.business_intent.value}；"
                f"由能力注册表选择 {descriptor.label}。"
            ),
            "routing_fallback_used": fallback_used,
        })


def _retrieval_mode_from_capabilities(capabilities: list[str]) -> RetrievalMode:
    """Translate semantic evidence requirements into a governed source mode."""

    needs_internal = any(item in {"knowledge.retrieve", "evidence.answer"} for item in capabilities)
    needs_web = any(item in {"web.search", "web.open", "weather.current"} for item in capabilities)
    if needs_internal and needs_web:
        return RetrievalMode.HYBRID
    if needs_internal:
        return RetrievalMode.INTERNAL
    if needs_web:
        return RetrievalMode.WEB
    return RetrievalMode.NONE


def _canonical_missing_inputs(intent: BusinessIntent, values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        clean = value.strip()
        if not clean:
            continue
        compact = clean.casefold()
        if compact in {"objective", "goal", "task objective", "task_objective", "任务目标", "目标"}:
            clean = "objective"
        if intent == BusinessIntent.PUBLIC_CURRENT_INFO and any(
            marker in compact for marker in ("location", "city", "district", "地点", "地区", "城市", "区县", "位置", "所在地")
        ):
            clean = "location"
        if clean not in normalized:
            normalized.append(clean)
    return normalized[:8]


def _needs_semantic_escalation(decision: _ModelDecision, message: str) -> bool:
    """Detect an unusable semantic route without inferring the domain by keywords."""

    if not message.strip():
        return False
    missing = {"".join(item.casefold().split()) for item in decision.missing_inputs}
    claims_objective_missing = bool(
        missing.intersection({"objective", "goal", "taskobjective", "任务目标", "目标"})
    )
    return (
        decision.primary_intent == BusinessIntent.UNCLEAR
        or decision.confidence < 0.55
        or claims_objective_missing
    )


def _semantic_fallback_model(gateway: Any, current_model: str) -> str:
    candidate = getattr(
        getattr(gateway, "settings", None),
        "router_fallback_model",
        "",
    )
    return candidate if candidate and candidate != current_model else ""


def _repair_model_decision(
    gateway: Any,
    *,
    original_message: str,
    invalid_content: str,
    model: str,
) -> tuple[_ModelDecision, Any]:
    """One bounded self-repair step for malformed semantic router output."""

    reply = gateway.complete(
        [
            {
                "role": "system",
                "content": (
                    "Repair a UniHR routing result. Return exactly one strict JSON object, "
                    "without markdown or explanation. Required fields: primary_intent, "
                    "secondary_intents, context_mode, missing_inputs, risk_level, data_class, "
                    "required_capabilities, confidence, reason_summary, turn_action, "
                    "delivery_mode, resolved_request, retrieval_queries, priority_constraints. "
                    f"primary_intent must be one of {[item.value for item in BusinessIntent]}. "
                    f"turn_action must be one of {[item.value for item in TurnAction]}. "
                    f"delivery_mode must be one of {[item.value for item in DeliveryMode]}. "
                    "If the user's message is non-empty, never claim objective is missing. "
                    "Current public facts such as recent news, releases, weather, schedules, "
                    "prices or events belong to public_current_info even when the user does not "
                    "explicitly say 'search the web'."
                ),
            },
            {
                "role": "user",
                "content": str({
                    "original_message": original_message,
                    "invalid_router_output": invalid_content[:2_500],
                }),
            },
        ],
        purpose="turn_interpretation",
        structured=True,
        max_tokens=600,
        model=model,
    )
    decision = _parse_model_decision(reply.content)
    return decision, reply


def _parse_model_decision(content: str) -> _ModelDecision:
    """Normalize harmless provider shape drift, then validate governed fields."""

    payload = parse_json_object(content)
    for field_name in (
        "secondary_intents",
        "missing_inputs",
        "required_capabilities",
        "retrieval_queries",
        "priority_constraints",
    ):
        value = payload.get(field_name)
        if isinstance(value, dict) and not value:
            payload[field_name] = []
        elif isinstance(value, str):
            payload[field_name] = [value] if value.strip() else []
    return _ModelDecision.model_validate(payload)


def _infer_deliverables(capabilities: list[str]) -> list[str]:
    deliverables: list[str] = []
    if "document.compose" in capabilities:
        deliverables.append("Word 文档")
    if "spreadsheet.transform" in capabilities:
        deliverables.append("Excel 工作簿")
    if "evidence.answer" in capabilities:
        deliverables.append("带证据回答")
    if "recruitment.evaluate" in capabilities:
        deliverables.append("候选人复核结果")
    if "web.search" in capabilities or "weather.current" in capabilities:
        deliverables.append("公开信息回答")
    return deliverables[:8]


def _fallback_action(message: str, has_active_result: bool) -> TurnAction:
    """Small, domain-neutral safety net used only when the model decision is unavailable."""

    clean = "".join(message.casefold().split())
    if clean in {"谢谢", "感谢", "多谢", "好的谢谢", "收到谢谢", "辛苦了"} or (
        len(clean) <= 16
        and clean.startswith(("谢谢", "感谢", "多谢", "辛苦了"))
        and any(marker in clean for marker in ("先这样", "就这样", "回头再说", "下次再说", "不用了"))
    ):
        return TurnAction.SOCIAL
    regeneration_question = any(marker in clean for marker in (
        "会不会", "是否会", "是不是会", "为什么会", "怎么会", "能不能自动", "有没有自动",
    ))
    if not regeneration_question and any(phrase in clean for phrase in (
        "重新生成", "重新计算", "重新筛选", "重跑", "再跑一遍",
    )):
        return TurnAction.REGENERATE
    if has_active_result and any(phrase in clean for phrase in (
        "对比", "比较", "选谁", "选一个", "敲定", "最终候选", "前两名", "前三名",
    )):
        return TurnAction.COMPARE_EXISTING
    if has_active_result and (
        ("最终" in clean and any(noun in clean for noun in ("候选", "人选", "方案", "版本")))
        or re.search(
            r"(?:选出|选择|推荐|确定|敲定|筛选出).{0,10}(?:\d+|[一二两三四五六七八九十两]+)(?:名|位|人|份|个)",
            clean,
        )
        or any(phrase in clean for phrase in ("进入后续面试", "进入下一轮", "进入复试"))
        or bool(re.search(r"(?:定|定下|确定|敲定).{0,8}(?:\d+|[一二两三仨四五六七八九十两]+)(?:名|位|人|份|个)?", clean))
    ):
        return TurnAction.COMPARE_EXISTING
    if has_active_result and any(phrase in clean for phrase in ("为什么", "解释", "依据", "怎么得出", "总结一下")):
        return TurnAction.EXPLAIN_EXISTING
    if has_active_result and any(phrase in clean for phrase in ("确认这个", "就按这个", "确认结果", "可以定稿")):
        return TurnAction.CONFIRM_EXISTING
    if has_active_result and any(phrase in clean for phrase in ("修改", "调整一下", "改成", "删掉", "补充")):
        return TurnAction.REVISE_EXISTING
    return TurnAction.START_NEW


def _is_explicit_knowledge_question(message: str) -> bool:
    compact = "".join(message.casefold().split())
    knowledge_anchor = any(term in compact for term in (
        "根据知识库", "知识库中", "刚发布的", "政策依据", "原文怎么说",
    ))
    question_action = any(term in compact for term in (
        "是什么", "分别是什么", "怎么规定", "是否", "多少", "哪一", "请告诉", "请回答",
    ))
    artifact_action = any(term in compact for term in (
        "生成word", "生成公文", "起草", "撰写", "制作文件", "导出word", "修改正文",
    ))
    return knowledge_anchor and question_action and not artifact_action


def _is_inline_transformation_request(message: str) -> bool:
    """Detect text-to-text work without granting artifact side effects.

    This is deliberately domain-neutral: the same contract covers polishing,
    rewriting, translation, proofreading and summarisation in any business
    area. Explicit file requests are handled separately by
    ``requested_artifact_intent`` or an explicitly selected task mode.
    """

    clean = "".join(message.casefold().split())
    transform_markers = (
        "润色", "改写", "重写", "优化表达", "调整语气", "换个说法",
        "翻译", "校对", "纠错", "扩写", "缩写", "精简这段", "概括这段",
        "总结这段", "整理下这段", "整理一下这段",
    )
    return any(marker in clean for marker in transform_markers) or bool(
        re.search(r"(?:换|改)(?:成|为|个).{0,12}(?:说法|表达|语气|措辞|版本)", clean)
    )


def _is_read_only_attachment_request(message: str) -> bool:
    """High-precision side-effect guard for attachment inspection requests."""

    clean = "".join(message.casefold().split())
    clean = re.sub(
        r"(?:不要|无需|不需要|无须|请勿|不得|禁止)(?:再|自动|另外|额外)?"
        r"(?:生成|创建|制作|撰写|起草|拟定|出具|导出|改写|修改|更新|填入|写入|做成|转成)"
        r"(?:任何)?(?:文件|文档|表格|报告|材料)?",
        "",
        clean,
    )
    creation_markers = (
        "生成", "创建", "制作", "撰写", "起草", "拟定", "出具", "导出",
        "改写", "修改", "更新", "填入", "写入", "做成", "转成", "形成文件",
        "形成文档", "生成报告", "生成表格", "写一份", "做一份",
    )
    if any(marker in clean for marker in creation_markers):
        return False
    inspection_markers = (
        "读取", "查看", "提取", "列出", "告诉我", "回答", "总结", "概括",
        "识别", "检查", "看看", "哪里不对", "分析附件", "附件中", "附件里",
        "文件中", "文件里", "这张表", "这个表",
    )
    return any(marker in clean for marker in inspection_markers)


def _explicitly_forbids_artifact(message: str) -> bool:
    clean = "".join(message.casefold().split())
    # A negative constraint on *content* (for example “不得写入受保护属性”)
    # must not be mistaken for “do not create an artifact”.  Require the
    # negated action to target an artifact noun, while retaining the natural
    # short forms users commonly use to pause a generation task.
    explicit_target = re.search(
        r"(?:不要|无需|不需要|无须|请勿|不得|禁止|先别|别)(?:再|自动|另外|额外)?"
        r"(?:生成|创建|制作|撰写|起草|拟定|出具|导出|改写|修改|更新|填入|写入|做成|转成|改|动)"
        r".{0,6}(?:文件|文档|word|docx|表格|excel|报告|材料|公文|产物)",
        clean,
    )
    if explicit_target:
        return True
    short_pauses = (
        "不要生成", "先别生成", "暂不生成", "别生成", "不要创建", "先别创建",
        "不要导出", "先别导出", "先别改", "不要改", "别改", "不要修改", "先别修改",
    )
    return any(clean.endswith(phrase) for phrase in short_pauses)


def _looks_context_dependent(message: str) -> bool:
    clean = "".join(message.casefold().split())
    if len(clean) <= 18:
        return True
    return any(marker in clean for marker in (
        "这个", "那个", "这批", "刚才", "上面", "之前", "照这个", "按去年的", "那就", "还是",
    ))


def _govern_resolved_request(original: str, candidate: str) -> str:
    """Reject rewrites that silently drop hard constraints from the user's words."""

    source = original.strip()
    rewritten = candidate.strip()
    if not rewritten or len(rewritten) > max(4_000, len(source) * 5):
        return source
    hard_tokens = re.findall(
        r"(?:\d+(?:\.\d+)?%?|20\d{2}年|[一二两三四五六七八九十两]+名|[一二两三四五六七八九十两]+位)",
        source,
    )
    if any(token not in rewritten for token in hard_tokens):
        return source
    return rewritten


def _govern_retrieval_queries(
    original: str,
    resolved: str,
    candidates: list[str],
) -> list[str]:
    values = [original.strip(), resolved.strip(), *[item.strip() for item in candidates]]
    return list(dict.fromkeys(item for item in values if item))[:4]


def _contextual_fallback_request(message: str, active_context: str) -> str:
    """Keep a conservative standalone query when semantic routing is unavailable."""

    source = message.strip()
    if not active_context.strip():
        return source
    previous_user_turns = [
        item.split(":", 1)[1].strip()
        for item in active_context.split("；")
        if item.startswith("user:") and item.split(":", 1)[1].strip() != source
    ]
    if not previous_user_turns:
        return source
    return f"{previous_user_turns[-1][:500]}；后续问题：{source}"
