from __future__ import annotations

import re

from app.domains.assistant.contracts import (
    BusinessIntent,
    ExecutionRoute,
    RetrievalMode,
    TurnInterpretation,
)


_PURE_CONVERSATION = {
    "你好",
    "您好",
    "嗨",
    "hello",
    "hi",
    "谢谢",
    "谢谢你",
    "好的",
    "ok",
}
_WEATHER_TERMS = (
    "天气", "气温", "温度", "下雨", "降雨", "下雪", "降雪", "风力",
    "台风", "暴雨", "大风", "风大", "预警", "风暴", "强对流",
)
_PUBLIC_INFO_TERMS = (
    "联网查询", "联网搜索", "搜索网页", "查一下官网", "官网最新", "最新消息",
    "最新新闻", "近期新闻", "当前汇率", "现在汇率", "今天新闻",
)
_RECRUITMENT_TERMS = ("简历", "候选人", "招聘", "岗位匹配", "筛选", "boss直聘")
_DOCUMENT_TERMS = ("公文", "会议材料", "会议纪要", "通知", "请示", "函")
_POLICY_TERMS = ("政策", "制度", "规定", "办法", "资格", "流程", "材料清单")
_LOCATION_FILLERS = (
    "你好",
    "您好",
    "请问",
    "帮我",
    "查一下",
    "查询",
    "今天",
    "明天",
    "后天",
    "现在",
    "当前",
    "怎么样",
    "多少",
    "几度",
    "会不会",
    "会",
    "的",
    "吗",
    "呢",
)
_LOCAL_SCOPE_TERMS = (
    "附近", "周边", "本地", "当地", "我这里", "我这边", "所在城市",
    "附近影院", "本地影院", "当地排片", "附近活动", "本地活动",
    "附近医院", "附近门店", "路况", "交通拥堵",
)


def _compact(message: str) -> str:
    return "".join(message.casefold().split())


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def is_weather_public_request(message: str) -> bool:
    """Return whether the current request itself is about weather or warnings."""

    return _contains(_compact(message), _WEATHER_TERMS)


def public_request_requires_location(message: str) -> bool:
    """Location is mandatory only when the requested fact is geographically scoped."""

    text = _compact(message)
    return is_weather_public_request(text) or _contains(text, _LOCAL_SCOPE_TERMS)


def is_explicit_web_action(message: str) -> bool:
    text = _compact(message)
    return (
        _contains(text, _PUBLIC_INFO_TERMS)
        or any(term in text for term in (
        "请联网", "联网查", "搜索公开网页", "搜索官网", "查官网", "附网页来源", "附上网址",
        ))
        or ("联网" in text and any(action in text for action in (
            "查", "搜索", "检索", "核对", "获取", "寻找", "搜集", "搜一下",
        )))
    )


def is_recruitment_web_request(message: str) -> bool:
    """Detect recruitment work whose evidence source is the public/authorized web."""

    text = _compact(message)
    # A recruitment *topic* is not automatically a candidate-sourcing task.
    # Remove explicitly negated person-level actions before looking for the
    # candidate target; this keeps public research about recruitment methods
    # in the public-information capability without adding prompt-specific rules.
    candidate_scope = re.sub(
        r"(?:不要|无需|不需要|无须|请勿|不得|禁止).{0,8}"
        r"(?:筛选|寻找|搜索|推荐|评价|排名|导出)?.{0,6}"
        r"(?:简历|候选人|人才|求职者|候选名单|人才画像)",
        "",
        text,
    )
    candidate_target = any(term in text for term in (
        "简历", "候选人", "人才", "求职者", "候选名单", "人才画像",
    ))
    candidate_target = candidate_target and any(term in candidate_scope for term in (
        "简历", "候选人", "人才", "求职者", "候选名单", "人才画像",
    ))
    return is_explicit_web_action(text) and candidate_target


def recruitment_web_missing_slots(message: str) -> list[str]:
    text = _compact(message)
    has_job_scope = any(term in text for term in (
        "岗位", "工程师", "经理", "专员", "实习生", "助理", "总监", "顾问",
        "java", "python", "算法", "产品", "运营", "销售", "行政", "财务", "人事",
    ))
    has_source_scope = any(term in text for term in (
        "公开", "官网", "招聘网站", "boss直聘", "猎聘", "智联", "前程无忧",
        "github", "linkedin", "领英", "已授权", "人才库", "简历库",
    ))
    missing: list[str] = []
    if not has_job_scope:
        missing.append("岗位名称或岗位要求")
    if not has_source_scope:
        missing.append("允许搜索的公开或已授权来源")
    return missing


def recruitment_web_capabilities() -> list[str]:
    return [
        "web.search", "web.open", "recruitment.source", "file.extract", "recruitment.evaluate",
    ]


def requested_artifact_intent(message: str) -> BusinessIntent | None:
    text = _compact(message)
    creates = any(term in text for term in (
        "生成", "创建", "制作", "撰写", "起草", "拟定", "出具", "导出", "整理成", "做成",
    ))
    if not creates:
        return None
    if any(term in text for term in (
        "word", "docx", "公文", "通知", "请示", "函", "会议纪要", "会议材料", "领导确认单",
    )):
        return BusinessIntent.DOCUMENT_GENERATION
    if any(term in text for term in (
        "excel", "xlsx", "工作簿", "电子表格", "统计表", "汇总表", "台账表",
    )):
        return BusinessIntent.SPREADSHEET_PROCESSING
    return None


def requested_evidence_mode(message: str) -> RetrievalMode:
    text = _compact(message)
    wants_web = is_explicit_web_action(text)
    wants_internal = any(term in text for term in (
        "知识库", "内部政策", "现有政策", "政策依据", "制度依据", "按政策", "根据政策",
    ))
    # Policy names often sit between the source qualifier and the word
    # “政策/制度” (for example “按内部调岗政策”). This governs evidence
    # provenance only; the semantic model still owns the business intent.
    wants_internal = wants_internal or bool(re.search(
        r"(?:按|依据|根据|参照).{0,12}(?:内部|现有|本校|学院)?.{0,12}(?:政策|制度|办法|规定)",
        text,
    ))
    if wants_web and wants_internal:
        return RetrievalMode.HYBRID
    if wants_web:
        return RetrievalMode.WEB
    if wants_internal:
        return RetrievalMode.INTERNAL
    return RetrievalMode.NONE


def artifact_evidence_capabilities(intent: BusinessIntent, mode: RetrievalMode) -> list[str]:
    capabilities = (
        ["document.compose", "artifact.create"]
        if intent == BusinessIntent.DOCUMENT_GENERATION
        else ["spreadsheet.inspect", "spreadsheet.transform", "artifact.create"]
    )
    if mode in {RetrievalMode.INTERNAL, RetrievalMode.HYBRID}:
        capabilities = ["knowledge.retrieve", *capabilities]
    if mode in {RetrievalMode.WEB, RetrievalMode.HYBRID}:
        capabilities = ["web.search", "web.open", *capabilities]
    return list(dict.fromkeys(capabilities))


def is_explicit_public_web_request(message: str) -> bool:
    """Honor an explicit public-information web request without stealing domain work."""

    text = _compact(message)
    if is_recruitment_web_request(text):
        return False
    explicit_web_action = is_explicit_web_action(text)
    public_source = any(term in text for term in (
        "公开", "官网", "新闻", "动态", "网页", "网站", "媒体", "公告",
    ))
    return explicit_web_action and public_source


def extract_weather_location(message: str) -> str | None:
    text = _compact(message)
    # Prefer an explicit place before a temporal anchor.  This avoids treating
    # the rest of a compound question (for example “今天周几，台风大不大”)
    # as a location merely because it is short Chinese text.
    for temporal in ("今天", "明天", "后天", "现在", "当前"):
        index = text.find(temporal)
        if index < 0:
            continue
        prefix = re.split(r"[，。！？、；：,.!?;:~～…\-]", text[:index])[-1]
        prefix = re.sub(
            r"^(?:(?:任务目标|请问|帮我|麻烦|查询|查一下|看看)(?:[:：])?)+",
            "",
            prefix,
        )
        if 2 <= len(prefix) <= 10 and not any(
            marker in prefix for marker in ("这里", "那里", "本地", "当地", "我这", "外面")
        ):
            return prefix
    explicit = re.search(r"([\u4e00-\u9fff]{2,8}(?:省|市|区|县))", text)
    if explicit:
        return explicit.group(1)
    return None


class LayeredRouter:
    """Stable semantic router; implementation names never escape its contract."""

    def interpret(self, message: str) -> TurnInterpretation:
        text = _compact(message)

        if is_product_help_question(text):
            return TurnInterpretation(
                business_intent=BusinessIntent.PRODUCT_HELP,
                execution_route=ExecutionRoute.CONVERSATION,
                confidence=0.98,
                reason_code="PRODUCT_HELP_EXPLICIT",
                required_capabilities=[],
                data_class="public",
                matched_layer="l0_product_help_guard",
            )

        artifact_intent = requested_artifact_intent(text)
        evidence_mode = requested_evidence_mode(text)
        if artifact_intent is not None and evidence_mode != RetrievalMode.NONE:
            capabilities = artifact_evidence_capabilities(artifact_intent, evidence_mode)
            return TurnInterpretation(
                business_intent=artifact_intent,
                execution_route=ExecutionRoute.HR_WORKFLOW,
                confidence=0.98,
                reason_code="ARTIFACT_WITH_GROUNDED_EVIDENCE",
                requires_freshness=evidence_mode in {RetrievalMode.WEB, RetrievalMode.HYBRID},
                required_capabilities=capabilities,
                suggested_capabilities=capabilities,
                retrieval_mode=evidence_mode,
                data_class="internal_hr",
                risk_flags=["formal_release_requires_approval"] if artifact_intent == BusinessIntent.DOCUMENT_GENERATION else [],
                matched_layer="l0_composite_route_guard",
            )
        if artifact_intent is not None:
            capabilities = artifact_evidence_capabilities(artifact_intent, RetrievalMode.NONE)
            return TurnInterpretation(
                business_intent=artifact_intent,
                execution_route=ExecutionRoute.HR_WORKFLOW,
                confidence=0.96,
                reason_code="ARTIFACT_CREATION_EXPLICIT",
                required_capabilities=capabilities,
                suggested_capabilities=capabilities,
                retrieval_mode=RetrievalMode.NONE,
                data_class="internal_hr",
                risk_flags=["formal_release_requires_approval"] if artifact_intent == BusinessIntent.DOCUMENT_GENERATION else [],
                matched_layer="l0_artifact_route_guard",
            )

        if is_recruitment_web_request(text):
            missing_slots = recruitment_web_missing_slots(text)
            capabilities = recruitment_web_capabilities()
            return TurnInterpretation(
                business_intent=BusinessIntent.RECRUITMENT_SCREENING,
                execution_route=(ExecutionRoute.CLARIFY if missing_slots else ExecutionRoute.HR_WORKFLOW),
                confidence=0.98,
                reason_code="RECRUITMENT_WEB_SOURCING_EXPLICIT",
                missing_slots=missing_slots,
                risk_flags=["employment_decision", "candidate_pii", "human_review_required"],
                requires_freshness=True,
                required_capabilities=[] if missing_slots else capabilities,
                suggested_capabilities=capabilities,
                retrieval_mode=RetrievalMode.WEB,
                data_class="restricted_hr",
                matched_layer="l0_composite_route_guard",
            )

        if is_explicit_public_web_request(text):
            return TurnInterpretation(
                business_intent=BusinessIntent.PUBLIC_CURRENT_INFO,
                execution_route=ExecutionRoute.PUBLIC_INFO,
                confidence=0.98,
                reason_code="PUBLIC_WEB_REQUEST_EXPLICIT",
                requires_freshness=True,
                required_capabilities=["web.search", "web.open"],
                suggested_capabilities=["web.search", "web.open"],
                retrieval_mode=RetrievalMode.WEB,
                data_class="public",
                matched_layer="l0_public_web_guard",
            )

        if is_policy_content_question(text):
            return TurnInterpretation(
                business_intent=BusinessIntent.POLICY_QUESTION,
                execution_route=ExecutionRoute.HR_EVIDENCE,
                confidence=0.95,
                reason_code="POLICY_CONTENT_REQUEST",
                required_capabilities=["knowledge.retrieve", "evidence.answer"],
                suggested_capabilities=["knowledge.retrieve", "evidence.answer"],
                retrieval_mode=RetrievalMode.INTERNAL,
                data_class="internal_hr",
                matched_layer="l1_rules",
            )

        # L0/L1: HR and sensitive work wins over greeting words.
        if _contains(text, _RECRUITMENT_TERMS):
            return TurnInterpretation(
                business_intent=BusinessIntent.RECRUITMENT_SCREENING,
                execution_route=ExecutionRoute.HR_WORKFLOW,
                confidence=0.96,
                reason_code="RECRUITMENT_WORK_EXPLICIT",
                risk_flags=["employment_decision", "candidate_pii"],
                required_capabilities=["file.extract", "recruitment.evaluate"],
                suggested_capabilities=["file.extract", "recruitment.evaluate"],
                retrieval_mode=RetrievalMode.ATTACHMENTS,
                data_class="restricted_hr",
                matched_layer="l1_rules",
            )
        # “通知/方案” may be the name of a source. A knowledge-anchored
        # question asks for evidence and must not create a new artifact.
        if _is_explicit_knowledge_question(text):
            return TurnInterpretation(
                business_intent=BusinessIntent.POLICY_QUESTION,
                execution_route=ExecutionRoute.HR_EVIDENCE,
                confidence=0.97,
                reason_code="EXPLICIT_KNOWLEDGE_QUESTION",
                required_capabilities=["knowledge.retrieve", "evidence.answer"],
                data_class="internal_hr",
                matched_layer="l1_rules",
            )
        if _contains(text, _DOCUMENT_TERMS):
            return TurnInterpretation(
                business_intent=BusinessIntent.DOCUMENT_GENERATION,
                execution_route=ExecutionRoute.HR_WORKFLOW,
                confidence=0.91,
                reason_code="HR_DOCUMENT_EXPLICIT",
                data_class="internal_hr",
                matched_layer="l1_rules",
            )
        if _contains(text, _POLICY_TERMS):
            return TurnInterpretation(
                business_intent=BusinessIntent.POLICY_QUESTION,
                execution_route=ExecutionRoute.HR_EVIDENCE,
                confidence=0.92,
                reason_code="HR_POLICY_EVIDENCE_REQUIRED",
                data_class="internal_hr",
                matched_layer="l1_rules",
            )

        if _contains(text, _WEATHER_TERMS):
            location = extract_weather_location(message)
            return TurnInterpretation(
                business_intent=BusinessIntent.PUBLIC_CURRENT_INFO,
                execution_route=(ExecutionRoute.PUBLIC_INFO if location else ExecutionRoute.CLARIFY),
                confidence=0.96,
                reason_code="CURRENT_WEATHER_EXPLICIT",
                missing_slots=[] if location else ["location"],
                requires_freshness=True,
                required_capabilities=["weather.current"],
                suggested_capabilities=["weather.current"],
                retrieval_mode=RetrievalMode.WEB,
                data_class="public",
                matched_layer="l1_rules",
            )

        if _contains(text, _PUBLIC_INFO_TERMS):
            return TurnInterpretation(
                business_intent=BusinessIntent.PUBLIC_CURRENT_INFO,
                execution_route=ExecutionRoute.PUBLIC_INFO,
                confidence=0.91,
                reason_code="PUBLIC_FRESH_INFO_EXPLICIT",
                requires_freshness=True,
                required_capabilities=["web.search", "web.open"],
                suggested_capabilities=["web.search", "web.open"],
                retrieval_mode=RetrievalMode.WEB,
                data_class="public",
                matched_layer="l1_rules",
            )

        greeting = text.strip("，。！？、；：,.!?;:~～…")
        if greeting in _PURE_CONVERSATION:
            return TurnInterpretation(
                business_intent=BusinessIntent.CONVERSATION,
                execution_route=ExecutionRoute.CONVERSATION,
                confidence=0.98,
                reason_code="PURE_CONVERSATION",
                data_class="public",
                matched_layer="l1_rules",
            )

        # L2 semantic seeds and L3 small-model classification plug in here.
        return TurnInterpretation(
            business_intent=BusinessIntent.UNCLEAR,
            execution_route=ExecutionRoute.CLARIFY,
            confidence=0.55,
            reason_code="SEMANTIC_OR_MODEL_CLASSIFICATION_PENDING",
            missing_slots=["objective"],
            data_class="unknown",
            matched_layer="fallback",
        )


def _is_explicit_knowledge_question(text: str) -> bool:
    anchored = any(term in text for term in ("根据知识库", "知识库中", "刚发布的", "政策依据", "原文怎么说"))
    asks = any(term in text for term in ("是什么", "分别是什么", "怎么规定", "是否", "多少", "请告诉", "请回答"))
    creates = any(term in text for term in ("生成word", "生成公文", "起草", "撰写", "制作文件", "导出word", "修改正文"))
    return anchored and asks and not creates


def is_product_help_question(message: str) -> bool:
    """High-precision guard for questions about UniHR itself, not HR source content."""

    text = _compact(message)
    product_anchor = any(term in text for term in (
        "unihr", "agentos", "产品验收", "产品经理", "系统能力", "产品能力",
        "这个功能", "该功能", "这项功能", "为什么需要", "为什么要做",
    ))
    mechanism = any(term in text for term in (
        "固定视觉评测", "后台入库任务", "单文件失败重试", "失败隔离",
        "上下文压缩", "状态外置", "路由机制", "模型调用记录", "agent run",
    ))
    explanation = any(term in text for term in (
        "说明", "解释", "为什么", "有什么用", "如何工作", "怎么实现", "产品机制",
    ))
    return product_anchor and mechanism and explanation


def is_policy_content_question(message: str) -> bool:
    """Recognize requests for governed HR source content, not product capability help."""

    compact = _compact(message)
    policy_anchor = any(term in compact for term in (
        "政策", "制度", "办法", "细则", "条例", "规定", "通知", "实施意见",
        "方案", "发文", "文件依据", "政策依据",
    ))
    information_action = any(term in compact for term in (
        "是否", "能否", "还能", "可以", "可不可以", "怎么", "如何", "什么",
        "多少", "按吗", "按哪个", "执行", "适用", "有效", "生效", "失效",
        "废止", "依据", "要求", "条件", "材料", "流程", "期限", "标准",
        "了解", "查询", "咨询", "介绍", "讲讲", "说说", "看看", "内容",
    ))
    artifact_action = any(term in compact for term in (
        "生成公文", "生成word", "起草", "撰写", "制作文件", "导出word", "修改正文",
    ))
    return policy_anchor and information_action and not artifact_action
