from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4


POLICY_EVAL_PROMPT_VERSION = "policy-evidence-graph-p1b-v2"


class PolicyEvaluationRepository:
    """Durable policy evaluation history and governed feedback candidates."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._migrate()

    def save_evaluation(
        self,
        report: dict[str, Any],
        *,
        tenant_id: str,
        actor_id: str,
        dataset_version: str,
        rerank_model: str,
        duration_ms: int,
        prompt_version: str = POLICY_EVAL_PROMPT_VERSION,
    ) -> dict[str, Any]:
        evaluation_id = f"peval_{uuid4().hex}"
        created_at = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO policy_retrieval_evaluations(
                evaluation_id,tenant_id,actor_id,dataset_version,rerank_model,prompt_version,
                duration_ms,report_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    evaluation_id, tenant_id, actor_id, dataset_version, rerank_model,
                    prompt_version, max(0, int(duration_ms)),
                    json.dumps(report, ensure_ascii=False), created_at,
                ),
            )
        return {
            "evaluation_id": evaluation_id,
            "created_at": created_at,
            "dataset_version": dataset_version,
            "rerank_model": rerank_model,
            "prompt_version": prompt_version,
            "duration_ms": max(0, int(duration_ms)),
            **report,
        }

    def list_evaluations(self, *, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM policy_retrieval_evaluations WHERE tenant_id=?
                ORDER BY created_at DESC LIMIT ?""",
                (tenant_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [self._evaluation(row) for row in rows]

    def create_feedback_candidate(
        self,
        *,
        tenant_id: str,
        user_id: str,
        feedback_id: str,
        conversation_id: str,
        message_id: str,
        question: str,
        answer: str,
        comment: str,
        trace: Sequence[dict[str, Any]],
        redaction_terms: Sequence[str] = (),
    ) -> dict[str, Any] | None:
        if not _is_policy_trace(trace):
            return None
        suggested_sources = _trace_sources(trace)
        clean_question = _deidentify(question, redaction_terms)
        clean_answer = _deidentify(answer, redaction_terms)[:8_000]
        clean_comment = _deidentify(comment, redaction_terms)[:1_000]
        now = _now()
        with self._connect() as db:
            existing = db.execute(
                "SELECT * FROM policy_evaluation_candidates WHERE tenant_id=? AND feedback_id=?",
                (tenant_id, feedback_id),
            ).fetchone()
            if existing is not None:
                return self._candidate(existing)
            candidate_id = f"pec_{uuid4().hex}"
            db.execute(
                """INSERT INTO policy_evaluation_candidates(
                candidate_id,tenant_id,feedback_id,conversation_id,message_id,question,answer_excerpt,
                comment,suggested_sources_json,status,approved_case_json,created_by,reviewed_by,
                created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'draft','{}',?,'',?,?)""",
                (
                    candidate_id, tenant_id, feedback_id, conversation_id, message_id,
                    clean_question, clean_answer, clean_comment,
                    json.dumps(suggested_sources, ensure_ascii=False), user_id, now, now,
                ),
            )
            row = db.execute(
                "SELECT * FROM policy_evaluation_candidates WHERE candidate_id=?", (candidate_id,),
            ).fetchone()
        return self._candidate(row)

    def list_candidates(
        self, *, tenant_id: str, status: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        values: list[Any] = [tenant_id]
        status_clause = ""
        if status:
            if status not in {"draft", "approved", "rejected"}:
                raise ValueError("invalid_candidate_status")
            status_clause = " AND status=?"
            values.append(status)
        values.append(max(1, min(int(limit), 200)))
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT * FROM policy_evaluation_candidates WHERE tenant_id=?{status_clause}
                ORDER BY updated_at DESC LIMIT ?""",
                values,
            ).fetchall()
        return [self._candidate(row) for row in rows]

    def review_candidate(
        self,
        candidate_id: str,
        *,
        tenant_id: str,
        reviewer_id: str,
        approved: bool,
        expected_source_ids: Sequence[str] = (),
        expected_no_answer: bool = False,
        expected_conflict: bool = False,
        forbidden_source_ids: Sequence[str] = (),
        as_of_date: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM policy_evaluation_candidates WHERE candidate_id=? AND tenant_id=?",
                (candidate_id, tenant_id),
            ).fetchone()
            if row is None:
                raise KeyError(candidate_id)
            if approved and not expected_no_answer and not expected_source_ids:
                raise ValueError("expected_sources_required")
            case = {}
            status = "rejected"
            if approved:
                status = "approved"
                case = {
                    "case_id": f"feedback_{feedback_case_digest(str(row['feedback_id']), str(row['question']))}",
                    "question": str(row["question"]),
                    "expected_sources": [],
                    "expected_source_ids": list(dict.fromkeys(expected_source_ids)),
                    "query_variants": [],
                    "expected_no_answer": bool(expected_no_answer),
                    "expected_conflict": bool(expected_conflict),
                    "forbidden_source_ids": list(dict.fromkeys(forbidden_source_ids)),
                    "as_of_date": as_of_date,
                    "review_notes": " ".join(notes.split())[:1_000],
                }
            now = _now()
            db.execute(
                """UPDATE policy_evaluation_candidates SET status=?,approved_case_json=?,
                reviewed_by=?,updated_at=? WHERE candidate_id=? AND tenant_id=?""",
                (
                    status, json.dumps(case, ensure_ascii=False), reviewer_id, now,
                    candidate_id, tenant_id,
                ),
            )
            updated = db.execute(
                "SELECT * FROM policy_evaluation_candidates WHERE candidate_id=?", (candidate_id,),
            ).fetchone()
        return self._candidate(updated)

    def approved_cases(self, *, tenant_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT approved_case_json FROM policy_evaluation_candidates
                WHERE tenant_id=? AND status='approved' ORDER BY created_at""", (tenant_id,),
            ).fetchall()
        return [
            payload for row in rows
            if (payload := json.loads(str(row[0]) or "{}"))
        ]

    def _migrate(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS policy_retrieval_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    dataset_version TEXT NOT NULL,
                    rerank_model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_policy_evaluations_tenant_created
                    ON policy_retrieval_evaluations(tenant_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS policy_evaluation_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    feedback_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer_excerpt TEXT NOT NULL,
                    comment TEXT NOT NULL,
                    suggested_sources_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('draft','approved','rejected')),
                    approved_case_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    reviewed_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(tenant_id, feedback_id)
                );
                CREATE INDEX IF NOT EXISTS idx_policy_candidates_tenant_status
                    ON policy_evaluation_candidates(tenant_id, status, updated_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _evaluation(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "evaluation_id": row["evaluation_id"], "created_at": row["created_at"],
            "dataset_version": row["dataset_version"], "rerank_model": row["rerank_model"],
            "prompt_version": row["prompt_version"], "duration_ms": int(row["duration_ms"]),
            **json.loads(str(row["report_json"])),
        }

    @staticmethod
    def _candidate(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "candidate_id": row["candidate_id"], "feedback_id": row["feedback_id"],
            "conversation_id": row["conversation_id"], "message_id": row["message_id"],
            "question": row["question"], "answer_excerpt": row["answer_excerpt"],
            "comment": row["comment"],
            "suggested_sources": json.loads(str(row["suggested_sources_json"])),
            "status": row["status"],
            "approved_case": json.loads(str(row["approved_case_json"] or "{}")),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }


def _is_policy_trace(trace: Sequence[dict[str, Any]]) -> bool:
    events = {str(item.get("event") or item.get("stage") or "") for item in trace}
    return bool(events & {
        "evidence.plan", "knowledge.retrieve", "policy.effective_date_gate",
        "policy.conflict_gate", "evidence.graph.completed",
    })


def _trace_sources(trace: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in trace:
        if str(item.get("event") or "") != "evidence.sources":
            continue
        for source in item.get("sources") or []:
            source_id = str(source.get("source_id") or "")
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)
            result.append({
                "source_id": source_id,
                "title": str(source.get("title") or ""),
                "version": str(source.get("source_version") or ""),
                "status": str(source.get("policy_status") or "unknown"),
            })
    return result[:20]


def _deidentify(value: str, terms: Sequence[str]) -> str:
    clean = value
    clean = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<EMAIL>", clean)
    clean = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "<PHONE>", clean)
    clean = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "<ID>", clean)
    for term in terms:
        if term and len(term.strip()) >= 2:
            clean = clean.replace(term.strip(), "<USER>")
    return " ".join(clean.split())


def feedback_case_digest(feedback_id: str, question: str) -> str:
    return hashlib.sha256(f"{feedback_id}:{question}".encode("utf-8")).hexdigest()[:16]


def dataset_digest(payload: Sequence[dict[str, Any]]) -> str:
    encoded = json.dumps(list(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
