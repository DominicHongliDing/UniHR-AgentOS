from fastapi import APIRouter, Depends

from app.api.deps import get_current_principal
from app.domains.identity import Principal
from app.domains.assistant.contracts import Citation, ExecutionRoute, TurnRequest, TurnResponse
from app.domains.assistant.router import LayeredRouter
from app.runtime.public_info import build_public_info_executor


router = APIRouter(prefix="/assistant", tags=["assistant"])
_router = LayeredRouter()
_public_info = build_public_info_executor()


@router.post("/turn", response_model=TurnResponse)
def handle_turn(
    request: TurnRequest,
    _principal: Principal = Depends(get_current_principal),
) -> TurnResponse:
    interpretation = _router.interpret(request.message)
    if interpretation.execution_route == ExecutionRoute.CLARIFY:
        answer = (
            "请告诉我需要查询天气的城市或区县。"
            if "location" in interpretation.missing_slots
            else "你希望我协助完成哪一项人事工作？"
        )
        return TurnResponse(
            interpretation=interpretation,
            answer=answer,
            next_action="collect_missing_input",
        )
    if interpretation.execution_route == ExecutionRoute.CONVERSATION:
        return TurnResponse(
            interpretation=interpretation,
            answer="你好，我可以陪你聊聊，也可以协助处理简历筛选、政策依据、通用表格和公文材料。",
            next_action="respond",
        )
    if interpretation.execution_route == ExecutionRoute.PUBLIC_INFO:
        result = _public_info.answer(request.message)
        return TurnResponse(
            interpretation=interpretation,
            answer=result.answer,
            next_action="respond",
            capability_status=result.capability_status,
            citations=[Citation(**citation) for citation in result.citations],
        )
    if interpretation.execution_route == ExecutionRoute.HR_EVIDENCE:
        return TurnResponse(
            interpretation=interpretation,
            answer=None,
            next_action="start_evidence_query",
        )
    return TurnResponse(
        interpretation=interpretation,
        answer=None,
        next_action="create_or_continue_case",
    )
