from __future__ import annotations

from dataclasses import dataclass, field
from math import log2
from typing import Sequence

from .contracts import KnowledgeAccessContext
from .retriever import PolicyKnowledgeRetriever


@dataclass(frozen=True)
class PolicyRetrievalCase:
    case_id: str
    question: str
    expected_sources: tuple[str, ...]
    query_variants: tuple[str, ...] = ()
    expected_no_answer: bool = False
    relevance_by_source: dict[str, float] = field(default_factory=dict)
    forbidden_sources: tuple[str, ...] = ()
    as_of_date: str | None = None
    current_only: bool = False
    expected_conflict: bool = False
    expected_source_ids: tuple[str, ...] = ()
    relevance_by_source_id: dict[str, float] = field(default_factory=dict)
    forbidden_source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyRetrievalCaseResult:
    case_id: str
    question: str
    expected_sources: tuple[str, ...]
    retrieved_sources: tuple[str, ...]
    matched_sources: tuple[str, ...]
    reciprocal_rank: float
    expected_no_answer: bool = False
    ndcg_at_k: float = 0.0
    citation_coverage: float = 1.0
    forbidden_sources: tuple[str, ...] = ()
    retrieved_forbidden_sources: tuple[str, ...] = ()
    expected_conflict: bool = False
    conflict_detected: bool = False
    relevant_evidence_count: int = 0
    cited_relevant_evidence_count: int = 0
    expected_source_ids: tuple[str, ...] = ()
    retrieved_source_ids: tuple[str, ...] = ()
    matched_source_ids: tuple[str, ...] = ()
    forbidden_source_ids: tuple[str, ...] = ()
    retrieved_forbidden_source_ids: tuple[str, ...] = ()

    @property
    def hit(self) -> bool:
        return (
            not self.retrieved_sources
            if self.expected_no_answer
            else bool(self.matched_sources or self.matched_source_ids)
        )


@dataclass(frozen=True)
class PolicyRetrievalEvaluation:
    case_count: int
    top_k: int
    hit_rate: float
    recall_at_k: float
    mean_reciprocal_rank: float
    no_answer_accuracy: float = 0.0
    ndcg_at_k: float = 0.0
    citation_coverage: float = 1.0
    effective_date_misuse_rate: float = 0.0
    conflict_evidence_recall: float = 1.0
    temporal_guard_case_count: int = 0
    conflict_case_count: int = 0
    results: list[PolicyRetrievalCaseResult] = field(default_factory=list)


class PolicyRetrievalEvaluator:
    """Measure retrieval behavior through the same retriever interface used in production."""

    def __init__(self, retriever: PolicyKnowledgeRetriever) -> None:
        self.retriever = retriever

    def evaluate(
        self,
        cases: Sequence[PolicyRetrievalCase],
        *,
        top_k: int = 10,
        access: KnowledgeAccessContext | None = None,
    ) -> PolicyRetrievalEvaluation:
        results: list[PolicyRetrievalCaseResult] = []
        for case in cases:
            bundle = self.retriever.retrieve(
                case.question,
                query_variants=case.query_variants,
                limit=top_k,
                candidate_limit=max(50, min(100, top_k * 10)),
                as_of_date=case.as_of_date,
                current_only=case.current_only,
                max_per_source=3,
                access=access,
            )
            retrieved = tuple(hit.title for hit in bundle.hits)
            retrieved_ids = tuple(str(getattr(hit, "source_id", "")) for hit in bundle.hits)
            expected = set(case.expected_sources)
            expected_ids = set(case.expected_source_ids)
            matched = tuple(dict.fromkeys(title for title in retrieved if title in expected))
            matched_ids = tuple(dict.fromkeys(
                source_id for source_id in retrieved_ids if source_id in expected_ids
            ))
            relevance = case.relevance_by_source or {
                title: 1.0 for title in case.expected_sources
            }
            relevance_ids = case.relevance_by_source_id or {
                source_id: 1.0 for source_id in case.expected_source_ids
            }
            forbidden = set(case.forbidden_sources)
            forbidden_ids = set(case.forbidden_source_ids)
            retrieved_forbidden = tuple(dict.fromkeys(
                title for title in retrieved if title in forbidden
            ))
            retrieved_forbidden_ids = tuple(dict.fromkeys(
                source_id for source_id in retrieved_ids if source_id in forbidden_ids
            ))
            relevant_hits = [
                hit for hit in bundle.hits
                if hit.title in relevance or str(getattr(hit, "source_id", "")) in relevance_ids
            ]
            cited_relevant = sum(bool(hit.citation.strip()) for hit in relevant_hits)
            expected_keys = expected_ids or expected
            retrieved_keys = set(retrieved_ids) if expected_ids else set(retrieved)
            conflict_detected = bool(
                case.expected_conflict
                and len(expected_keys) >= 2
                and expected_keys.issubset(retrieved_keys)
            )
            first_rank = next(
                (
                    index for index, hit in enumerate(bundle.hits, start=1)
                    if hit.title in expected or str(getattr(hit, "source_id", "")) in expected_ids
                ),
                None,
            )
            ndcg_keys = [
                (
                    str(getattr(hit, "source_id", ""))
                    if str(getattr(hit, "source_id", "")) in relevance_ids else hit.title
                )
                for hit in bundle.hits
            ]
            combined_relevance = {**relevance, **relevance_ids}
            results.append(PolicyRetrievalCaseResult(
                case_id=case.case_id,
                question=case.question,
                expected_sources=case.expected_sources,
                retrieved_sources=retrieved,
                matched_sources=matched,
                reciprocal_rank=0.0 if first_rank is None else 1.0 / first_rank,
                expected_no_answer=case.expected_no_answer,
                ndcg_at_k=_ndcg(ndcg_keys, combined_relevance, top_k),
                citation_coverage=(
                    cited_relevant / len(relevant_hits) if relevant_hits else 1.0
                ),
                forbidden_sources=case.forbidden_sources,
                retrieved_forbidden_sources=retrieved_forbidden,
                expected_conflict=case.expected_conflict,
                conflict_detected=conflict_detected,
                relevant_evidence_count=len(relevant_hits),
                cited_relevant_evidence_count=cited_relevant,
                expected_source_ids=case.expected_source_ids,
                retrieved_source_ids=retrieved_ids,
                matched_source_ids=matched_ids,
                forbidden_source_ids=case.forbidden_source_ids,
                retrieved_forbidden_source_ids=retrieved_forbidden_ids,
            ))
        case_count = len(results)
        expected_total = sum(
            len(set(item.expected_source_ids or item.expected_sources)) for item in results
        )
        matched_total = sum(
            len(set(item.matched_source_ids or item.matched_sources)) for item in results
        )
        no_answer_results = [item for item in results if item.expected_no_answer]
        conflict_results = [item for item in results if item.expected_conflict]
        temporal_results = [
            item for item in results if item.forbidden_sources or item.forbidden_source_ids
        ]
        relevant_evidence_count = sum(item.relevant_evidence_count for item in results)
        cited_relevant_count = sum(item.cited_relevant_evidence_count for item in results)
        return PolicyRetrievalEvaluation(
            case_count=case_count,
            top_k=top_k,
            hit_rate=(sum(item.hit for item in results) / case_count if case_count else 0.0),
            recall_at_k=(matched_total / expected_total if expected_total else 0.0),
            mean_reciprocal_rank=(
                sum(item.reciprocal_rank for item in results) / case_count if case_count else 0.0
            ),
            no_answer_accuracy=(
                sum(item.hit for item in no_answer_results) / len(no_answer_results)
                if no_answer_results else 1.0
            ),
            ndcg_at_k=(
                sum(item.ndcg_at_k for item in results) / case_count if case_count else 0.0
            ),
            citation_coverage=(
                cited_relevant_count / relevant_evidence_count
                if relevant_evidence_count else 1.0
            ),
            effective_date_misuse_rate=(
                sum(bool(
                    item.retrieved_forbidden_sources or item.retrieved_forbidden_source_ids
                ) for item in temporal_results)
                / len(temporal_results) if temporal_results else 0.0
            ),
            conflict_evidence_recall=(
                sum(item.conflict_detected for item in conflict_results) / len(conflict_results)
                if conflict_results else 1.0
            ),
            temporal_guard_case_count=len(temporal_results),
            conflict_case_count=len(conflict_results),
            results=results,
        )


def _ndcg(retrieved: Sequence[str], relevance: dict[str, float], top_k: int) -> float:
    seen: set[str] = set()
    gains: list[float] = []
    for title in retrieved[:top_k]:
        if title in seen:
            gains.append(0.0)
            continue
        seen.add(title)
        gains.append(max(0.0, float(relevance.get(title, 0.0))))
    dcg = sum((2 ** gain - 1) / log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal = sorted((max(0.0, float(value)) for value in relevance.values()), reverse=True)[:top_k]
    idcg = sum((2 ** gain - 1) / log2(rank + 1) for rank, gain in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 1.0
