from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Sequence, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models.gateway import ModelGateway
from app.domains.runs import RunPayloadStore, RunPointer
from app.observability import emit_tool_progress

from .contracts import KnowledgeAccessContext
from .retriever import PolicyKnowledgeRetriever, RetrievalBundle
from .vector_index import SearchHit


@dataclass(frozen=True)
class PolicyAttachmentEvidence:
    title: str
    text: str


@dataclass(frozen=True)
class PolicyRagRequest:
    question: str
    resolved_question: str = ""
    query_variants: Sequence[str] = ()
    priority_constraints: Sequence[str] = ()
    conversation_messages: Sequence[dict[str, Any]] = ()
    attachments: Sequence[PolicyAttachmentEvidence] = ()
    skill_prompt: str = ""
    access: KnowledgeAccessContext | None = None
    payload_scope: RunPointer | None = None


@dataclass(frozen=True)
class EvidenceRequirement:
    code: str
    label: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class PolicyEvidencePlan:
    queries: list[str]
    requirements: list[EvidenceRequirement]
    top_k: int
    candidate_k: int
    as_of_date: str | None
    current_only: bool
    reason: str


@dataclass(frozen=True)
class PolicySufficiency:
    sufficient: bool
    missing_requirements: list[str]
    requirement_coverage: dict[str, bool]
    lexical_coverage: float
    reason: str


@dataclass(frozen=True)
class PolicyClaimReview:
    status: str
    answer: str
    direct_count: int
    partial_count: int
    missing_count: int
    invalid_citations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PolicyConflictItem:
    left_reference: str
    right_reference: str
    kind: str
    reason: str
    left_values: tuple[str, ...] = ()
    right_values: tuple[str, ...] = ()
    # Preserve the value-to-source pairing.  The flattened fields above remain
    # useful for traces and compatibility, but cannot safely drive a grouped
    # UI summary when one source contains more than one numeric claim.
    value_pairs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PolicyConflictReview:
    status: str
    requires_human_review: bool
    items: list[PolicyConflictItem] = field(default_factory=list)

    @property
    def conflict_count(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class PolicyTemporalReview:
    status: str
    requires_human_review: bool
    reason: str = ""
    references: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyRagResult:
    answer: str
    trace: list[dict[str, Any]]
    plan: PolicyEvidencePlan
    sufficiency: PolicySufficiency
    claim_review: PolicyClaimReview
    evidence_count: int
    attempts: int
    conflict_review: PolicyConflictReview = field(
        default_factory=lambda: PolicyConflictReview("none", False)
    )
    temporal_review: PolicyTemporalReview = field(
        default_factory=lambda: PolicyTemporalReview("not_requested", False)
    )


@dataclass(frozen=True)
class _EvidenceItem:
    reference_id: str
    chunk_id: str
    title: str
    citation: str
    text: str
    score: float
    source_id: str = ""
    policy_status: str = "unknown"
    source_version: str | None = None
    publication_date: str | None = None
    effective_date: str | None = None
    expiry_date: str | None = None
    authority_level: str = "unknown"
    version_id: str | None = None
    publication_id: str | None = None


class _PolicyRagState(TypedDict, total=False):
    request: PolicyRagRequest
    plan: PolicyEvidencePlan
    bundle: RetrievalBundle
    sufficiency: PolicySufficiency
    retry_queries: list[str]
    attempts: int
    evidence: list[_EvidenceItem]
    evidence_ref: dict[str, Any]
    raw_answer: str
    answer: str
    review: PolicyClaimReview
    conflict_review: PolicyConflictReview
    temporal_review: PolicyTemporalReview
    trace: list[dict[str, Any]]


class PolicyRagEngine:
    """Answer policy questions through one bounded evidence-and-repair interface."""

    def __init__(
        self,
        retriever: PolicyKnowledgeRetriever,
        gateway: ModelGateway,
        *,
        answer_model: str,
        max_retrieval_attempts: int = 2,
        payload_store: RunPayloadStore | None = None,
    ) -> None:
        self.retriever = retriever
        self.gateway = gateway
        self.answer_model = answer_model
        self.max_retrieval_attempts = max(1, min(max_retrieval_attempts, 3))
        self.payload_store = payload_store
        self.graph = self._compile()

    def run(self, request: PolicyRagRequest) -> PolicyRagResult:
        state = self.graph.invoke({
            "request": request,
            "retry_queries": [],
            "attempts": 0,
            "trace": [],
        })
        return PolicyRagResult(
            answer=state["answer"],
            trace=state["trace"],
            plan=state["plan"],
            sufficiency=state["sufficiency"],
            claim_review=state["review"],
            evidence_count=len(self._evidence(state)),
            attempts=state["attempts"],
            conflict_review=state.get("conflict_review") or PolicyConflictReview("none", False),
            temporal_review=state.get("temporal_review") or PolicyTemporalReview("not_requested", False),
        )

    def _compile(self):
        builder = StateGraph(_PolicyRagState)
        builder.add_node("evidence_plan", self._plan_node)
        builder.add_node("hybrid_retrieve", self._retrieve_node)
        builder.add_node("sufficiency_gate", self._sufficiency_node)
        builder.add_node("rewrite_missing_evidence", self._rewrite_node)
        builder.add_node("effective_date_gate", self._temporal_node)
        builder.add_node("conflict_gate", self._conflict_node)
        builder.add_node("grounded_answer", self._answer_node)
        builder.add_node("claim_alignment", self._align_node)
        builder.add_node("finalize", self._finalize_node)
        builder.add_edge(START, "evidence_plan")
        builder.add_edge("evidence_plan", "hybrid_retrieve")
        builder.add_edge("hybrid_retrieve", "sufficiency_gate")
        builder.add_conditional_edges(
            "sufficiency_gate",
            self._after_sufficiency,
            {"rewrite": "rewrite_missing_evidence", "answer": "effective_date_gate"},
        )
        builder.add_edge("rewrite_missing_evidence", "hybrid_retrieve")
        builder.add_edge("effective_date_gate", "conflict_gate")
        builder.add_edge("conflict_gate", "grounded_answer")
        builder.add_edge("grounded_answer", "claim_alignment")
        builder.add_edge("claim_alignment", "finalize")
        builder.add_edge("finalize", END)
        return builder.compile(name="unihr-policy-evidence-graph")

    @staticmethod
    def _plan_node(state: _PolicyRagState) -> dict[str, Any]:
        request = state["request"]
        emit_tool_progress(
            "tool.policy.plan.started",
            "正在把用户问题拆成政策证据需求和检索查询",
        )
        plan = _plan_evidence(request)
        emit_tool_progress(
            "tool.policy.plan.completed",
            f"已规划 {len(plan.queries)} 组查询和 {len(plan.requirements)} 个证据维度",
            status="completed",
            query_count=len(plan.queries),
            requirement_count=len(plan.requirements),
            top_k=plan.top_k,
            current_only=plan.current_only,
        )
        return {
            "plan": plan,
            "trace": [*state.get("trace", []), _trace(
                "evidence.plan", "completed",
                f"已规划 {len(plan.queries)} 条检索查询和 {len(plan.requirements)} 个证据维度",
                queries=plan.queries,
                requirements=[item.label for item in plan.requirements],
                top_k=plan.top_k,
                candidate_k=plan.candidate_k,
                as_of_date=plan.as_of_date,
                current_only=plan.current_only,
            )],
        }

    def _retrieve_node(self, state: _PolicyRagState) -> dict[str, Any]:
        request = state["request"]
        plan = state["plan"]
        attempt = int(state.get("attempts") or 0) + 1
        bundle = self.retriever.retrieve(
            request.question,
            resolved_query=request.resolved_question,
            query_variants=[*plan.queries, *state.get("retry_queries", [])],
            limit=min(12, plan.top_k + (attempt - 1) * 2),
            candidate_limit=min(100, plan.candidate_k + (attempt - 1) * 20),
            as_of_date=plan.as_of_date,
            current_only=plan.current_only,
            max_per_source=3,
            access=request.access,
        )
        return {
            "bundle": bundle,
            "attempts": attempt,
            "trace": [*state.get("trace", []), _trace(
                "knowledge.retrieve", "completed" if bundle.hits else "insufficient",
                f"第 {attempt} 轮混合检索得到 {len(bundle.hits)} 个政策片段",
                attempt=attempt,
                queries=bundle.queries,
                retrieval_mode="multi_query_vector_bm25_rrf_cross_encoder",
                candidate_count=bundle.candidate_count,
                selected_count=len(bundle.hits),
                filtered_by_policy_metadata=bundle.filtered_count,
                lexical_coverage=round(bundle.coverage, 3),
                rerank_model=bundle.rerank_model,
                filtered_by_relevance_gate=bundle.relevance_filtered_count,
                filtered_by_relative_rerank=bundle.rerank_filtered_count,
            )],
        }

    @staticmethod
    def _sufficiency_node(state: _PolicyRagState) -> dict[str, Any]:
        request = state["request"]
        bundle = state["bundle"]
        sufficiency = _assess_sufficiency(
            state["plan"], bundle, _evidence_from_hits(bundle.hits), request.attachments,
        )
        emit_tool_progress(
            "tool.policy.sufficiency.completed",
            (
                "政策证据覆盖已满足回答要求"
                if sufficiency.sufficient
                else f"政策证据仍缺少 {len(sufficiency.missing_requirements)} 个维度"
            ),
            status="completed" if sufficiency.sufficient else "waiting",
            sufficient=sufficiency.sufficient,
            missing_requirement_count=len(sufficiency.missing_requirements),
            requirement_coverage=round(
                sum(bool(value) for value in sufficiency.requirement_coverage.values())
                / max(1, len(sufficiency.requirement_coverage)),
                3,
            ),
            attempt=int(state.get("attempts") or 0),
        )
        return {
            "sufficiency": sufficiency,
            "trace": [*state.get("trace", []), _trace(
                "evidence.sufficiency", "completed" if sufficiency.sufficient else "insufficient",
                sufficiency.reason,
                attempt=state["attempts"],
                sufficient=sufficiency.sufficient,
                missing_requirements=sufficiency.missing_requirements,
                requirement_coverage=sufficiency.requirement_coverage,
            )],
        }

    def _after_sufficiency(self, state: _PolicyRagState) -> Literal["rewrite", "answer"]:
        if state["sufficiency"].sufficient:
            return "answer"
        if state["attempts"] >= self.max_retrieval_attempts:
            return "answer"
        queries = _retry_queries(
            state["request"].resolved_question or state["request"].question,
            state["plan"].requirements,
            state["sufficiency"].missing_requirements,
        )
        return "rewrite" if queries else "answer"

    def _rewrite_node(self, state: _PolicyRagState) -> dict[str, Any]:
        queries = _retry_queries(
            state["request"].resolved_question or state["request"].question,
            state["plan"].requirements,
            state["sufficiency"].missing_requirements,
        )
        emit_tool_progress(
            "tool.policy.rewrite.completed",
            f"已针对缺失证据改写 {len(queries)} 组查询，准备再次检索",
            status="completed",
            retry_query_count=len(queries),
            next_attempt=int(state.get("attempts") or 0) + 1,
            max_attempts=self.max_retrieval_attempts,
        )
        return {
            "retry_queries": queries,
            "trace": [*state.get("trace", []), _trace(
                "evidence.retry", "completed",
                "证据覆盖不足，已针对缺失维度改写查询并进入下一轮检索",
                retry_queries=queries,
                bounded_attempt=f"{state['attempts'] + 1}/{self.max_retrieval_attempts}",
            )],
        }

    def _answer_node(self, state: _PolicyRagState) -> dict[str, Any]:
        request = state["request"]
        evidence = _number_evidence(state["bundle"].hits, request.attachments)
        emit_tool_progress(
            "tool.policy.answer.started",
            f"正在基于 {len(evidence)} 条受控证据生成政策回答",
            evidence_count=len(evidence),
        )
        if not evidence:
            answer = (
                "当前受控政策知识库中没有检索到足以回答该问题的有效依据。"
                "我不会根据常识补写政策结论；请补充相关政策文件，或请管理员处理知识库中解析失败的资料后再试。"
            )
            emit_tool_progress(
                "tool.policy.answer.completed",
                "没有足够的受控证据，已返回不猜测的降级回答",
                status="waiting",
                evidence_count=0,
            )
            return {
                "evidence": [], "raw_answer": answer, "answer": answer,
                "review": PolicyClaimReview("no_evidence", answer, 0, 0, 0),
                "trace": [*state.get("trace", []), _trace(
                    "answer.grounded_fallback", "completed", "无可用证据，返回受控降级回答",
                )],
            }
        reply = self.gateway.complete(
            _answer_messages(
                request, state["plan"], state["sufficiency"], evidence,
                state.get("conflict_review") or PolicyConflictReview("none", False),
                state.get("temporal_review") or PolicyTemporalReview("not_requested", False),
            ),
            purpose="policy_grounded_answer", model=self.answer_model, max_tokens=2_800,
        )
        stored = self._store_evidence(request, evidence)
        emit_tool_progress(
            "tool.policy.answer.completed",
            f"政策回答已生成并锁定 {len(evidence)} 条可回链证据",
            status="completed",
            evidence_count=len(evidence),
            model=reply.model,
            latency_ms=reply.latency_ms,
            payload_offloaded=bool(stored.get("evidence_ref")),
        )
        return {
            **stored,
            "raw_answer": reply.content.strip(),
            "trace": [
                *state.get("trace", []),
                *([_trace(
                    "graph.payload.offloaded", "completed",
                    "政策证据正文已外置，Evidence Graph 仅保留引用",
                    kind="policy.evidence",
                    byte_size=int(dict(stored.get("evidence_ref") or {}).get("byte_size") or 0),
                )] if stored.get("evidence_ref") else []),
                _trace(
                    "evidence.sources", "completed", f"锁定 {len(evidence)} 条可回链政策证据",
                    sources=[{
                        "reference_id": item.reference_id,
                        "source_id": item.source_id,
                        "title": item.title,
                        "citation": item.citation,
                        "source_version": item.source_version,
                        "publication_date": item.publication_date,
                        "effective_date": item.effective_date,
                        "expiry_date": item.expiry_date,
                        "policy_status": item.policy_status,
                        "authority_level": item.authority_level,
                        "version_id": item.version_id,
                        "publication_id": item.publication_id,
                        "score": round(item.score, 4),
                    } for item in evidence],
                ),
                _trace(
                    "model.answer", "completed", f"使用 {reply.model} 基于受控政策证据生成回答",
                    model=reply.model, latency_ms=reply.latency_ms,
                ),
            ],
        }

    @staticmethod
    def _temporal_node(state: _PolicyRagState) -> dict[str, Any]:
        evidence = _number_evidence(state["bundle"].hits, state["request"].attachments)
        review = _assess_policy_temporal_status(state["request"], evidence)
        emit_tool_progress(
            "tool.policy.temporal.completed",
            review.reason or "政策有效期检查完成",
            status="waiting" if review.requires_human_review else "completed",
            temporal_status=review.status,
            requires_human_review=review.requires_human_review,
            reference_count=len(review.references),
        )
        return {
            "temporal_review": review,
            "trace": [*state.get("trace", []), _trace(
                "policy.effective_date_gate",
                "review_required" if review.requires_human_review else "completed",
                review.reason or "本轮问题不要求判断政策现行效力",
                temporal_status=review.status,
                requires_human_review=review.requires_human_review,
                references=list(review.references),
            )],
        }

    @staticmethod
    def _conflict_node(state: _PolicyRagState) -> dict[str, Any]:
        evidence = _number_evidence(state["bundle"].hits, state["request"].attachments)
        review = _detect_policy_conflicts(
            evidence,
            focus_text=state["request"].resolved_question or state["request"].question,
        )
        emit_tool_progress(
            "tool.policy.conflict.completed",
            (
                f"发现 {review.conflict_count} 组需并列披露的政策差异"
                if review.requires_human_review else "未发现达到阈值的政策冲突"
            ),
            status="waiting" if review.requires_human_review else "completed",
            conflict_count=review.conflict_count,
            requires_human_review=review.requires_human_review,
        )
        return {
            "conflict_review": review,
            "trace": [*state.get("trace", []), _trace(
                "policy.conflict_gate",
                "review_required" if review.requires_human_review else "completed",
                (
                    f"发现 {review.conflict_count} 组政策证据冲突，回答必须并列披露并进入人工核验"
                    if review.requires_human_review else "未发现达到确定性阈值的政策证据冲突"
                ),
                conflict_count=review.conflict_count,
                requires_human_review=review.requires_human_review,
                conflicts=[asdict(item) for item in review.items],
            )],
        }

    def _align_node(self, state: _PolicyRagState) -> dict[str, Any]:
        if state.get("review") is not None:
            return {}
        review = _align_claims(state["raw_answer"], self._evidence(state))
        conflict_review = state.get("conflict_review") or PolicyConflictReview("none", False)
        temporal_review = state.get("temporal_review") or PolicyTemporalReview("not_requested", False)
        answer = review.answer
        if temporal_review.requires_human_review:
            answer = _temporal_guarded_answer(
                self._evidence(state), temporal_review, grounded_answer=review.answer,
            )
        if conflict_review.requires_human_review:
            answer = _conflict_guarded_answer(
                self._evidence(state), conflict_review, grounded_answer=review.answer,
            )
        emit_tool_progress(
            "tool.policy.alignment.completed",
            "已核对政策结论、证据编号和缺少依据的表述",
            status="completed" if review.status == "pass" else "waiting",
            review_status=review.status,
            direct_claim_count=review.direct_count,
            partial_claim_count=review.partial_count,
            unsupported_claim_count=review.missing_count,
            invalid_citation_count=len(review.invalid_citations),
        )
        return {
            "review": review,
            "answer": answer,
            "trace": [*state.get("trace", []), _trace(
                "claim_evidence.align", "completed" if review.status == "pass" else "revised",
                "已检查回答中的政策结论和证据编号，并对缺少依据的表述进行降级",
                review_status=review.status,
                direct_claims=review.direct_count,
                partial_claims=review.partial_count,
                unsupported_claims=review.missing_count,
                invalid_citations=review.invalid_citations,
            )],
        }

    def _store_evidence(
        self, request: PolicyRagRequest, evidence: list[_EvidenceItem]
    ) -> dict[str, Any]:
        if self.payload_store is None or request.payload_scope is None:
            return {"evidence": evidence}
        reference = self.payload_store.put_json(
            request.payload_scope,
            [asdict(item) for item in evidence],
            kind="policy.evidence",
        )
        return {"evidence": [], "evidence_ref": reference.model_dump(mode="json")}

    def _evidence(self, state: _PolicyRagState) -> list[_EvidenceItem]:
        evidence = list(state.get("evidence") or [])
        request = state["request"]
        reference = dict(state.get("evidence_ref") or {})
        if (
            not evidence and reference and self.payload_store is not None
            and request.payload_scope is not None
        ):
            evidence = [
                _EvidenceItem(**item)
                for item in self.payload_store.get_json(request.payload_scope, reference)
            ]
        return evidence

    @staticmethod
    def _finalize_node(state: _PolicyRagState) -> dict[str, Any]:
        emit_tool_progress(
            "tool.policy.finalize.completed",
            "政策证据链、有效期、冲突与引用校验已完成",
            status="completed",
            retrieval_attempts=int(state.get("attempts") or 0),
        )
        return {"trace": [*state.get("trace", []), _trace(
            "evidence.graph.completed", "completed", "Policy Evidence Graph 已完成",
            graph_path=[
                "evidence_plan", "hybrid_retrieve", "sufficiency_gate",
                *(["rewrite_missing_evidence", "hybrid_retrieve", "sufficiency_gate"] if state["attempts"] > 1 else []),
                "effective_date_gate", "conflict_gate", "grounded_answer", "claim_alignment", "finalize",
            ],
        )]}


_REQUIREMENT_CATALOG = (
    EvidenceRequirement("applicability", "适用对象与范围", ("适用", "对象", "人员范围", "哪些人", "范围")),
    EvidenceRequirement("eligibility", "申请或享受条件", ("条件", "资格", "要求", "应当具备", "符合")),
    EvidenceRequirement("materials", "所需材料", ("材料", "提交", "申请表", "证明", "附件", "清单")),
    EvidenceRequirement("process", "办理流程", ("流程", "办理", "程序", "步骤", "受理", "申报")),
    EvidenceRequirement("deadline", "时间与期限", ("时间", "期限", "截止", "工作日", "日内", "年度")),
    EvidenceRequirement("standard", "金额、比例或待遇标准", ("金额", "比例", "标准", "基数", "待遇", "补贴", "缴存")),
    EvidenceRequirement("approval", "审批权限与复核", ("审批", "审核", "复核", "批准", "权限", "备案")),
    EvidenceRequirement("exception", "例外与特殊情形", ("例外", "特殊", "除外", "不适用", "另有规定")),
)


def _plan_evidence(request: PolicyRagRequest) -> PolicyEvidencePlan:
    resolved = (request.resolved_question or request.question).strip()
    normalized = resolved.casefold()
    requirements = [
        item for item in _REQUIREMENT_CATALOG
        if any(term.casefold() in normalized for term in item.terms)
    ]
    if not requirements:
        requirements = [
            EvidenceRequirement(
                "policy_basis",
                "直接政策依据",
                tuple(_query_key_terms(resolved)[:8]),
            )
        ]
    queries = list(dict.fromkeys(
        value.strip()
        for value in (resolved, request.question, *request.query_variants)
        if value.strip()
    ))[:6]
    year_match = re.search(r"(?<!\d)((?:19|20)\d{2})\s*年", resolved)
    asks_current = any(marker in resolved for marker in ("现行", "当前", "目前", "最新", "现在"))
    as_of_date = f"{year_match.group(1)}-12-31" if year_match and not asks_current else None
    current_only = asks_current or as_of_date is None
    complexity = len(requirements) + len(request.priority_constraints)
    top_k = max(6, min(12, 5 + complexity * 2))
    candidate_k = max(40, min(100, top_k * 8))
    return PolicyEvidencePlan(
        queries=queries,
        requirements=requirements,
        top_k=top_k,
        candidate_k=candidate_k,
        as_of_date=as_of_date,
        current_only=current_only,
        reason="根据问题中的政策维度、时间口径和显式约束制定检索计划",
    )


def _assess_sufficiency(
    plan: PolicyEvidencePlan,
    bundle: RetrievalBundle,
    evidence: Sequence[_EvidenceItem],
    attachments: Sequence[PolicyAttachmentEvidence],
) -> PolicySufficiency:
    corpus = "\n".join(item.text for item in evidence)
    if attachments:
        corpus += "\n" + "\n".join(item.text for item in attachments)
    coverage: dict[str, bool] = {}
    for requirement in plan.requirements:
        if requirement.code == "policy_basis":
            coverage[requirement.label] = bundle.coverage >= 0.18
        else:
            coverage[requirement.label] = any(term in corpus for term in requirement.terms)
    missing = [label for label, covered in coverage.items() if not covered]
    has_evidence = bool(evidence or attachments)
    covered_count = len(coverage) - len(missing)
    coverage_ratio = covered_count / max(1, len(coverage))
    sufficient = has_evidence and (
        not missing
        or (coverage_ratio >= 0.67 and bundle.coverage >= 0.22)
    )
    if not has_evidence:
        reason = "没有召回任何可用政策证据"
    elif sufficient:
        reason = "检索证据已覆盖本问题所需的主要政策维度"
    else:
        reason = "检索结果存在，但尚未覆盖全部关键政策维度"
    return PolicySufficiency(
        sufficient=sufficient,
        missing_requirements=missing,
        requirement_coverage=coverage,
        lexical_coverage=bundle.coverage,
        reason=reason,
    )


def _retry_queries(
    resolved_question: str,
    requirements: Sequence[EvidenceRequirement],
    missing_labels: Sequence[str],
) -> list[str]:
    missing = set(missing_labels)
    return [
        f"{resolved_question} {item.label} {' '.join(item.terms[:4])}"[:240]
        for item in requirements
        if item.label in missing
    ][:3]


def _evidence_from_hits(hits: Sequence[SearchHit]) -> list[_EvidenceItem]:
    return [
        _EvidenceItem(
            reference_id="",
            chunk_id=hit.chunk_id,
            source_id=hit.source_id,
            title=hit.title,
            citation=hit.citation,
            text=hit.text,
            score=hit.score,
            policy_status=hit.policy_status,
            source_version=hit.source_version,
            publication_date=hit.publication_date,
            effective_date=hit.effective_date,
            expiry_date=hit.expiry_date,
            authority_level=hit.authority_level,
            version_id=hit.version_id,
            publication_id=hit.publication_id,
        )
        for hit in hits
    ]


def _number_evidence(
    hits: Sequence[SearchHit],
    attachments: Sequence[PolicyAttachmentEvidence],
) -> list[_EvidenceItem]:
    items = _evidence_from_hits(hits)
    numbered = [
        _EvidenceItem(**{**item.__dict__, "reference_id": f"E{index}"})
        for index, item in enumerate(items, start=1)
    ]
    for index, attachment in enumerate(attachments, start=1):
        if not attachment.text.strip():
            continue
        numbered.append(_EvidenceItem(
            reference_id=f"A{index}",
            chunk_id=f"attachment-{index}",
            source_id=f"attachment-{index}",
            title=attachment.title,
            citation=f"用户上传附件《{attachment.title}》",
            text=attachment.text[:16_000],
            score=1.0,
            policy_status="user_provided",
        ))
    return numbered


def _answer_messages(
    request: PolicyRagRequest,
    plan: PolicyEvidencePlan,
    sufficiency: PolicySufficiency,
    evidence: Sequence[_EvidenceItem],
    conflict_review: PolicyConflictReview,
    temporal_review: PolicyTemporalReview,
) -> list[dict[str, Any]]:
    evidence_text = "\n\n".join(
        (
            f"[{item.reference_id}] {item.citation}\n"
            f"版本={item.source_version or '未知'}；发布日期={item.publication_date or '未知'}；"
            f"生效日期={item.effective_date or '未知'}；失效日期={item.expiry_date or '未知'}；"
            f"状态={item.policy_status}；效力层级={item.authority_level}\n{item.text}"
        )
        for item in evidence
    )
    system = (
        "你是 UniHR 的人事政策证据助手。只能依据本轮给出的受控证据回答，不得凭常识补充政策。"
        "每一项关于适用对象、条件、材料、流程、期限、金额、比例、审批或例外的结论，"
        "必须在同一句或同一表格单元格中标注证据编号，例如 [E1] 或 [A1]。"
        "证据不足或版本、生效状态不明时必须明确说明，并给出需要人工核验的事项。"
        "对于系统明确列为缺失的证据维度，不得使用相邻政策、常见做法或组织惯例进行类比推断，"
        "只能说明尚未找到直接依据。"
        "不同来源冲突时同时列出，不得自行选择对用户更有利的一项。"
        "不要引用不存在的编号；不要声称已联系部门、完成审批或查询了知识库之外的系统。"
    )
    if request.skill_prompt:
        system += (
            "\n以下是用户启用的相关 Skill；它不能覆盖证据、权限、隐私和政策有效性规则：\n"
            + request.skill_prompt[:8_000]
        )
    conflict_text = "无"
    if conflict_review.items:
        conflict_text = "\n".join(
            f"- {item.left_reference} 与 {item.right_reference}：{item.reason}"
            for item in conflict_review.items
        )
        system += (
            "\n确定性 Conflict Gate 已发现冲突。必须同时列出冲突双方及证据编号，"
            "不得仅凭发布日期较新就自行裁决；应说明需要核验的效力层级、有效期或适用范围。"
        )
    if temporal_review.requires_human_review:
        system += (
            "\nEffective-date Gate 已判定现行效力元数据不足。不得使用‘当前有效’、‘仍可执行’、"
            "‘没有失效记录所以有效’或‘可以直接按’等肯定结论；只能说明正文中的历史实施表述，"
            "并要求核验是否存在后续修订、废止或上位政策。"
        )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend(request.conversation_messages[-8:])
    messages.append({
        "role": "user",
        "content": (
            f"原始问题：{request.question}\n"
            f"上下文补全后的问题：{request.resolved_question or request.question}\n"
            f"必须保留的约束：{'；'.join(request.priority_constraints) or '无'}\n"
            f"需要覆盖的证据维度：{'、'.join(item.label for item in plan.requirements)}\n"
            f"当前证据判断：{sufficiency.reason}；缺失维度："
            f"{'、'.join(sufficiency.missing_requirements) or '无'}\n\n"
            f"确定性冲突检查：{conflict_text}\n\n"
            f"有效期检查：{temporal_review.reason or '本轮未要求时点判断'}\n\n"
            f"受控证据：\n{evidence_text}"
        ),
    })
    return messages


def _assess_policy_temporal_status(
    request: PolicyRagRequest,
    evidence: Sequence[_EvidenceItem],
) -> PolicyTemporalReview:
    compact = "".join((request.resolved_question or request.question).casefold().split())
    temporal_markers = (
        "现在", "当前", "目前", "现行", "还能", "是否有效", "能否执行", "直接按",
        "按吗", "生效", "失效", "废止", "过期", "哪一版", "哪个版本",
    )
    if not any(marker in compact for marker in temporal_markers):
        return PolicyTemporalReview("not_requested", False)
    governed = [item for item in evidence if item.policy_status != "user_provided"]
    if not governed:
        return PolicyTemporalReview(
            "unverified", True, "没有可用于核实现行效力的受控政策版本元数据"
        )
    today = datetime.now(timezone.utc).date().isoformat()
    verified = [
        item for item in governed
        if item.policy_status in {"active", "trial"}
        and (not item.expiry_date or item.expiry_date >= today)
    ]
    if verified:
        return PolicyTemporalReview(
            "verified", False, "至少一份命中政策已登记为现行或试行且未超过失效日期",
            tuple(item.reference_id for item in verified),
        )
    unknown = [
        item for item in governed
        if item.policy_status in {"", "unknown"}
        or (not item.effective_date and not item.expiry_date)
    ]
    if unknown:
        return PolicyTemporalReview(
            "unverified", True,
            "命中政策的状态或生效/失效日期尚未登记完整，不能据此断言当前仍有效",
            tuple(item.reference_id for item in unknown),
        )
    return PolicyTemporalReview(
        "not_current", True,
        "现有版本未形成可证明当前有效的时间链，需核验后续修订、废止和上位政策",
        tuple(item.reference_id for item in governed),
    )


def _temporal_guarded_answer(
    evidence: Sequence[_EvidenceItem],
    review: PolicyTemporalReview,
    *,
    grounded_answer: str = "",
) -> str:
    selected = [item for item in evidence if item.reference_id in set(review.references)]
    if not selected:
        selected = list(evidence[:3])
    references = "".join(f"[{item.reference_id}]" for item in selected[:3]) or ""
    source_lines = []
    seen: set[tuple[str, str | None]] = set()
    for item in selected:
        key = (item.title, item.source_version)
        if key in seen:
            continue
        seen.add(key)
        source_lines.append(
            f"- {item.title}（版本：{item.source_version or '待确认'}；"
            f"状态：{item.policy_status or 'unknown'}；生效：{item.effective_date or '待确认'}；"
            f"失效：{item.expiry_date or '待确认'}）[{item.reference_id}]"
        )
        if len(source_lines) >= 3:
            break
    grounded_summary = _safe_temporal_grounded_summary(grounded_answer, evidence)
    scheme_section = (
        "\n\n**文件中当前可以确认的内容**（不代表今天仍然有效）\n"
        + grounded_summary + "\n\n"
        if grounded_summary else ""
    )
    return (
        "**结论：目前不能确认这份细则可以直接作为现行执行依据。**\n\n"
        f"知识库已经检索到相关正式文件和条款 {references}，但{review.reason}。"
        "正文中出现“即日起实施”或“自公布之日起实施”，只能证明文件发布时的实施安排；"
        "没有检出失效记录不等于已经证明今天仍然有效。\n\n"
        f"{scheme_section}"
        "**当前命中版本**\n" + "\n".join(source_lines) + "\n\n"
        "**建议下一步**\n"
        "1. 在正式发文或政策管理系统核对该文是否被后续文件修订、替代或废止。\n"
        "2. 核对上位政策和适用组织范围。\n"
        "3. 核验完成前，可用这些条款准备材料和做内部核对，但不要据此作出最终人事决定。"
    )


def _safe_temporal_grounded_summary(
    answer: str,
    evidence: Sequence[_EvidenceItem],
) -> str:
    valid_references = {item.reference_id for item in evidence}
    unsafe_validity = re.compile(
        r"(?:当前|现行|今天|目前).{0,18}(?:有效|生效|可执行|适用|为准)"
        r"|(?:可以|可|能够|能)\s*(?:直接)?\s*(?:执行|按照|按|适用)"
        r"|(?:未|没有).{0,12}(?:废止|失效).{0,12}(?:所以|因此|说明).{0,12}(?:有效|可执行)",
    )
    kept: list[str] = []
    for sentence in re.split(r"(?<=[。！？；;])\s*|\n+", answer.strip()):
        clean = sentence.strip()
        if not clean or unsafe_validity.search(clean):
            continue
        references = set(re.findall(r"\[([EA]\d+)\]", clean))
        if not references or not references.issubset(valid_references):
            continue
        kept.append(clean)
    return "\n".join(f"- {item}" for item in kept[:8])


def _detect_policy_conflicts(
    evidence: Sequence[_EvidenceItem],
    *,
    focus_text: str = "",
) -> PolicyConflictReview:
    items: list[PolicyConflictItem] = []
    focus_terms = _conflict_anchor_terms(focus_text)
    for left_index, left in enumerate(evidence):
        if left.policy_status == "user_provided":
            continue
        for right in evidence[left_index + 1:]:
            if right.policy_status == "user_provided" or left.source_id == right.source_id:
                continue
            shared_ratio = _shared_policy_topic_ratio(left.text, right.text)
            same_family = _policy_family(left.title) == _policy_family(right.title)
            if shared_ratio < 0.12 and not same_family:
                continue
            hard_value_conflict = _conflicting_policy_hard_values(
                left.text,
                right.text,
                focus_terms=focus_terms,
            )
            if hard_value_conflict:
                left_values = tuple(dict.fromkeys(pair[0] for pair in hard_value_conflict))
                right_values = tuple(dict.fromkeys(pair[1] for pair in hard_value_conflict))
                items.append(PolicyConflictItem(
                    left.reference_id, right.reference_id, "hard_value",
                    "同一政策事项出现不同金额、比例、期限或人数",
                    left_values, right_values,
                    hard_value_conflict,
                ))
                continue
            if _has_conflicting_policy_obligation(
                left.text,
                right.text,
                focus_terms=focus_terms,
            ):
                items.append(PolicyConflictItem(
                    left.reference_id, right.reference_id, "obligation",
                    "同一政策事项出现相反的允许、义务或禁止表述",
                ))
                continue
            if (
                same_family and left.version_id and right.version_id
                and left.version_id != right.version_id
                and shared_ratio >= 0.12
                and _normalized_policy_excerpt(left.text) != _normalized_policy_excerpt(right.text)
                and not _metadata_establishes_precedence(left, right)
            ):
                items.append(PolicyConflictItem(
                    left.reference_id, right.reference_id, "version_ambiguity",
                    "同一政策家族存在多个版本，但元数据不足以确定适用优先级",
                ))
    unique = list({
        (item.left_reference, item.right_reference, item.kind): item for item in items
    }.values())
    return PolicyConflictReview(
        status="detected" if unique else "none",
        requires_human_review=bool(unique),
        items=unique[:8],
    )


def _conflict_notice(review: PolicyConflictReview) -> str:
    lines = [
        "⚠️ **政策冲突提示**：本轮受控证据存在不能自动裁决的差异，"
        "以下内容必须由人事或政策管理人员核验有效期、效力层级和适用范围：",
    ]
    lines.extend(
        f"- [{item.left_reference}] 与 [{item.right_reference}]：{item.reason}。"
        for item in review.items
    )
    return "\n".join(lines)


def _conflict_guarded_answer(
    evidence: Sequence[_EvidenceItem],
    review: PolicyConflictReview,
    *,
    grounded_answer: str = "",
) -> str:
    """Return a deterministic safe answer when version precedence is unresolved.

    A model may correctly describe both sides yet still append a recommendation
    such as "prefer the newer document".  That is an execution decision, not a
    language-generation task, unless governance metadata has established it.
    """

    by_reference = {item.reference_id: item for item in evidence}
    lines = [
        "**可以先把现有方案讲清楚；最终执行口径仍需核验。**",
        "我会先按现有证据说明各版本写了什么。由于同一受控政策字段存在不同表述，"
        "在核验替代关系、效力层级和生效状态前，暂不替你裁定哪一版应作为正式人事执行依据。",
        "",
    ]
    grounded_summary = _safe_grounded_conflict_summary(grounded_answer, evidence)
    if grounded_summary:
        lines.extend([
            "**基于现有证据的方案梳理**（AI 归纳，不代表已确认生效）",
            grounded_summary,
            "",
        ])
    lines.append("**当前可确认的方案内容**（不代表已确认生效）")
    lines.extend(_format_current_scheme_evidence(evidence, review))
    lines.extend([
        "",
        "**已检出的直接差异**",
    ])
    hard_value_lines = _format_hard_value_conflict_groups(review.items)
    lines.extend(hard_value_lines)
    for item in review.items:
        if item.kind == "hard_value":
            continue
        left = by_reference.get(item.left_reference)
        right = by_reference.get(item.right_reference)
        left_label = _conflict_source_label(left, item.left_reference)
        right_label = _conflict_source_label(right, item.right_reference)
        lines.append(f"- {left_label} 与 {right_label}：{item.reason}。")
    lines.extend([
        "",
        "**需要人工核验**",
        "1. 是否有明确的修订、替代或废止文件；",
        "2. 各版本的正式发文层级、适用组织和生效/失效日期；",
        "3. 核验完成后，再由具备权限的人员确认最终执行版本。",
        "",
        "系统已保留全部命中证据和版本信息，供人工复核；本回答不推荐优先采用其中任何一版。",
    ])
    return "\n".join(lines)


def _safe_grounded_conflict_summary(
    answer: str,
    evidence: Sequence[_EvidenceItem],
) -> str:
    """Keep the model's cited explanation; remove only unverified precedence decisions."""

    valid_references = {item.reference_id for item in evidence}
    unsafe_decision = re.compile(
        r"(?:建议|应当?|应该|可以|可)?\s*(?:优先|直接)\s*(?:采用|执行|适用|按照|按|以)"
        r"|(?:建议|应当?|应该|可以|可)\s*(?:按照|按|以).{0,36}(?:执行|为准)"
        r"|以.{0,48}为准"
        r"|(?:依据|根据).{0,36}(?:最新|最终|完整)(?:版本|文件|表格|材料)?"
        r"|(?:应指|应为|可见应|可以推断|由此推断)"
        r"|(?:现行|最终|正式)\s*(?:版本|口径).{0,12}(?:是|为)",
    )
    kept: list[str] = []
    for sentence in re.split(r"(?<=[。！？；;])\s*|\n+", answer.strip()):
        clean = sentence.strip()
        if not clean or unsafe_decision.search(clean):
            continue
        references = set(re.findall(r"\[([EA]\d+)\]", clean))
        if not references or not references.issubset(valid_references):
            continue
        kept.append(clean)
        if sum(len(item) for item in kept) >= 1_200:
            break
    return "\n".join(f"- {item}" for item in kept)


def _format_current_scheme_evidence(
    evidence: Sequence[_EvidenceItem],
    review: PolicyConflictReview,
) -> list[str]:
    """Preserve useful policy facts while withholding only the precedence decision."""

    conflict_references = {
        reference
        for item in review.items
        for reference in (item.left_reference, item.right_reference)
    }
    seen: set[str] = set()
    lines: list[str] = []
    for item in evidence:
        if item.reference_id not in conflict_references:
            continue
        excerpt = re.sub(r"\s+", " ", item.text).strip()
        excerpt = re.sub(rf"^《?{re.escape(item.title)}》?", "", excerpt).strip(" ：:·-")
        normalized = _normalized_policy_excerpt(excerpt)
        if len(normalized) < 6 or normalized in seen:
            continue
        seen.add(normalized)
        if len(excerpt) > 220:
            excerpt = excerpt[:220].rstrip("，。；; ") + "……"
        version = item.source_version or item.publication_date or "版本待确认"
        lines.append(f"- [{item.reference_id}]（{version}）{excerpt}")
        if len(lines) >= 6:
            break
    return lines or ["- 当前命中仅能确认存在多个待核验版本；具体方案正文尚未形成足够直接证据。"]


def _normalized_policy_excerpt(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _conflict_source_label(item: _EvidenceItem | None, fallback: str) -> str:
    if item is None:
        return f"[{fallback}]"
    version = item.source_version or item.publication_date or "版本待确认"
    return f"[{item.reference_id}]《{item.title}》（{version}）"


def _format_hard_value_conflict_groups(items: Sequence[PolicyConflictItem]) -> list[str]:
    """Collapse pairwise conflicts without inventing a value-to-source mapping.

    One retrieved document can legitimately contain several coefficients for
    neighbouring posts.  A high-level conflict banner must never imply that a
    particular source supports only one of those values.  The detailed evidence
    cards remain the source-level audit surface; the banner only reports the
    disputed field, distinct values and involved evidence IDs.
    """

    grouped: dict[str, dict[str, list[str]]] = {}
    for item in items:
        if item.kind != "hard_value":
            continue
        pairs = item.value_pairs or tuple(zip(item.left_values, item.right_values))
        for left_value, right_value in pairs:
            for reference, value in (
                (item.left_reference, left_value),
                (item.right_reference, right_value),
            ):
                unit = _hard_value_unit(value)
                field = grouped.setdefault(unit, {"values": [], "references": []})
                if value not in field["values"]:
                    field["values"].append(value)
                if reference not in field["references"]:
                    field["references"].append(reference)
    lines: list[str] = []
    for unit, field in grouped.items():
        values = field["values"]
        references = field["references"]
        if len(values) < 2:
            continue
        field_label = {"系数": "岗位系数", "%": "比例", "元": "金额", "万元": "金额"}.get(unit, unit or "数值")
        value_text = "、".join(values)
        reference_text = "、".join(f"[{reference}]" for reference in references)
        lines.append(
            f"- {field_label}：检出 {value_text} 的不同表述（涉及 {reference_text}）；"
            "请在下方证据面板逐项核验原文语境。"
        )
    return lines


def _hard_value_unit(value: str) -> str:
    match = re.search(r"(%|％|万元|元|个工作日|个月|周岁|系数|天|人|年)$", value)
    return _normalize_policy_unit(match.group(1)) if match else ""


def _shared_policy_topic_ratio(left: str, right: str) -> float:
    left_terms = set(_query_key_terms(left))
    right_terms = set(_query_key_terms(right))
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / min(len(left_terms), len(right_terms))


def _policy_hard_values(text: str) -> tuple[str, ...]:
    values = re.findall(
        r"\d+(?:\.\d+)?\s*(?:%|％|元|万元|天|个工作日|人|个月|年|周岁|系数)", text,
    )
    return tuple(dict.fromkeys(re.sub(r"\s+", "", item) for item in values))


def _conflicting_policy_hard_values(
    left_text: str,
    right_text: str,
    *,
    focus_terms: set[str] | None = None,
) -> tuple[tuple[str, str], ...] | None:
    """Return only hard values that disagree on the same concrete field.

    A policy chunk often contains unrelated numbers such as an age limit, number
    of recommendation letters, a deadline and a penalty.  Comparing the bags of
    numbers for the whole chunks creates false conflicts.  We therefore require
    the units to match *and* the local semantic anchors around the values to
    overlap before treating different values as contradictory.
    """
    left_claims = _policy_value_claims(left_text)
    right_claims = _policy_value_claims(right_text)
    conflicts: list[tuple[str, str]] = []
    for left_value, left_unit, left_anchors in left_claims:
        for right_value, right_unit, right_anchors in right_claims:
            if left_unit != right_unit or left_value == right_value:
                continue
            shared = left_anchors & right_anchors
            denominator = max(1, min(len(left_anchors), len(right_anchors)))
            if len(shared) < 2 or len(shared) / denominator < 0.3:
                continue
            if not _conflict_claim_matches_focus(left_anchors, right_anchors, focus_terms):
                continue
            conflicts.append((f"{left_value}{left_unit}", f"{right_value}{right_unit}"))
    if not conflicts:
        return None
    return tuple(dict.fromkeys(conflicts))


def _conflict_claim_matches_focus(
    left_anchors: set[str],
    right_anchors: set[str],
    focus_terms: set[str] | None,
) -> bool:
    """Require conflicts to concern the field the user is actually asking about."""

    if not focus_terms or len(focus_terms) < 3:
        return True
    left_matches = left_anchors & focus_terms
    right_matches = right_anchors & focus_terms
    return len(left_matches) >= 2 and len(right_matches) >= 2


def _policy_value_claims(text: str) -> list[tuple[str, str, set[str]]]:
    claims: list[tuple[str, str, set[str]]] = []
    pattern = re.compile(
        r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%|％|元|万元|天|个工作日|人|个月|年|周岁|系数)"
    )
    for match in pattern.finditer(text):
        start = max(0, match.start() - 28)
        end = min(len(text), match.end() + 28)
        context = text[start:end]
        claims.append((
            match.group("value"),
            _normalize_policy_unit(match.group("unit")),
            _conflict_anchor_terms(context),
        ))
    return claims


def _normalize_policy_unit(unit: str) -> str:
    return {"％": "%"}.get(unit, unit)


def _conflict_anchor_terms(text: str) -> set[str]:
    generic = {
        "博士", "士后", "工作", "管理", "细则", "办法", "规定", "政策", "实施",
        "通知", "附件", "国际", "学院", "研究", "究院", "浙江", "大学", "本细",
        "本办", "应当", "必须", "不得", "禁止", "严禁", "可以", "允许",
    }
    return {term for term in _query_key_terms(text) if term not in generic}


def _has_conflicting_policy_obligation(
    left_text: str,
    right_text: str,
    *,
    focus_terms: set[str] | None = None,
) -> bool:
    left_claims = _policy_obligation_claims(left_text)
    right_claims = _policy_obligation_claims(right_text)
    for left_polarity, left_anchors in left_claims:
        for right_polarity, right_anchors in right_claims:
            if left_polarity == right_polarity:
                continue
            shared = left_anchors & right_anchors
            denominator = max(1, min(len(left_anchors), len(right_anchors)))
            if (
                len(shared) >= 2 and len(shared) / denominator >= 0.3
                and _conflict_claim_matches_focus(left_anchors, right_anchors, focus_terms)
            ):
                return True
    return False


def _policy_obligation_claims(text: str) -> list[tuple[str, set[str]]]:
    claims: list[tuple[str, set[str]]] = []
    for sentence in re.split(r"[。；;\n]+", text):
        polarity = _policy_polarity(sentence)
        if not polarity:
            continue
        anchors = _conflict_anchor_terms(sentence)
        if anchors:
            claims.append((polarity, anchors))
    return claims


def _policy_polarity(text: str) -> str:
    if any(marker in text for marker in ("不得", "禁止", "严禁", "不予", "不能")):
        return "forbidden"
    if any(marker in text for marker in ("可以", "允许", "可申请", "可享受", "应当", "必须")):
        return "allowed_or_required"
    return ""


def _policy_family(title: str) -> str:
    normalized = re.sub(r"(?:19|20)\d{2}", "", title.casefold())
    normalized = re.sub(r"[〔〕【】\[\]（）(){}《》〈〉\s·._\-—]+", "", normalized)
    normalized = re.sub(r"(?:试行|暂行|修订版|最新版|旧版|新版|通知|附件|pdf|docx?)", "", normalized)
    return normalized


def _metadata_establishes_precedence(left: _EvidenceItem, right: _EvidenceItem) -> bool:
    statuses = {left.policy_status, right.policy_status}
    if "repealed" in statuses and statuses & {"active", "trial"}:
        return True
    if left.expiry_date and right.effective_date and left.expiry_date < right.effective_date:
        return True
    if right.expiry_date and left.effective_date and right.expiry_date < left.effective_date:
        return True
    return False


def _align_claims(answer: str, evidence: Sequence[_EvidenceItem]) -> PolicyClaimReview:
    normalized = re.sub(r"[【\[]\s*证据\s*([0-9]+)\s*[】\]]", r"[E\1]", answer)
    valid = {item.reference_id: item for item in evidence}
    direct = 0
    partial = 0
    missing = 0
    invalid: set[str] = set()
    revised_lines: list[str] = []
    for line in normalized.splitlines():
        clean = line.strip()
        if not _is_policy_claim(clean):
            revised_lines.append(line)
            continue
        references = re.findall(r"\[([EA]\d+)\]", clean)
        invalid.update(reference for reference in references if reference not in valid)
        cited = [valid[reference] for reference in references if reference in valid]
        if not cited:
            missing += 1
            revised_lines.append(
                line + " 【当前证据未直接支持该表述，需人工核验】"
            )
            continue
        overlap = max(_claim_overlap(clean, item.text) for item in cited)
        if _is_inference_claim(clean):
            partial += 1
            revised_lines.append(line + " 【该表述包含间接推断，需人工核验】")
        elif overlap >= 0.08:
            direct += 1
            revised_lines.append(line)
        else:
            partial += 1
            revised_lines.append(line + " 【引用证据仅部分支持，需人工核验】")
    status = "pass" if not missing and not partial and not invalid else "revised"
    return PolicyClaimReview(
        status=status,
        answer="\n".join(revised_lines).strip(),
        direct_count=direct,
        partial_count=partial,
        missing_count=missing,
        invalid_citations=sorted(invalid),
    )


def _is_policy_claim(value: str) -> bool:
    if len(value) < 10 or value.startswith(("#", "|---", "引用依据", "证据说明")):
        return False
    if value.startswith("|") and not re.search(r"\[[EA]\d+\]", value):
        return False
    if re.fullmatch(r"\*{0,2}\d+[.、].{1,80}\*{0,2}", value):
        return False
    if any(marker in value for marker in (
        "根据现有受控证据", "当前证据中未", "当前证据不足", "尚未找到直接依据",
        "证据缺口说明", "需要人工核验的事项", "当前证据未直接支持",
        "以下是具体的政策依据汇总", "具体依据如下",
    )):
        return False
    markers = (
        "应", "必须", "不得", "可以", "需要", "适用", "条件", "资格", "材料", "提交",
        "办理", "流程", "期限", "截止", "比例", "金额", "标准", "待遇", "审批", "复核",
        "生效", "失效", "规定", "依据", "享受", "缴存", "发放",
    )
    return any(marker in value for marker in markers)


def _is_inference_claim(value: str) -> bool:
    return any(marker in value for marker in (
        "推测", "推断", "可能", "间接关联", "可认为", "可以认为", "鉴于", "类比",
    ))


def _claim_overlap(claim: str, evidence: str) -> float:
    claim_terms = set(_query_key_terms(re.sub(r"\[[EA]\d+\]", "", claim)))
    evidence_terms = set(_query_key_terms(evidence))
    if not claim_terms:
        return 0.0
    return len(claim_terms & evidence_terms) / len(claim_terms)


def _query_key_terms(value: str) -> list[str]:
    normalized = re.sub(r"[^\u3400-\u9fffa-zA-Z0-9]+", "", value.casefold())
    terms = re.findall(r"[a-z0-9]{2,}", normalized)
    chinese = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    terms.extend(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
    stop = {"什么", "哪些", "怎么", "如何", "是否", "这个", "那个", "根据", "当前", "问题"}
    return list(dict.fromkeys(term for term in terms if term not in stop))


def _trace(event: str, status: str, summary: str, **details: Any) -> dict[str, Any]:
    return {
        "event": event,
        "status": status,
        "summary": summary,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        **{key: value for key, value in details.items() if value is not None},
    }
