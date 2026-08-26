from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .contracts import (
    ApprovalQueueItem,
    RunEventRecord,
    RunPointer,
    RunRecord,
    RunStatus,
    SkillNodeOperations,
    WorkflowRunRequest,
    WorkflowRunResult,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunRepository:
    """Own tenant-scoped Run status and audit events independently of checkpoints."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
                    status TEXT NOT NULL,
                    business_intent TEXT NOT NULL,
                    turn_action TEXT NOT NULL,
                    execution_mode TEXT NOT NULL,
                    iteration INTEGER NOT NULL DEFAULT 0,
                    max_iterations INTEGER NOT NULL,
                    graph_path_json TEXT NOT NULL DEFAULT '[]',
                    quality_issues_json TEXT NOT NULL DEFAULT '[]',
                    requires_human_review INTEGER NOT NULL DEFAULT 0,
                    checkpoint_thread_id TEXT NOT NULL DEFAULT '',
                    error_code TEXT,
                    approval_decision INTEGER,
                    approval_comment TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_owner_updated
                    ON workflow_runs(tenant_id, owner_user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_conversation
                    ON workflow_runs(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS workflow_run_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(workflow_runs)").fetchall()}
            if "approval_decision" not in columns:
                db.execute("ALTER TABLE workflow_runs ADD COLUMN approval_decision INTEGER")
            if "approval_comment" not in columns:
                db.execute("ALTER TABLE workflow_runs ADD COLUMN approval_comment TEXT NOT NULL DEFAULT ''")
            if "message_id" not in columns:
                db.execute("ALTER TABLE workflow_runs ADD COLUMN message_id TEXT REFERENCES messages(id) ON DELETE SET NULL")

    def start(self, request: WorkflowRunRequest) -> RunRecord:
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO workflow_runs(
                    id,tenant_id,owner_user_id,conversation_id,status,business_intent,turn_action,
                    execution_mode,iteration,max_iterations,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request.run_id, request.tenant_id, request.owner_user_id, request.conversation_id,
                    RunStatus.RUNNING.value, request.business_intent.value, request.turn_action.value,
                    request.execution_mode.value, 0, request.max_iterations, now, now,
                ),
            )
        return self.get(RunPointer(
            run_id=request.run_id, tenant_id=request.tenant_id, owner_user_id=request.owner_user_id,
        ))

    def finish(self, result: WorkflowRunResult, pointer: RunPointer) -> RunRecord:
        self.get(pointer)
        now = _now()
        with self._connect() as db:
            db.execute(
                """UPDATE workflow_runs SET status=?,iteration=?,graph_path_json=?,quality_issues_json=?,
                requires_human_review=?,checkpoint_thread_id=?,error_code=?,approval_decision=?,
                approval_comment=?,updated_at=?
                WHERE id=? AND tenant_id=? AND owner_user_id=?""",
                (
                    result.status.value, result.iteration,
                    json.dumps(result.graph_path, ensure_ascii=False),
                    json.dumps(result.quality_issues, ensure_ascii=False),
                    int(result.requires_human_review), result.checkpoint_thread_id,
                    result.error_code,
                    None if result.approval_decision is None else int(result.approval_decision),
                    result.approval_comment,
                    now, pointer.run_id, pointer.tenant_id, pointer.owner_user_id,
                ),
            )
        return self.get(pointer)

    def fail(
        self,
        pointer: RunPointer,
        *,
        error_code: str,
        graph_path: list[str] | None = None,
        quality_issues: list[str] | None = None,
        checkpoint_thread_id: str = "",
    ) -> RunRecord:
        """Move an interrupted Run out of ``running`` without inventing a result."""
        current = self.get(pointer)
        now = _now()
        resolved_path = graph_path if graph_path is not None else current.graph_path
        resolved_issues = quality_issues or ["execution_failed"]
        with self._connect() as db:
            db.execute(
                """UPDATE workflow_runs SET status=?,graph_path_json=?,quality_issues_json=?,
                checkpoint_thread_id=?,error_code=?,updated_at=?
                WHERE id=? AND tenant_id=? AND owner_user_id=?""",
                (
                    RunStatus.FAILED.value,
                    json.dumps(resolved_path, ensure_ascii=False),
                    json.dumps(resolved_issues, ensure_ascii=False),
                    checkpoint_thread_id or current.checkpoint_thread_id,
                    error_code[:120],
                    now,
                    pointer.run_id,
                    pointer.tenant_id,
                    pointer.owner_user_id,
                ),
            )
        return self.get(pointer)

    def bind_message(self, pointer: RunPointer, message_id: str) -> RunRecord:
        """Attach a completed/paused Run to the assistant message that presents its result."""
        self.get(pointer)
        with self._connect() as db:
            updated = db.execute(
                """UPDATE workflow_runs SET message_id=?,updated_at=?
                WHERE id=? AND tenant_id=? AND owner_user_id=?""",
                (message_id, _now(), pointer.run_id, pointer.tenant_id, pointer.owner_user_id),
            ).rowcount
        if not updated:
            raise KeyError(pointer.run_id)
        return self.get(pointer)

    def append_events(
        self,
        pointer: RunPointer,
        events: list[dict[str, Any]],
    ) -> list[RunEventRecord]:
        self.get(pointer)
        created: list[RunEventRecord] = []
        now = _now()
        with self._connect() as db:
            current = db.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM workflow_run_events WHERE run_id=?",
                (pointer.run_id,),
            ).fetchone()[0]
            for offset, item in enumerate(events, start=1):
                sequence = int(current) + offset
                details = {
                    key: value for key, value in item.items()
                    if key not in {"event", "status", "summary", "occurred_at"}
                }
                record = RunEventRecord(
                    id=f"runevt_{uuid4().hex}", run_id=pointer.run_id, sequence=sequence,
                    event=str(item.get("event") or "runtime.event"),
                    status=str(item.get("status") or "completed"),
                    summary=str(item.get("summary") or ""), details=details, created_at=now,
                )
                db.execute(
                    """INSERT INTO workflow_run_events(
                    id,run_id,sequence,event,status,summary,details_json,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        record.id, record.run_id, record.sequence, record.event, record.status,
                        record.summary, json.dumps(record.details, ensure_ascii=False), record.created_at,
                    ),
                )
                created.append(record)
        return created

    def get(self, pointer: RunPointer) -> RunRecord:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_runs WHERE id=? AND tenant_id=? AND owner_user_id=?",
                (pointer.run_id, pointer.tenant_id, pointer.owner_user_id),
            ).fetchone()
        if row is None:
            raise KeyError(pointer.run_id)
        return self._record(row)

    def get_for_tenant(self, run_id: str, tenant_id: str) -> RunRecord:
        """Resolve the real owner only for a permission-checked tenant approver."""

        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_runs WHERE id=? AND tenant_id=?",
                (run_id, tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._record(row)

    def list_waiting_review(self, tenant_id: str, limit: int = 100) -> list[ApprovalQueueItem]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT r.*, c.title AS conversation_title, u.username AS owner_username,
                          u.display_name AS owner_display_name
                FROM workflow_runs r
                JOIN conversations c ON c.id=r.conversation_id
                JOIN users u ON u.id=r.owner_user_id
                WHERE r.tenant_id=? AND r.status=?
                ORDER BY r.updated_at DESC LIMIT ?""",
                (tenant_id, RunStatus.WAITING_REVIEW.value, max(1, min(limit, 500))),
            ).fetchall()
        return [ApprovalQueueItem(
            **self._record(row).model_dump(),
            conversation_title=row["conversation_title"],
            owner_username=row["owner_username"],
            owner_display_name=row["owner_display_name"],
        ) for row in rows]

    def list_recent_for_tenant(
        self,
        tenant_id: str,
        *,
        since: str,
        limit: int = 2_000,
    ) -> list[RunRecord]:
        """Read the tenant's recent run ledger for operational aggregation."""

        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM workflow_runs
                WHERE tenant_id=? AND created_at>=?
                ORDER BY created_at DESC LIMIT ?""",
                (tenant_id, since, max(1, min(limit, 10_000))),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_events_for_runs(
        self,
        run_ids: list[str],
    ) -> dict[str, list[RunEventRecord]]:
        """Batch-read trace events so dashboards do not open one query per run."""

        unique_ids = list(dict.fromkeys(item for item in run_ids if item))
        if not unique_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_ids)
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT * FROM workflow_run_events
                WHERE run_id IN ({placeholders}) ORDER BY run_id,sequence""",
                unique_ids,
            ).fetchall()
        result: dict[str, list[RunEventRecord]] = {run_id: [] for run_id in unique_ids}
        for row in rows:
            event = self._event(row)
            result.setdefault(event.run_id, []).append(event)
        return result

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        tenant_id: str,
        owner_user_id: str,
    ) -> list[RunRecord]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM workflow_runs
                WHERE conversation_id=? AND tenant_id=? AND owner_user_id=? ORDER BY created_at""",
                (conversation_id, tenant_id, owner_user_id),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_events(self, pointer: RunPointer) -> list[RunEventRecord]:
        self.get(pointer)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM workflow_run_events WHERE run_id=? ORDER BY sequence",
                (pointer.run_id,),
            ).fetchall()
        return [self._event(row) for row in rows]

    def skill_node_operations(self, tenant_id: str) -> SkillNodeOperations:
        """Aggregate real Skill node traces; never invent success counters."""

        with self._connect() as db:
            rows = db.execute(
                """SELECT e.event,e.details_json FROM workflow_run_events e
                JOIN workflow_runs r ON r.id=e.run_id
                WHERE r.tenant_id=? AND e.event LIKE 'skill.graph.%'""",
                (tenant_id,),
            ).fetchall()
            run_count = int(db.execute(
                """SELECT COUNT(DISTINCT e.run_id) FROM workflow_run_events e
                JOIN workflow_runs r ON r.id=e.run_id
                WHERE r.tenant_id=? AND e.event='skill.graph.started'""",
                (tenant_id,),
            ).fetchone()[0])
        counts: dict[str, int] = {}
        durations: list[float] = []
        total_cost = 0.0
        for row in rows:
            event = str(row["event"])
            counts[event] = counts.get(event, 0) + 1
            details = json.loads(row["details_json"] or "{}")
            if event == "skill.graph.node.completed":
                if isinstance(details.get("duration_ms"), (int, float)):
                    durations.append(float(details["duration_ms"]))
                if isinstance(details.get("estimated_cost_cny"), (int, float)):
                    total_cost += float(details["estimated_cost_cny"])
        completed = counts.get("skill.graph.node.completed", 0)
        reused = counts.get("skill.graph.node.reused", 0)
        recoveries = counts.get("skill.graph.recovery.started", 0)
        recovered = counts.get("skill.graph.recovery.completed", 0)
        return SkillNodeOperations(
            run_count=run_count,
            node_started=counts.get("skill.graph.node.started", 0),
            node_completed=completed,
            node_failed=counts.get("skill.graph.node.failed", 0),
            node_waiting_input=counts.get("skill.graph.node.waiting_input", 0),
            node_reused=reused,
            recovery_started=recoveries,
            recovery_completed=recovered,
            retry_success_rate=(recovered / recoveries if recoveries else None),
            reuse_rate=(reused / (completed + reused) if completed + reused else None),
            average_duration_ms=(sum(durations) / len(durations) if durations else None),
            total_estimated_cost_cny=total_cost,
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"], tenant_id=row["tenant_id"], owner_user_id=row["owner_user_id"],
            conversation_id=row["conversation_id"], message_id=row["message_id"], status=row["status"],
            business_intent=row["business_intent"], turn_action=row["turn_action"],
            execution_mode=row["execution_mode"], iteration=row["iteration"],
            max_iterations=row["max_iterations"], graph_path=json.loads(row["graph_path_json"]),
            quality_issues=json.loads(row["quality_issues_json"]),
            requires_human_review=bool(row["requires_human_review"]),
            checkpoint_thread_id=row["checkpoint_thread_id"], error_code=row["error_code"],
            approval_decision=(
                None if row["approval_decision"] is None else bool(row["approval_decision"])
            ),
            approval_comment=row["approval_comment"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _event(row: sqlite3.Row) -> RunEventRecord:
        return RunEventRecord(
            id=row["id"], run_id=row["run_id"], sequence=row["sequence"], event=row["event"],
            status=row["status"], summary=row["summary"], details=json.loads(row["details_json"]),
            created_at=row["created_at"],
        )
