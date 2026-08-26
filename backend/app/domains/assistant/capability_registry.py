from __future__ import annotations

from dataclasses import dataclass

from .contracts import BusinessIntent, ExecutionRoute, RetrievalMode


@dataclass(frozen=True)
class CapabilityDescriptor:
    """A product capability advertised to the semantic router.

    The router selects these stable product capabilities.  Tool and function
    names remain an implementation detail owned by the descriptor.
    """

    capability_id: str
    label: str
    intent: BusinessIntent
    route: ExecutionRoute
    description: str
    default_tools: tuple[str, ...] = ()
    default_retrieval_mode: RetrievalMode = RetrievalMode.NONE
    requires_freshness: bool = False
    version: str = "1.0.0"
    lifecycle: str = "active"
    risk_level: str = "low"
    owner: str = "Agent Platform"
    input_contract: str = "用户自然语言请求"
    output_contract: str = "可追溯的对话回答"
    human_review_policy: str = "按任务风险决定"

    def model_card(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "intent": self.intent.value,
            "description": self.description,
        }

    def operations_card(self) -> dict[str, object]:
        """Expose the governed product contract without leaking executors."""

        return {
            "capability_id": self.capability_id,
            "label": self.label,
            "intent": self.intent.value,
            "route": self.route.value,
            "description": self.description,
            "version": self.version,
            "lifecycle": self.lifecycle,
            "risk_level": self.risk_level,
            "owner": self.owner,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "human_review_policy": self.human_review_policy,
            "tools": list(self.default_tools),
            "retrieval_mode": self.default_retrieval_mode.value,
            "requires_freshness": self.requires_freshness,
        }


class TaskCapabilityRegistry:
    """Single source of truth for ordinary Agent capability selection."""

    def __init__(self, descriptors: tuple[CapabilityDescriptor, ...] | None = None) -> None:
        self._descriptors = descriptors or _DEFAULT_DESCRIPTORS
        self._by_intent = {item.intent: item for item in self._descriptors}
        if len(self._by_intent) != len(self._descriptors):
            raise ValueError("duplicate_business_intent")

    def descriptor_for(self, intent: BusinessIntent) -> CapabilityDescriptor:
        return self._by_intent[intent]

    def candidates_for(
        self,
        primary: BusinessIntent,
        secondary: list[BusinessIntent] | tuple[BusinessIntent, ...] = (),
    ) -> list[CapabilityDescriptor]:
        ordered = [primary, *secondary]
        if primary != BusinessIntent.CONVERSATION:
            ordered.append(BusinessIntent.CONVERSATION)
        result: list[CapabilityDescriptor] = []
        seen: set[str] = set()
        for intent in ordered:
            descriptor = self._by_intent.get(intent)
            if descriptor is None or descriptor.capability_id in seen:
                continue
            seen.add(descriptor.capability_id)
            result.append(descriptor)
        return result[:4]

    def model_catalog(self) -> list[dict[str, object]]:
        return [item.model_card() for item in self._descriptors]

    def operations_catalog(self) -> list[dict[str, object]]:
        return [item.operations_card() for item in self._descriptors]

    def descriptors(self) -> tuple[CapabilityDescriptor, ...]:
        return self._descriptors


_DEFAULT_DESCRIPTORS = (
    CapabilityDescriptor(
        "general_conversation", "通用对话", BusinessIntent.CONVERSATION,
        ExecutionRoute.CONVERSATION,
        "闲聊、常识解释和不需要内部证据、实时网页或业务文件的普通问答。",
        owner="通用助手", output_contract="自然、直接的对话回答",
    ),
    CapabilityDescriptor(
        "product_help", "产品帮助", BusinessIntent.PRODUCT_HELP,
        ExecutionRoute.CONVERSATION,
        "解释 UniHR 自身已经注册的功能、页面、机制和使用方式。",
        owner="产品运营", input_contract="关于 UniHR 产品的提问",
        output_contract="仅依据能力台账生成的产品说明",
    ),
    CapabilityDescriptor(
        "public_information", "联网信息", BusinessIntent.PUBLIC_CURRENT_INFO,
        ExecutionRoute.PUBLIC_INFO,
        "回答天气、预警、日期相关实时状况、新闻、官网动态等需要新鲜公开信息的问题；缺少地点等关键范围时先澄清。",
        ("web.search", "web.open"), RetrievalMode.WEB, True,
        owner="联网信息 Agent", input_contract="公开信息问题与必要的时间、地点范围",
        output_contract="带来源和观测时间的公开信息回答",
    ),
    CapabilityDescriptor(
        "policy_evidence", "政策证据问答", BusinessIntent.POLICY_QUESTION,
        ExecutionRoute.HR_EVIDENCE,
        "依据受控内部知识库回答政策、制度、办法、资格、流程、期限和薪酬口径。",
        ("knowledge.retrieve", "evidence.answer"), RetrievalMode.INTERNAL,
        risk_level="medium", owner="Policy RAG Agent",
        input_contract="政策问题、适用对象与必要的组织范围",
        output_contract="带引用、版本提示和不确定性说明的政策回答",
    ),
    CapabilityDescriptor(
        "recruitment_screening", "招聘筛选", BusinessIntent.RECRUITMENT_SCREENING,
        ExecutionRoute.HR_WORKFLOW,
        "解析候选人材料，按岗位证据评分、比较、复核或从授权来源寻找候选线索。",
        ("file.extract", "recruitment.evaluate"), RetrievalMode.ATTACHMENTS,
        risk_level="high", owner="招聘筛选 Agent",
        input_contract="岗位要求与一份或多份候选人材料",
        output_contract="证据化评分、排名、待核实问题和 Excel 报告",
        human_review_policy="必须由招聘人员复核，不自动录用或淘汰",
    ),
    CapabilityDescriptor(
        "document_generation", "公文材料", BusinessIntent.DOCUMENT_GENERATION,
        ExecutionRoute.HR_WORKFLOW,
        "创建或修改 Word、公文、通知、请示、会议材料及其结构化表格。",
        ("document.compose", "artifact.create"),
        risk_level="medium", owner="公文材料 Agent",
        input_contract="文种、事实、受众、语气与版式要求",
        output_contract="Word 文档、事实台账和内容/版式校验摘要",
        human_review_policy="正式发文前必须人工复核",
    ),
    CapabilityDescriptor(
        "spreadsheet_processing", "表格处理", BusinessIntent.SPREADSHEET_PROCESSING,
        ExecutionRoute.HR_WORKFLOW,
        "从零创建或修改 Excel，执行公式、数据校验、样式、重算与渲染验证。",
        ("spreadsheet.inspect", "spreadsheet.transform"), RetrievalMode.ATTACHMENTS,
        risk_level="medium", owner="表格处理 Agent",
        input_contract="文字需求和可选源工作簿、字段及样式约束",
        output_contract="可计算 Excel、源结果差异与 Harness 校验摘要",
    ),
    CapabilityDescriptor(
        "image_understanding", "图片理解", BusinessIntent.IMAGE_UNDERSTANDING,
        ExecutionRoute.HR_WORKFLOW,
        "读取图片或扫描材料，生成带位置、页码、置信度与待复核标记的证据。",
        ("vision.understand",), RetrievalMode.ATTACHMENTS,
        risk_level="medium", owner="视觉证据 Agent",
        input_contract="图片或扫描 PDF 与提取目标",
        output_contract="结构化字段、位置证据、置信度和待复核项",
    ),
    CapabilityDescriptor(
        "skill_authoring", "Skill 制作", BusinessIntent.SKILL_AUTHORING,
        ExecutionRoute.HR_WORKFLOW,
        "把自然语言需求或当前对话中已经验证过的工作过程沉淀为可复用、可版本化、可校验的 Skill。",
        ("skill.author", "skill.validate"), RetrievalMode.NONE,
        risk_level="medium", owner="Skill Authoring Agent",
        input_contract="一段可复用流程描述，或包含已完成工作过程的当前对话",
        output_contract="待复核 Skill、触发条件、输入输出契约与 Harness 校验摘要",
        human_review_policy="发布到组织前必须经过人工复核",
    ),
    CapabilityDescriptor(
        "clarify_request", "补充需求", BusinessIntent.UNCLEAR,
        ExecutionRoute.CLARIFY,
        "只有在语义目标或执行所需关键输入确实缺失时，向用户提出最少量澄清问题。",
        owner="路由与任务理解", input_contract="目标不完整或关键执行条件缺失的请求",
        output_contract="一个最小必要的澄清问题",
    ),
)


def default_task_capability_registry() -> TaskCapabilityRegistry:
    return TaskCapabilityRegistry()
