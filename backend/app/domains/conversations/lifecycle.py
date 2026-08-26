from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from pydantic import BaseModel, Field

from app.domains.identity import Principal
from app.domains.runs import RunPointer, RunRepository
from app.domains.turns import TERMINAL_EXECUTION_STATUSES, TurnExecutionRepository

from .repository import ConversationRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationCleanupReport(BaseModel):
    audit_id: str
    conversation_id: str
    conversation_title: str
    tenant_id: str
    owner_user_id: str
    deleted_counts: dict[str, int] = Field(default_factory=dict)
    graph_threads_deleted: int = 0
    turn_executions_deleted: int = 0
    files_deleted: int = 0
    files_skipped: int = 0
    skills_detached: int = 0
    verification_status: str
    created_at: str


class ConversationLifecycleService:
    """One deletion boundary for SQLite state, Graph checkpoints and workspace files."""

    _CASCADE_TABLES = (
        "messages",
        "attachments",
        "artifacts",
        "answer_feedback",
        "workflow_runs",
        "workflow_run_events",
        "workflow_cases",
        "recruitment_cases",
        "context_summaries",
        "context_snapshots",
        "context_semantic_facts",
    )

    def __init__(
        self,
        repository: ConversationRepository,
        runs: RunRepository,
        turn_executions: TurnExecutionRepository,
        workflow: Any,
        data_root: str | Path,
    ) -> None:
        self.repository = repository
        self.runs = runs
        self.turn_executions = turn_executions
        self.workflow = workflow
        self.database_path = Path(repository.database_path)
        self.data_root = Path(data_root).resolve()
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
            db.execute(
                """CREATE TABLE IF NOT EXISTS conversation_cleanup_audits (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    conversation_title TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE INDEX IF NOT EXISTS idx_conversation_cleanup_audits_scope
                ON conversation_cleanup_audits(tenant_id,owner_user_id,created_at DESC)"""
            )

    def delete(
        self,
        conversation_id: str,
        principal: Principal,
        *,
        admin: bool = False,
    ) -> ConversationCleanupReport:
        conversation = (
            self.repository.admin_get_conversation(conversation_id, principal)
            if admin
            else self.repository.get_conversation(conversation_id, principal)
        )
        executions = self.turn_executions.list_for_conversation(
            conversation_id,
            tenant_id=conversation.tenant_id,
            owner_user_id=conversation.owner_user_id,
        )
        active = [item for item in executions if item.status not in TERMINAL_EXECUTION_STATUSES]
        if active:
            raise RuntimeError("conversation_has_active_turn")

        deleted_counts = self._conversation_counts(conversation_id)
        run_records = self.runs.list_for_conversation(
            conversation_id,
            tenant_id=conversation.tenant_id,
            owner_user_id=conversation.owner_user_id,
        )
        for run in run_records:
            self.workflow.delete(RunPointer(
                run_id=run.id,
                tenant_id=run.tenant_id,
                owner_user_id=run.owner_user_id,
            ))

        turn_count = self.turn_executions.delete_for_conversation(
            conversation_id,
            tenant_id=conversation.tenant_id,
            owner_user_id=conversation.owner_user_id,
        )
        with self._connect() as db:
            skills_detached = int(db.execute(
                "SELECT COUNT(*) FROM skills WHERE source_conversation_id=?",
                (conversation_id,),
            ).fetchone()[0]) if self._table_exists(db, "skills") else 0
            if skills_detached:
                db.execute(
                    "UPDATE skills SET source_conversation_id=NULL WHERE source_conversation_id=?",
                    (conversation_id,),
                )

        paths = (
            self.repository.admin_delete_conversation(conversation_id, principal)
            if admin
            else self.repository.delete_conversation(conversation_id, principal)
        )
        files_deleted = 0
        files_skipped = 0
        for raw in paths:
            path = Path(raw)
            try:
                resolved = path.resolve()
                if resolved.is_file() and self.data_root in resolved.parents:
                    resolved.unlink(missing_ok=True)
                    files_deleted += 1
                else:
                    files_skipped += 1
            except OSError:
                files_skipped += 1

        leftovers = self._conversation_counts(conversation_id)
        verified = not any(leftovers.values()) and not self._conversation_exists(conversation_id)
        report = ConversationCleanupReport(
            audit_id=f"cleanup_{uuid4().hex}",
            conversation_id=conversation_id,
            conversation_title=conversation.title,
            tenant_id=conversation.tenant_id,
            owner_user_id=conversation.owner_user_id,
            deleted_counts=deleted_counts,
            graph_threads_deleted=len(run_records),
            turn_executions_deleted=turn_count,
            files_deleted=files_deleted,
            files_skipped=files_skipped,
            skills_detached=skills_detached,
            verification_status="passed" if verified else "residual_state_detected",
            created_at=_now(),
        )
        self._record_audit(report)
        if not verified:
            raise RuntimeError("conversation_cleanup_incomplete")
        return report

    def list_audits(
        self, principal: Principal, *, admin: bool = False, limit: int = 20
    ) -> list[ConversationCleanupReport]:
        where = "tenant_id=?" if admin else "tenant_id=? AND owner_user_id=?"
        values: list[Any] = [principal.tenant_id]
        if not admin:
            values.append(principal.user_id)
        values.append(max(1, min(limit, 100)))
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT report_json FROM conversation_cleanup_audits
                WHERE {where} ORDER BY created_at DESC LIMIT ?""",
                values,
            ).fetchall()
        return [ConversationCleanupReport.model_validate_json(row["report_json"]) for row in rows]

    def _record_audit(self, report: ConversationCleanupReport) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO conversation_cleanup_audits(
                id,conversation_id,conversation_title,tenant_id,owner_user_id,
                report_json,verification_status,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    report.audit_id, report.conversation_id, report.conversation_title,
                    report.tenant_id, report.owner_user_id, report.model_dump_json(),
                    report.verification_status, report.created_at,
                ),
            )

    def _conversation_counts(self, conversation_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._connect() as db:
            for table in self._CASCADE_TABLES:
                if not self._table_exists(db, table):
                    counts[table] = 0
                    continue
                if table == "workflow_run_events":
                    counts[table] = int(db.execute(
                        """SELECT COUNT(*) FROM workflow_run_events e
                        JOIN workflow_runs r ON r.id=e.run_id WHERE r.conversation_id=?""",
                        (conversation_id,),
                    ).fetchone()[0])
                else:
                    counts[table] = int(db.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE conversation_id=?",
                        (conversation_id,),
                    ).fetchone()[0])
        return counts

    def _conversation_exists(self, conversation_id: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone() is not None

    @staticmethod
    def _table_exists(db: sqlite3.Connection, table: str) -> bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None
