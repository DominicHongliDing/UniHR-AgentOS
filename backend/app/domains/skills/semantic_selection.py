from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Sequence

import httpx
from pydantic import BaseModel, Field

from app.domains.assistant.contracts import TaskIntent
from app.models.embeddings import EmbeddingGateway
from app.models.usage_store import ModelUsageStore
from app.settings import Settings

from .contracts import SkillRecord


class SemanticSkillMatch(BaseModel):
    skill_id: str
    score: float = Field(ge=0, le=1)
    positive_score: float = Field(default=0, ge=-1, le=1)
    negative_score: float = Field(default=0, ge=-1, le=1)
    reason: str = ""


class SemanticSkillBatch(BaseModel):
    matches: list[SemanticSkillMatch] = Field(default_factory=list)
    provider: str = "none"
    fallback_used: bool = False


class SkillRerankCandidate(BaseModel):
    skill_id: str
    display_name: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    base_score: float = 0


class SkillRerankMatch(BaseModel):
    skill_id: str
    score: float = Field(ge=0, le=1)
    reason: str = ""


class SkillRerankBatch(BaseModel):
    matches: list[SkillRerankMatch] = Field(default_factory=list)
    provider: str = "none"
    fallback_used: bool = False


class SkillSemanticRetriever(Protocol):
    def retrieve(
        self,
        query: str,
        candidates: Sequence[SkillRecord],
        *,
        task_intent: TaskIntent | None,
        required_capabilities: Sequence[str],
        limit: int,
    ) -> SemanticSkillBatch: ...


class SkillCandidateReranker(Protocol):
    def rerank(
        self,
        query: str,
        candidates: Sequence[SkillRerankCandidate],
        *,
        limit: int,
    ) -> SkillRerankBatch: ...


class NullSkillSemanticRetriever:
    def retrieve(
        self,
        query: str,
        candidates: Sequence[SkillRecord],
        *,
        task_intent: TaskIntent | None,
        required_capabilities: Sequence[str],
        limit: int,
    ) -> SemanticSkillBatch:
        return SemanticSkillBatch(provider="disabled", fallback_used=True)


class LocalSkillCandidateReranker:
    """Deterministic adapter used when the provider is unavailable."""

    name = "local-skill-rerank-v1"

    def rerank(
        self,
        query: str,
        candidates: Sequence[SkillRerankCandidate],
        *,
        limit: int,
    ) -> SkillRerankBatch:
        ordered = sorted(candidates, key=lambda item: (-item.base_score, item.display_name.casefold()))
        ceiling = max((item.base_score for item in ordered), default=1.0) or 1.0
        return SkillRerankBatch(
            matches=[
                SkillRerankMatch(
                    skill_id=item.skill_id,
                    score=max(0.0, min(1.0, item.base_score / ceiling)),
                    reason="deterministic base-score order",
                )
                for item in ordered[:limit]
            ],
            provider=self.name,
            fallback_used=True,
        )


class EmbeddingSkillSemanticRetriever:
    """Persistent Manifest-vector adapter behind the Skill Orchestrator interface."""

    def __init__(
        self,
        database_path: str | Path,
        embedding_gateway: EmbeddingGateway,
        *,
        min_score: float = 0.32,
    ) -> None:
        self.database_path = Path(database_path)
        self.embedding_gateway = embedding_gateway
        self.min_score = min_score
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS skill_semantic_vectors (
                    skill_id TEXT NOT NULL,
                    skill_version INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    positive_vector_json TEXT NOT NULL,
                    negative_vector_json TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    PRIMARY KEY(skill_id, skill_version, embedding_model)
                );
                CREATE INDEX IF NOT EXISTS idx_skill_semantic_latest
                ON skill_semantic_vectors(skill_id, skill_version);
                """
            )

    def retrieve(
        self,
        query: str,
        candidates: Sequence[SkillRecord],
        *,
        task_intent: TaskIntent | None,
        required_capabilities: Sequence[str],
        limit: int,
    ) -> SemanticSkillBatch:
        skills = list(candidates)
        if not query.strip() or not skills:
            return SemanticSkillBatch(provider=self.embedding_gateway.model_name)

        query_text = _semantic_query_text(query, task_intent, required_capabilities)
        cached: dict[str, tuple[list[float], list[float]]] = {}
        missing: list[tuple[SkillRecord, str, str, str]] = []
        with self._connect() as db:
            for skill in skills:
                content_hash = _skill_content_hash(skill)
                row = db.execute(
                    """SELECT positive_vector_json,negative_vector_json,content_sha256
                    FROM skill_semantic_vectors
                    WHERE skill_id=? AND skill_version=? AND embedding_model=?""",
                    (skill.id, skill.version, self.embedding_gateway.model_name),
                ).fetchone()
                if row is not None and str(row["content_sha256"]) == content_hash:
                    cached[skill.id] = (
                        list(map(float, json.loads(row["positive_vector_json"]))),
                        list(map(float, json.loads(row["negative_vector_json"]))),
                    )
                    continue
                positive, negative = _skill_semantic_documents(skill)
                missing.append((skill, content_hash, positive, negative))

        texts = [query_text]
        for _, _, positive, negative in missing:
            texts.extend([positive, negative])
        vectors = self.embedding_gateway.embed(texts)
        if len(vectors) != len(texts):
            raise ValueError("skill_semantic_embedding_count_mismatch")
        query_vector = vectors[0]
        if missing:
            now = datetime.now(timezone.utc).isoformat()
            with self._connect() as db:
                for index, (skill, content_hash, _, _) in enumerate(missing):
                    positive_vector = vectors[1 + index * 2]
                    negative_vector = vectors[2 + index * 2]
                    cached[skill.id] = (positive_vector, negative_vector)
                    db.execute(
                        """INSERT INTO skill_semantic_vectors(
                        skill_id,skill_version,content_sha256,embedding_model,
                        positive_vector_json,negative_vector_json,indexed_at
                        ) VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(skill_id,skill_version,embedding_model) DO UPDATE SET
                        content_sha256=excluded.content_sha256,
                        positive_vector_json=excluded.positive_vector_json,
                        negative_vector_json=excluded.negative_vector_json,
                        indexed_at=excluded.indexed_at""",
                        (
                            skill.id, skill.version, content_hash, self.embedding_gateway.model_name,
                            json.dumps(positive_vector), json.dumps(negative_vector), now,
                        ),
                    )

        matches: list[SemanticSkillMatch] = []
        for skill in skills:
            positive_vector, negative_vector = cached[skill.id]
            positive_score = _cosine(query_vector, positive_vector)
            negative_score = _cosine(query_vector, negative_vector)
            # Negative examples only suppress a candidate when they are materially closer.
            adjusted = positive_score - max(0.0, negative_score - positive_score + 0.05) * 0.8
            normalized = max(0.0, min(1.0, (adjusted + 1.0) / 2.0))
            if normalized < self.min_score:
                continue
            matches.append(SemanticSkillMatch(
                skill_id=skill.id,
                score=normalized,
                positive_score=positive_score,
                negative_score=negative_score,
                reason="manifest/examples embedding similarity",
            ))
        matches.sort(key=lambda item: (-item.score, item.skill_id))
        return SemanticSkillBatch(
            matches=matches[:limit],
            provider=self.embedding_gateway.model_name,
        )


class BailianSkillCandidateReranker:
    """Small rerank-model adapter with a deterministic failure path."""

    endpoint = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

    def __init__(self, settings: Settings, fallback: SkillCandidateReranker | None = None) -> None:
        self.settings = settings
        self.fallback = fallback or LocalSkillCandidateReranker()
        self.usage_store = ModelUsageStore(settings) if settings.database_path is not None else None

    def rerank(
        self,
        query: str,
        candidates: Sequence[SkillRerankCandidate],
        *,
        limit: int,
    ) -> SkillRerankBatch:
        rows = list(candidates)
        if not rows:
            return SkillRerankBatch(provider=self.settings.rerank_model)
        if not self.settings.rerank_enabled or not self.settings.model_api_key:
            return self.fallback.rerank(query, rows, limit=limit)
        documents = [
            "\n".join([
                item.display_name,
                item.description,
                "Capabilities: " + ", ".join(item.capabilities),
                "Examples: " + " | ".join(item.examples[:4]),
            ])[:12_000]
            for item in rows
        ]
        try:
            with httpx.Client(timeout=self.settings.rerank_timeout_seconds) as client:
                response = client.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.settings.model_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.rerank_model,
                        "input": {"query": query[:12_000], "documents": documents},
                        "parameters": {
                            "top_n": min(len(rows), max(1, limit)),
                            "instruct": (
                                "Rank reusable HR Agent Skills that can execute the user's task. "
                                "Prefer direct capability and example fit; do not infer permissions."
                            ),
                        },
                    },
                )
                response.raise_for_status()
                results = response.json()["output"]["results"]
            matches = []
            for result in results:
                index = int(result["index"])
                if index < 0 or index >= len(rows):
                    continue
                score = float(result.get("relevance_score", result.get("score", 0)))
                matches.append(SkillRerankMatch(
                    skill_id=rows[index].skill_id,
                    score=max(0.0, min(1.0, score)),
                    reason="qwen candidate rerank",
                ))
            if not matches:
                return self.fallback.rerank(query, rows, limit=limit)
            if self.usage_store is not None:
                self.usage_store.record(
                    provider="bailian",
                    model=self.settings.rerank_model,
                    purpose="skill_candidate_rerank",
                    operation_kind="rerank",
                    input_units=len(documents),
                    unit_type="documents",
                    status="succeeded",
                    metadata={"top_n": min(len(documents), max(1, limit))},
                )
            return SkillRerankBatch(matches=matches, provider=self.settings.rerank_model)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError):
            return self.fallback.rerank(query, rows, limit=limit)


def rerank_candidate_for(skill: SkillRecord, base_score: float) -> SkillRerankCandidate:
    examples = []
    for raw in skill.metadata.get("examples") or []:
        if isinstance(raw, dict):
            value = str(raw.get("user_request") or "").strip()
        else:
            value = str(raw).strip()
        if value:
            examples.append(value)
    return SkillRerankCandidate(
        skill_id=skill.id,
        display_name=skill.display_name,
        description=skill.description,
        capabilities=skill.capabilities,
        examples=examples,
        base_score=base_score,
    )


def _semantic_query_text(
    query: str,
    task_intent: TaskIntent | None,
    required_capabilities: Sequence[str],
) -> str:
    parts = [query.strip()]
    if task_intent is not None:
        parts.extend([
            f"Objective: {task_intent.objective}",
            f"Domain: {task_intent.domain.value}",
            "Deliverables: " + ", ".join(task_intent.deliverables),
        ])
    if required_capabilities:
        parts.append("Required capabilities: " + ", ".join(required_capabilities))
    return "\n".join(item for item in parts if item and not item.endswith(": "))[:16_000]


def _skill_semantic_documents(skill: SkillRecord) -> tuple[str, str]:
    positive_examples = []
    for raw in skill.metadata.get("examples") or []:
        value = str(raw.get("user_request") or "") if isinstance(raw, dict) else str(raw)
        if value.strip():
            positive_examples.append(value.strip())
    negative_examples = []
    for raw in skill.metadata.get("non_examples") or []:
        value = str(raw.get("user_request") or "") if isinstance(raw, dict) else str(raw)
        if value.strip():
            negative_examples.append(value.strip())
    positive = "\n".join([
        skill.display_name,
        skill.description,
        "Triggers: " + " | ".join(skill.trigger_phrases),
        "Capabilities: " + ", ".join(skill.capabilities),
        "Examples: " + " | ".join(positive_examples),
    ])
    negative = "\n".join(negative_examples) or "Unrelated casual conversation"
    return positive[:16_000], negative[:8_000]


def _skill_content_hash(skill: SkillRecord) -> str:
    stored = str(skill.metadata.get("content_sha256") or "").strip()
    if stored:
        return stored
    payload = json.dumps({
        "name": skill.name,
        "description": skill.description,
        "triggers": skill.trigger_phrases,
        "capabilities": skill.capabilities,
        "examples": skill.metadata.get("examples") or [],
        "non_examples": skill.metadata.get("non_examples") or [],
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return max(-1.0, min(1.0, numerator / (left_norm * right_norm)))
