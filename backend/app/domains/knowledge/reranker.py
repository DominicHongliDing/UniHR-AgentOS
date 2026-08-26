from __future__ import annotations

from dataclasses import replace
from typing import Protocol, Sequence

import httpx

from app.settings import Settings
from app.models.usage_store import ModelUsageStore

from .vector_index import SearchHit


class PolicyReranker(Protocol):
    name: str

    def rerank(self, query: str, hits: Sequence[SearchHit], *, limit: int) -> list[SearchHit]: ...


class LocalPolicyReranker:
    """Deterministic fallback used in tests and provider outages."""

    name = "local-policy-rerank-v1"

    def rerank(self, query: str, hits: Sequence[SearchHit], *, limit: int) -> list[SearchHit]:
        ordered = sorted(
            hits,
            key=lambda item: (
                item.score,
                item.lexical_score,
                item.effective_date or "0000-00-00",
            ),
            reverse=True,
        )
        return [replace(item, rerank_score=item.score, rerank_model=self.name) for item in ordered[:limit]]


class BailianPolicyReranker:
    """Qwen3 rerank adapter with deterministic fallback behind one internal seam."""

    endpoint = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

    def __init__(self, settings: Settings, fallback: PolicyReranker | None = None) -> None:
        self.settings = settings
        self.name = settings.rerank_model
        self.fallback = fallback or LocalPolicyReranker()
        self.usage_store = ModelUsageStore(settings) if settings.database_path is not None else None

    def rerank(self, query: str, hits: Sequence[SearchHit], *, limit: int) -> list[SearchHit]:
        candidates = list(hits)
        if not candidates:
            return []
        if not self.settings.rerank_enabled or not self.settings.model_api_key:
            return self.fallback.rerank(query, candidates, limit=limit)
        documents = [f"{item.title}\n{item.section}\n{item.text}"[:16_000] for item in candidates]
        payload = {
            "model": self.settings.rerank_model,
            "input": {
                "query": query[:12_000],
                "documents": documents,
            },
            "parameters": {
                "top_n": min(len(documents), max(1, limit)),
                "instruct": "Given an HR policy question, rank passages that directly answer it and preserve exact policy terms.",
            },
        }
        try:
            with httpx.Client(timeout=self.settings.rerank_timeout_seconds) as client:
                response = client.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.settings.model_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                results = response.json()["output"]["results"]
            if self.usage_store is not None:
                self.usage_store.record(
                    provider="bailian",
                    model=self.settings.rerank_model,
                    purpose="policy_rerank",
                    operation_kind="rerank",
                    input_units=len(documents),
                    unit_type="documents",
                    status="succeeded",
                    metadata={"top_n": min(len(documents), max(1, limit))},
                )
            reranked: list[SearchHit] = []
            for row in results:
                index = int(row["index"])
                score = float(row.get("relevance_score", row.get("score", 0.0)))
                if index < 0 or index >= len(candidates) or score < self.settings.rerank_min_score:
                    continue
                reranked.append(replace(
                    candidates[index],
                    score=max(0.0, min(1.0, score)),
                    rerank_score=score,
                    rerank_model=self.settings.rerank_model,
                    retrieval_method=f"{candidates[index].retrieval_method}+rerank",
                ))
            return reranked or self.fallback.rerank(query, candidates, limit=limit)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError):
            return self.fallback.rerank(query, candidates, limit=limit)
