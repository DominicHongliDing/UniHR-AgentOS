from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import re
from typing import Sequence

from app.observability import emit_tool_progress

from .contracts import KnowledgeAccessContext
from .reranker import LocalPolicyReranker, PolicyReranker
from .vector_index import KnowledgeVectorIndex, SearchHit


@dataclass(frozen=True)
class RetrievalBundle:
    hits: list[SearchHit]
    queries: list[str]
    coverage: float
    status: str
    candidate_count: int = 0
    filtered_count: int = 0
    rerank_model: str = ""
    relevance_filtered_count: int = 0
    rerank_filtered_count: int = 0


class PolicyKnowledgeRetriever:
    """Run bounded multi-query hybrid retrieval behind one small interface."""

    def __init__(
        self,
        index: KnowledgeVectorIndex,
        reranker: PolicyReranker | None = None,
    ) -> None:
        self.index = index
        self.reranker = reranker or LocalPolicyReranker()

    def retrieve(
        self,
        query: str,
        *,
        resolved_query: str = "",
        query_variants: Sequence[str] = (),
        limit: int = 6,
        candidate_limit: int | None = None,
        as_of_date: str | None = None,
        current_only: bool = False,
        max_per_source: int = 3,
        access: KnowledgeAccessContext | None = None,
    ) -> RetrievalBundle:
        queries = list(dict.fromkeys(
            item.strip()
            for item in (query, resolved_query, *query_variants)
            if item.strip()
        ))[:6]
        resolved_candidate_limit = candidate_limit or max(30, min(100, limit * 8))
        emit_tool_progress(
            "tool.policy.hybrid_recall.started",
            f"正在用 {len(queries)} 组查询执行向量与 BM25 混合召回",
            query_count=len(queries),
            candidate_limit=resolved_candidate_limit,
        )
        raw_rankings = [
            self._search_index(
                item,
                resolved_candidate_limit,
                access,
                include_withdrawn=bool(as_of_date and not current_only),
            )
            for item in queries
        ]
        raw_count = sum(len(ranking) for ranking in raw_rankings)
        emit_tool_progress(
            "tool.policy.hybrid_recall.completed",
            f"混合召回完成，共获得 {raw_count} 条候选片段",
            status="completed" if raw_count else "waiting",
            query_count=len(queries),
            raw_candidate_count=raw_count,
        )
        rankings = [
            [
                hit for hit in ranking
                if _policy_is_applicable(hit, as_of_date=as_of_date, current_only=current_only)
            ]
            for ranking in raw_rankings
        ]
        if as_of_date:
            rankings = [
                [hit for hit in ranking if _temporal_query_matches(queries, hit)]
                for ranking in rankings
            ]
        filtered_count = raw_count - sum(len(ranking) for ranking in rankings)
        by_chunk: dict[str, SearchHit] = {}
        fused: dict[str, float] = {}
        for ranking in rankings:
            for rank, hit in enumerate(ranking, start=1):
                key = hit.chunk_id
                by_chunk.setdefault(key, hit)
                fused[key] = fused.get(key, 0.0) + 1.0 / (60 + rank)

        max_rrf = max(1, len(queries)) / 61
        reranked = [
            replace(
                by_chunk[key],
                score=min(
                    1.0,
                    score / max_rrf + _policy_preference_bonus(by_chunk[key]),
                ),
                retrieval_method=(
                    f"multi_query_{by_chunk[key].retrieval_method}"
                    if len(queries) > 1 else by_chunk[key].retrieval_method
                ),
            )
            for key, score in sorted(
                fused.items(),
                key=lambda item: (
                    item[1] + _policy_preference_bonus(by_chunk[item[0]]) * max_rrf,
                    _sortable_date(by_chunk[item[0]].effective_date),
                ),
                reverse=True,
            )
        ]
        rerank_input_count = len(reranked[:max(20, min(100, resolved_candidate_limit))])
        emit_tool_progress(
            "tool.policy.rerank.started",
            f"正在重排 {rerank_input_count} 条候选政策证据",
            candidate_count=rerank_input_count,
            rerank_model=self.reranker.name,
        )
        reranked = self.reranker.rerank(
            resolved_query or query,
            reranked[:max(20, min(100, resolved_candidate_limit))],
            limit=max(limit * 3, limit),
        )
        emit_tool_progress(
            "tool.policy.rerank.completed",
            f"证据重排完成，保留 {len(reranked)} 条高相关候选",
            status="completed" if reranked else "waiting",
            input_count=rerank_input_count,
            reranked_count=len(reranked),
            rerank_model=self.reranker.name,
        )
        rerank_filtered_count = 0
        if reranked and reranked[0].rerank_score >= 0.75:
            relative_floor = reranked[0].rerank_score * 0.65
            retained = [item for item in reranked if item.rerank_score >= relative_floor]
            rerank_filtered_count = len(reranked) - len(retained)
            reranked = retained
        selected: list[SearchHit] = []
        per_source: dict[str, int] = {}
        for hit in reranked:
            if per_source.get(hit.source_id, 0) >= max(1, max_per_source):
                continue
            selected.append(hit)
            per_source[hit.source_id] = per_source.get(hit.source_id, 0) + 1
            if len(selected) >= max(1, min(limit, 12)):
                break

        coverage = _query_coverage(resolved_query or query, selected)
        relevance_filtered_count = 0
        if (
            selected
            and coverage < 0.12
            and max(item.rerank_score for item in selected) < 0.60
        ):
            relevance_filtered_count = len(selected)
            selected = []
            coverage = 0.0
        status = "sufficient" if selected and (coverage >= 0.18 or selected[0].score >= 0.45) else "insufficient"
        emit_tool_progress(
            "tool.policy.select.completed",
            f"已选择 {len(selected)} 条受控证据进入回答链路",
            status="completed" if selected else "waiting",
            selected_count=len(selected),
            source_count=len({item.source_id for item in selected}),
            lexical_coverage=round(coverage, 3),
            filtered_count=filtered_count + relevance_filtered_count + rerank_filtered_count,
        )
        return RetrievalBundle(
            hits=selected,
            queries=queries,
            coverage=coverage,
            status=status,
            candidate_count=len(by_chunk),
            filtered_count=filtered_count,
            rerank_model=self.reranker.name,
            relevance_filtered_count=relevance_filtered_count,
            rerank_filtered_count=rerank_filtered_count,
        )

    def _search_index(
        self,
        query: str,
        limit: int,
        access: KnowledgeAccessContext | None,
        *,
        include_withdrawn: bool,
    ) -> list[SearchHit]:
        try:
            return self.index.search(
                query,
                limit=limit,
                access=access,
                include_withdrawn=include_withdrawn,
            )
        except TypeError as exc:
            # Compatibility for minimal in-memory adapters that implement the pre-ACL
            # search interface. Production always uses the ACL-aware index adapter.
            if "access" not in str(exc) and "include_withdrawn" not in str(exc):
                raise
            return self.index.search(query, limit=limit)


def _query_coverage(query: str, hits: Sequence[SearchHit]) -> float:
    normalized = re.sub(r"\s+", "", query.casefold())
    terms = {
        normalized[index:index + 2]
        for index in range(max(0, len(normalized) - 1))
        if normalized[index:index + 2].strip()
    }
    if not terms or not hits:
        return 0.0
    evidence = re.sub(
        r"\s+",
        "",
        "\n".join(f"{hit.title} {hit.section} {hit.text}" for hit in hits).casefold(),
    )
    return sum(term in evidence for term in terms) / len(terms)


def _policy_is_applicable(
    hit: SearchHit,
    *,
    as_of_date: str | None,
    current_only: bool,
) -> bool:
    if current_only and hit.policy_status == "repealed":
        return False
    target = _parse_date(as_of_date) if as_of_date else date.today()
    publication = _parse_date(hit.publication_date)
    effective = _parse_date(hit.effective_date)
    expiry = _parse_date(hit.expiry_date)
    # A document cannot be evidence for a point in time before it was published,
    # even when the document has no separately declared effective date.
    if as_of_date and publication is not None and publication > target:
        return False
    if as_of_date and effective is not None and effective > target:
        return False
    if as_of_date and publication is None and effective is None:
        named_years = [
            int(value) for value in re.findall(
                r"(?<!\d)((?:19|20)\d{2})(?!\d)",
                f"{hit.source_version or ''} {hit.title}",
            )
        ]
        if named_years and min(named_years) > target.year:
            return False
    if expiry is not None and expiry < target:
        return False
    return True


def _temporal_query_matches(queries: Sequence[str], hit: SearchHit) -> bool:
    """Keep point-in-time retrieval from falling through to unrelated undated files."""

    normalized = re.sub(r"\s+", "", "".join(queries).casefold())
    normalized = re.sub(r"\d{4}年?\d{0,2}月?\d{0,2}日?", "", normalized)
    generic = {
        "截至", "年度", "安排", "什么", "如何", "是否", "可以", "规定", "政策",
        "通知", "文件", "目前", "当时", "执行", "适用", "内容", "要求",
    }
    anchors = {
        normalized[index:index + size]
        for size in (2, 3)
        for index in range(max(0, len(normalized) - size + 1))
        if normalized[index:index + size] not in generic
        and not re.search(r"\d", normalized[index:index + size])
    }
    if len(anchors) < 4:
        return True
    evidence = re.sub(
        r"\s+", "", f"{hit.title}{hit.section}{hit.text}".casefold(),
    )
    return sum(anchor in evidence for anchor in anchors) >= 2


def _policy_preference_bonus(hit: SearchHit) -> float:
    status_bonus = {"active": 0.035, "trial": 0.02, "unknown": 0.0}.get(
        hit.policy_status,
        -0.05,
    )
    authority_bonus = {
        "national": 0.025,
        "provincial": 0.02,
        "municipal": 0.015,
        "institution": 0.01,
        "organization": 0.008,
    }.get(hit.authority_level, 0.0)
    return status_bonus + authority_bonus


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _sortable_date(value: str | None) -> str:
    return value or "0000-00-00"
