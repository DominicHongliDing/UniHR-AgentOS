from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from pydantic import BaseModel, Field


class ContextRetentionPolicy(BaseModel):
    snapshot_days: int = Field(default=90, ge=7, le=3650)
    snapshot_keep_latest: int = Field(default=20, ge=1, le=500)
    summary_days: int = Field(default=180, ge=7, le=3650)
    run_payload_days: int = Field(default=30, ge=1, le=3650)
    turn_execution_days: int = Field(default=30, ge=1, le=3650)
    model_usage_days: int = Field(default=180, ge=7, le=3650)


class RetentionCandidateMetric(BaseModel):
    kind: str
    count: int = 0
    byte_size: int = 0
    oldest_created_at: str = ""


class ContextRetentionReport(BaseModel):
    audit_id: str
    tenant_id: str
    dry_run: bool
    status: str
    policy: ContextRetentionPolicy
    candidates: list[RetentionCandidateMetric]
    deleted_counts: dict[str, int] = Field(default_factory=dict)
    reclaimed_bytes: int = 0
    created_at: str


class ContextRetentionService:
    """Tenant-scoped lifecycle cleanup with dry-run as the safe default."""

    _TERMINAL_RUN_STATUSES = ("succeeded", "failed", "exhausted", "rejected")
    _TERMINAL_TURN_STATUSES = ("succeeded", "failed", "cancelled")

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS context_retention_audits (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    dry_run INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    deleted_counts_json TEXT NOT NULL,
                    reclaimed_bytes INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_context_retention_audits_tenant
                    ON context_retention_audits(tenant_id, created_at DESC);
                """
            )

    def run(
        self,
        tenant_id: str,
        policy: ContextRetentionPolicy | None = None,
        *,
        execute: bool = False,
    ) -> ContextRetentionReport:
        policy = policy or ContextRetentionPolicy()
        now = datetime.now(timezone.utc)
        cutoffs = {
            "context_snapshots": (now - timedelta(days=policy.snapshot_days)).isoformat(),
            "context_summaries": (now - timedelta(days=policy.summary_days)).isoformat(),
            "run_payloads": (now - timedelta(days=policy.run_payload_days)).isoformat(),
            "turn_executions": (now - timedelta(days=policy.turn_execution_days)).isoformat(),
            "model_usage_events": (now - timedelta(days=policy.model_usage_days)).isoformat(),
        }
        with self._connect() as db:
            candidate_ids = self._candidate_ids(db, tenant_id, policy, cutoffs)
            candidates = self._candidate_metrics(db, candidate_ids)
            deleted_counts: dict[str, int] = {}
            reclaimed_bytes = 0
            if execute:
                reclaimed_bytes = next(
                    (item.byte_size for item in candidates if item.kind == "run_payloads"), 0
                )
                for kind in (
                    "context_snapshots", "context_summaries", "run_payloads",
                    "turn_executions", "model_usage_events",
                ):
                    ids = candidate_ids[kind]
                    if not ids:
                        deleted_counts[kind] = 0
                        continue
                    placeholders = ",".join("?" for _ in ids)
                    cursor = db.execute(f"DELETE FROM {kind} WHERE id IN ({placeholders})", ids)
                    deleted_counts[kind] = max(0, cursor.rowcount)
            audit_id = f"retention_{uuid4().hex}"
            created_at = now.isoformat()
            report = ContextRetentionReport(
                audit_id=audit_id,
                tenant_id=tenant_id,
                dry_run=not execute,
                status="executed" if execute else "previewed",
                policy=policy,
                candidates=candidates,
                deleted_counts=deleted_counts,
                reclaimed_bytes=reclaimed_bytes,
                created_at=created_at,
            )
            db.execute(
                """INSERT INTO context_retention_audits(
                id,tenant_id,dry_run,status,policy_json,candidates_json,
                deleted_counts_json,reclaimed_bytes,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    audit_id,
                    tenant_id,
                    int(not execute),
                    report.status,
                    json.dumps(policy.model_dump(mode="json"), ensure_ascii=False),
                    json.dumps([item.model_dump(mode="json") for item in candidates], ensure_ascii=False),
                    json.dumps(deleted_counts, ensure_ascii=False),
                    reclaimed_bytes,
                    created_at,
                ),
            )
        return report

    def _candidate_ids(
        self,
        db: sqlite3.Connection,
        tenant_id: str,
        policy: ContextRetentionPolicy,
        cutoffs: dict[str, str],
    ) -> dict[str, list[str]]:
        tables = {
            str(row["name"])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        result = {kind: [] for kind in (
            "context_snapshots", "context_summaries", "run_payloads",
            "turn_executions", "model_usage_events",
        )}
        if "context_snapshots" in tables:
            rows = db.execute(
                """WITH ranked AS (
                    SELECT s.id,s.created_at,
                    ROW_NUMBER() OVER(PARTITION BY s.conversation_id ORDER BY s.created_at DESC) AS position
                    FROM context_snapshots s
                    JOIN conversations c ON c.id=s.conversation_id
                    WHERE c.tenant_id=?
                )
                SELECT id FROM ranked WHERE position>? AND created_at<?""",
                (tenant_id, policy.snapshot_keep_latest, cutoffs["context_snapshots"]),
            ).fetchall()
            result["context_snapshots"] = [str(row["id"]) for row in rows]
        if "context_summaries" in tables:
            rows = db.execute(
                """WITH ranked AS (
                    SELECT s.id,s.created_at,
                    ROW_NUMBER() OVER(PARTITION BY s.conversation_id ORDER BY s.version DESC) AS position
                    FROM context_summaries s
                    JOIN conversations c ON c.id=s.conversation_id
                    WHERE c.tenant_id=?
                )
                SELECT r.id FROM ranked r
                WHERE r.position>1 AND r.created_at<?
                AND NOT EXISTS(SELECT 1 FROM context_snapshots x WHERE x.summary_id=r.id)""",
                (tenant_id, cutoffs["context_summaries"]),
            ).fetchall()
            result["context_summaries"] = [str(row["id"]) for row in rows]
        if {"run_payloads", "workflow_runs"}.issubset(tables):
            placeholders = ",".join("?" for _ in self._TERMINAL_RUN_STATUSES)
            rows = db.execute(
                f"""SELECT p.id FROM run_payloads p
                JOIN workflow_runs r ON r.id=p.run_id
                WHERE p.tenant_id=? AND r.tenant_id=? AND r.status IN ({placeholders})
                AND p.created_at<?""",
                (tenant_id, tenant_id, *self._TERMINAL_RUN_STATUSES, cutoffs["run_payloads"]),
            ).fetchall()
            result["run_payloads"] = [str(row["id"]) for row in rows]
        if "turn_executions" in tables:
            placeholders = ",".join("?" for _ in self._TERMINAL_TURN_STATUSES)
            rows = db.execute(
                f"""SELECT id FROM turn_executions WHERE tenant_id=?
                AND status IN ({placeholders}) AND updated_at<?""",
                (tenant_id, *self._TERMINAL_TURN_STATUSES, cutoffs["turn_executions"]),
            ).fetchall()
            result["turn_executions"] = [str(row["id"]) for row in rows]
        if "model_usage_events" in tables:
            rows = db.execute(
                """SELECT id FROM model_usage_events
                WHERE tenant_id=? AND created_at<?""",
                (tenant_id, cutoffs["model_usage_events"]),
            ).fetchall()
            result["model_usage_events"] = [str(row["id"]) for row in rows]
        return result

    @staticmethod
    def _candidate_metrics(
        db: sqlite3.Connection,
        candidate_ids: dict[str, list[str]],
    ) -> list[RetentionCandidateMetric]:
        metrics: list[RetentionCandidateMetric] = []
        for kind, ids in candidate_ids.items():
            if not ids:
                metrics.append(RetentionCandidateMetric(kind=kind))
                continue
            placeholders = ",".join("?" for _ in ids)
            size_expression = "COALESCE(SUM(byte_size),0)" if kind == "run_payloads" else "0"
            time_column = "updated_at" if kind == "turn_executions" else "created_at"
            row = db.execute(
                f"SELECT COUNT(*) count,{size_expression} byte_size,MIN({time_column}) oldest FROM {kind} WHERE id IN ({placeholders})",
                ids,
            ).fetchone()
            metrics.append(RetentionCandidateMetric(
                kind=kind,
                count=int(row["count"] or 0),
                byte_size=int(row["byte_size"] or 0),
                oldest_created_at=str(row["oldest"] or ""),
            ))
        return metrics
