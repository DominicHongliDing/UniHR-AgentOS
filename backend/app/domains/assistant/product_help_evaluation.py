from __future__ import annotations

import json

from pydantic import BaseModel

from app.models.gateway import ModelGatewayError, ModelReply

from .model_router import GovernedModelRouter


class ProductHelpRouteCaseResult(BaseModel):
    case_id: str
    message: str
    expected_intent: str
    actual_intent: str
    expected_route: str
    actual_route: str
    passed: bool


class ProductHelpRouteEvalReport(BaseModel):
    status: str
    total: int
    passed: int
    accuracy: float
    cases: list[ProductHelpRouteCaseResult]


class _FixedGateway:
    def __init__(self, intent: str) -> None:
        self.intent = intent

    def complete(self, messages, **kwargs):
        content = json.dumps({
            "primary_intent": self.intent,
            "secondary_intents": [],
            "context_mode": "standalone",
            "missing_inputs": [],
            "risk_level": "low",
            "data_class": "public",
            "required_capabilities": ["vision.understand"] if self.intent == "image_understanding" else [],
            "confidence": 0.91,
            "reason_summary": "固定评测注入的候选路由",
            "turn_action": "start_new",
            "resolved_request": "",
            "retrieval_queries": [],
            "priority_constraints": [],
        }, ensure_ascii=False)
        return ModelReply(content, "fixed-router-eval", 0, {})


class _UnavailableGateway:
    def complete(self, messages, **kwargs):
        raise ModelGatewayError("fixed_router_fallback")


_CASES = (
    (
        "product-help-scan-governance",
        "P1-A Slice 3 产品验收：请用产品经理能听懂的话说明，为什么扫描政策需要固定视觉评测、后台入库任务和单文件失败重试？",
        "image_understanding",
        "product_help",
        "conversation",
        False,
    ),
    (
        "product-help-fallback",
        "请说明 UniHR 为什么需要上下文压缩、状态外置和路由机制。",
        "unclear",
        "product_help",
        "conversation",
        True,
    ),
    (
        "real-policy-question",
        "根据知识库中刚发布的扫描政策通知，会议地点是什么？不要猜。",
        "policy_question",
        "policy_question",
        "hr_evidence",
        False,
    ),
    (
        "real-image-request",
        "请读取我上传的扫描图片并提取会议日期，看不清不要猜。",
        "image_understanding",
        "image_understanding",
        "hr_workflow",
        False,
    ),
)


def run_product_help_route_evaluation() -> ProductHelpRouteEvalReport:
    results: list[ProductHelpRouteCaseResult] = []
    for case_id, message, candidate_intent, expected_intent, expected_route, fallback in _CASES:
        gateway = _UnavailableGateway() if fallback else _FixedGateway(candidate_intent)
        decision = GovernedModelRouter(gateway).interpret(message)
        passed = (
            decision.business_intent.value == expected_intent
            and decision.execution_route.value == expected_route
        )
        results.append(ProductHelpRouteCaseResult(
            case_id=case_id,
            message=message,
            expected_intent=expected_intent,
            actual_intent=decision.business_intent.value,
            expected_route=expected_route,
            actual_route=decision.execution_route.value,
            passed=passed,
        ))
    passed_count = sum(item.passed for item in results)
    return ProductHelpRouteEvalReport(
        status="passed" if passed_count == len(results) else "needs_attention",
        total=len(results),
        passed=passed_count,
        accuracy=passed_count / len(results) if results else 0,
        cases=results,
    )
