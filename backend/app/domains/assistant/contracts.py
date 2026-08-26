from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class BusinessIntent(StrEnum):
    CONVERSATION = "conversation"
    PRODUCT_HELP = "product_help"
    PUBLIC_CURRENT_INFO = "public_current_info"
    POLICY_QUESTION = "policy_question"
    RECRUITMENT_SCREENING = "recruitment_screening"
    DOCUMENT_GENERATION = "document_generation"
    SPREADSHEET_PROCESSING = "spreadsheet_processing"
    IMAGE_UNDERSTANDING = "image_understanding"
    SKILL_AUTHORING = "skill_authoring"
    UNCLEAR = "unclear"

    @classmethod
    def _missing_(cls, value: object):
        """Keep retired annual-adjustment records readable as generic sheets."""

        if value == "annual_adjustment":
            return cls.SPREADSHEET_PROCESSING
        return None


class ExecutionRoute(StrEnum):
    CONVERSATION = "conversation"
    PUBLIC_INFO = "public_info"
    HR_EVIDENCE = "hr_evidence"
    HR_WORKFLOW = "hr_workflow"
    CLARIFY = "clarify"
    REFUSE_OR_GUARD = "refuse_or_guard"


class TurnAction(StrEnum):
    """What the user wants to do *this turn*, independent of the HR domain."""

    START_NEW = "start_new"
    CONTINUE_WITH_TOOLS = "continue_with_tools"
    COMPARE_EXISTING = "compare_existing"
    EXPLAIN_EXISTING = "explain_existing"
    CONFIRM_EXISTING = "confirm_existing"
    REVISE_EXISTING = "revise_existing"
    REGENERATE = "regenerate"
    EXPORT_EXISTING = "export_existing"
    SOCIAL = "social"
    CANCEL = "cancel"
    CLARIFY = "clarify"


class ExecutionMode(StrEnum):
    """Governed side-effect policy derived from ``TurnAction``."""

    EXECUTE = "execute"
    REUSE_RESULT = "reuse_result"
    MUTATE = "mutate"
    NO_OP = "no_op"
    CLARIFY = "clarify"


class DeliveryMode(StrEnum):
    """How the answer is delivered, independent of domain and turn action."""

    UNSPECIFIED = "unspecified"
    INLINE_RESPONSE = "inline_response"
    INLINE_TRANSFORM = "inline_transform"
    CREATE_ARTIFACT = "create_artifact"
    MUTATE_ARTIFACT = "mutate_artifact"
    REUSE_ARTIFACT = "reuse_artifact"
    EXTERNAL_ACTION = "external_action"


class RetrievalMode(StrEnum):
    """Where evidence must come from, independent of the business owner."""

    NONE = "none"
    ATTACHMENTS = "attachments"
    INTERNAL = "internal"
    WEB = "web"
    HYBRID = "hybrid"


class TaskIntent(BaseModel):
    """Normalized product request used to select a registered capability."""

    objective: str = ""
    domain: BusinessIntent
    action: TurnAction = TurnAction.START_NEW
    delivery_mode: DeliveryMode = DeliveryMode.UNSPECIFIED
    deliverables: list[str] = Field(default_factory=list, max_length=8)
    constraints: list[str] = Field(default_factory=list, max_length=8)
    requires_freshness: bool = False
    retrieval_mode: RetrievalMode = RetrievalMode.NONE
    context_mode: str = "standalone"
    missing_inputs: list[str] = Field(default_factory=list, max_length=8)


class TurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: str | None = None
    case_id: str | None = None


class TurnInterpretation(BaseModel):
    business_intent: BusinessIntent
    execution_route: ExecutionRoute
    confidence: float = Field(ge=0, le=1)
    reason_code: str
    missing_slots: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    requires_freshness: bool = False
    required_capabilities: list[str] = Field(default_factory=list)
    data_class: str = "public"
    matched_layer: str
    secondary_intents: list[str] = Field(default_factory=list)
    context_mode: str = "standalone"
    suggested_capabilities: list[str] = Field(default_factory=list)
    reason_summary: str = ""
    turn_action: TurnAction = TurnAction.START_NEW
    execution_mode: ExecutionMode = ExecutionMode.EXECUTE
    delivery_mode: DeliveryMode = DeliveryMode.UNSPECIFIED
    retrieval_mode: RetrievalMode = RetrievalMode.NONE
    resolved_request: str = ""
    retrieval_queries: list[str] = Field(default_factory=list, max_length=4)
    priority_constraints: list[str] = Field(default_factory=list, max_length=8)
    task_intent: TaskIntent | None = None
    candidate_capability_ids: list[str] = Field(default_factory=list, max_length=4)
    selected_capability_id: str = ""
    capability_selection_reason: str = ""
    routing_fallback_used: bool = False


class Citation(BaseModel):
    title: str
    url: str
    observed_at: str


class TurnResponse(BaseModel):
    interpretation: TurnInterpretation
    answer: str | None = None
    next_action: str
    citations: list[Citation] = Field(default_factory=list)
    capability_status: str | None = None
