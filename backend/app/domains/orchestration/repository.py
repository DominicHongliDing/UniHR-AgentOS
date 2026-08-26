from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.domains.identity import Principal, RUN_APPROVE

from .contracts import (
    WorkflowCase,
    WorkflowCaseStatus,
    WorkflowHandoff,
    WorkflowPlan,
    WorkflowStep,
    WorkflowStepStatus,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowCaseRepository:
    """Durable state boundary for a complete, multi-Agent business objective."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=30)
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
                CREATE TABLE IF NOT EXISTS workflow_cases (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    rationale TEXT NOT NULL DEFAULT '',
                    planner_source TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    current_step_id TEXT NOT NULL DEFAULT '',
                    recruitment_case_id TEXT NOT NULL DEFAULT '',
                    steps_json TEXT NOT NULL DEFAULT '[]',
                    handoffs_json TEXT NOT NULL DEFAULT '[]',
                    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    decision_log_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_cases_owner
                    ON workflow_cases(tenant_id, owner_user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_workflow_cases_conversation
                    ON workflow_cases(conversation_id, updated_at DESC);
                """
            )

    def create(self, plan: WorkflowPlan, conversation_id: str, principal: Principal) -> WorkflowCase:
        case_id = f"workflow_case_{uuid4().hex}"
        now = _now()
        steps = [item.model_copy(deep=True) for item in plan.steps]
        steps[0].status = WorkflowStepStatus.RUNNING
        steps[0].started_at = now
        with self._connect() as db:
            db.execute(
                """INSERT INTO workflow_cases(
                id,tenant_id,owner_user_id,conversation_id,title,objective,rationale,
                planner_source,status,current_step_id,steps_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    case_id, principal.tenant_id, principal.user_id, conversation_id,
                    plan.title, plan.objective, plan.rationale, plan.source,
                    WorkflowCaseStatus.RUNNING.value, steps[0].id,
                    json.dumps([item.model_dump(mode="json") for item in steps], ensure_ascii=False),
                    now, now,
                ),
            )
        return self.get(case_id, principal)

    def get(self, case_id: str, principal: Principal) -> WorkflowCase:
        with self._connect() as db:
            row = db.execute("SELECT * FROM workflow_cases WHERE id=?", (case_id,)).fetchone()
        if row is None or not self._can_access(row, principal):
            raise KeyError("workflow_case_not_found")
        return self._record(row)

    def list_for_conversation(self, conversation_id: str, principal: Principal) -> list[WorkflowCase]:
        conditions = ["conversation_id=?", "tenant_id=?"]
        values: list[str] = [conversation_id, principal.tenant_id]
        if not principal.can(RUN_APPROVE):
            conditions.append("owner_user_id=?")
            values.append(principal.user_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM workflow_cases WHERE " + " AND ".join(conditions)
                + " ORDER BY updated_at DESC", values,
            ).fetchall()
        return [self._record(row) for row in rows]

    def save(self, item: WorkflowCase, principal: Principal) -> WorkflowCase:
        current = self.get(item.id, principal)
        if current.owner_user_id != principal.user_id and not principal.can(RUN_APPROVE):
            raise KeyError("workflow_case_not_found")
        updated_at = _now()
        with self._connect() as db:
            db.execute(
                """UPDATE workflow_cases SET title=?,objective=?,rationale=?,planner_source=?,
                status=?,current_step_id=?,recruitment_case_id=?,steps_json=?,handoffs_json=?,
                artifact_ids_json=?,decision_log_json=?,updated_at=? WHERE id=?""",
                (
                    item.title, item.objective, item.rationale, item.planner_source,
                    item.status.value, item.current_step_id, item.recruitment_case_id,
                    json.dumps([value.model_dump(mode="json") for value in item.steps], ensure_ascii=False),
                    json.dumps([value.model_dump(mode="json") for value in item.handoffs], ensure_ascii=False),
                    json.dumps(item.artifact_ids, ensure_ascii=False),
                    json.dumps(item.decision_log, ensure_ascii=False), updated_at, item.id,
                ),
            )
        return self.get(item.id, principal)

    @staticmethod
    def _can_access(row: sqlite3.Row, principal: Principal) -> bool:
        return row["tenant_id"] == principal.tenant_id and (
            row["owner_user_id"] == principal.user_id or principal.can(RUN_APPROVE)
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> WorkflowCase:
        return WorkflowCase(
            id=row["id"], tenant_id=row["tenant_id"], owner_user_id=row["owner_user_id"],
            conversation_id=row["conversation_id"], title=row["title"], objective=row["objective"],
            rationale=row["rationale"], planner_source=row["planner_source"], status=row["status"],
            current_step_id=row["current_step_id"], recruitment_case_id=row["recruitment_case_id"],
            steps=[WorkflowStep.model_validate(item) for item in json.loads(row["steps_json"] or "[]")],
            handoffs=[WorkflowHandoff.model_validate(item) for item in json.loads(row["handoffs_json"] or "[]")],
            artifact_ids=json.loads(row["artifact_ids_json"] or "[]"),
            decision_log=json.loads(row["decision_log_json"] or "[]"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

