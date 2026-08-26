from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterator
from uuid import uuid4

from pydantic import BaseModel, Field

from app.domains.identity import Principal
from app.models.usage_store import ModelUsageStore, model_usage_scope
from app.domains.workbench import TurnCancelledError, UploadPayload, WorkbenchService
from app.domains.workbench.contracts import WorkbenchTurnResponse


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TurnExecutionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_EXECUTION_STATUSES = {
    TurnExecutionStatus.SUCCEEDED,
    TurnExecutionStatus.FAILED,
    TurnExecutionStatus.CANCELLED,
}


class TurnExecutionRecord(BaseModel):
    id: str
    tenant_id: str
    owner_user_id: str
    status: TurnExecutionStatus
    cancel_requested: bool = False
    conversation_id: str | None = None
    run_id: str | None = None
    result: WorkbenchTurnResponse | None = None
    error_code: str | None = None
    created_at: str
    updated_at: str


class TurnExecutionEvent(BaseModel):
    id: str
    execution_id: str
    sequence: int
    event: str
    status: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class TurnExecutionRepository:
    """Persist async turn lifecycle, cursors, and owner-scoped observable events."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self._write_lock = Lock()
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
                CREATE TABLE IF NOT EXISTS turn_executions (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    conversation_id TEXT,
                    run_id TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_turn_executions_owner
                    ON turn_executions(tenant_id, owner_user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS turn_execution_events (
                    id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL REFERENCES turn_executions(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(execution_id, sequence)
                );
                """
            )
            # A process restart cannot resume an in-memory pre-Run call. Make that boundary explicit.
            now = _now()
            db.execute(
                """UPDATE turn_executions SET status=?,error_code=?,updated_at=?
                WHERE status IN (?,?)""",
                (
                    TurnExecutionStatus.FAILED.value,
                    "process_restarted_before_completion",
                    now,
                    TurnExecutionStatus.QUEUED.value,
                    TurnExecutionStatus.RUNNING.value,
                ),
            )

    def create(self, principal: Principal) -> TurnExecutionRecord:
        execution_id = f"turnexec_{uuid4().hex}"
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO turn_executions(
                id,tenant_id,owner_user_id,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    execution_id,
                    principal.tenant_id,
                    principal.user_id,
                    TurnExecutionStatus.QUEUED.value,
                    now,
                    now,
                ),
            )
        return self.get(execution_id, principal)

    def get(self, execution_id: str, principal: Principal) -> TurnExecutionRecord:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM turn_executions
                WHERE id=? AND tenant_id=? AND owner_user_id=?""",
                (execution_id, principal.tenant_id, principal.user_id),
            ).fetchone()
        if row is None:
            raise KeyError(execution_id)
        return self._record(row)

    def set_running(self, execution_id: str, principal: Principal) -> None:
        self._update(execution_id, principal, status=TurnExecutionStatus.RUNNING)

    def complete(
        self, execution_id: str, principal: Principal, result: WorkbenchTurnResponse
    ) -> None:
        self._update(
            execution_id,
            principal,
            status=TurnExecutionStatus.SUCCEEDED,
            conversation_id=result.conversation.id,
            run_id=result.run.id,
            result_json=result.model_dump_json(),
        )

    def fail(self, execution_id: str, principal: Principal, error_code: str) -> None:
        self._update(
            execution_id,
            principal,
            status=TurnExecutionStatus.FAILED,
            error_code=error_code[:160],
        )

    def mark_cancelled(self, execution_id: str, principal: Principal) -> None:
        self._update(
            execution_id,
            principal,
            status=TurnExecutionStatus.CANCELLED,
            cancel_requested=True,
            error_code="turn_cancelled",
        )

    def request_cancel(self, execution_id: str, principal: Principal) -> TurnExecutionRecord:
        current = self.get(execution_id, principal)
        if current.status in TERMINAL_EXECUTION_STATUSES:
            return current
        self._update(execution_id, principal, cancel_requested=True)
        return self.get(execution_id, principal)

    def is_cancel_requested(self, execution_id: str, principal: Principal) -> bool:
        return self.get(execution_id, principal).cancel_requested

    def append_event(
        self,
        execution_id: str,
        principal: Principal,
        event: str,
        summary: str,
        *,
        status: str = "running",
        details: dict[str, Any] | None = None,
    ) -> TurnExecutionEvent:
        self.get(execution_id, principal)
        event_details = details or {}
        with self._write_lock, self._connect() as db:
            conversation_id = str(event_details.get("conversation_id") or "").strip()
            if conversation_id:
                db.execute(
                    """UPDATE turn_executions SET conversation_id=?,updated_at=?
                    WHERE id=? AND tenant_id=? AND owner_user_id=? AND conversation_id IS NULL""",
                    (conversation_id, _now(), execution_id, principal.tenant_id, principal.user_id),
                )
            sequence = int(db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM turn_execution_events WHERE execution_id=?",
                (execution_id,),
            ).fetchone()[0])
            record = TurnExecutionEvent(
                id=f"turnevt_{uuid4().hex}",
                execution_id=execution_id,
                sequence=sequence,
                event=event,
                status=status,
                summary=summary,
                details=event_details,
                created_at=_now(),
            )
            db.execute(
                """INSERT INTO turn_execution_events(
                id,execution_id,sequence,event,status,summary,details_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    record.id,
                    execution_id,
                    sequence,
                    event,
                    status,
                    summary,
                    json.dumps(record.details, ensure_ascii=False),
                    record.created_at,
                ),
            )
        return record

    def list_for_conversation(
        self, conversation_id: str, *, tenant_id: str, owner_user_id: str
    ) -> list[TurnExecutionRecord]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM turn_executions
                WHERE conversation_id=? AND tenant_id=? AND owner_user_id=?
                ORDER BY updated_at DESC""",
                (conversation_id, tenant_id, owner_user_id),
            ).fetchall()
        return [self._record(row) for row in rows]

    def delete_for_conversation(
        self, conversation_id: str, *, tenant_id: str, owner_user_id: str
    ) -> int:
        with self._connect() as db:
            count = int(db.execute(
                """SELECT COUNT(*) FROM turn_executions
                WHERE conversation_id=? AND tenant_id=? AND owner_user_id=?""",
                (conversation_id, tenant_id, owner_user_id),
            ).fetchone()[0])
            db.execute(
                """DELETE FROM turn_executions
                WHERE conversation_id=? AND tenant_id=? AND owner_user_id=?""",
                (conversation_id, tenant_id, owner_user_id),
            )
        return count

    def list_events(
        self, execution_id: str, principal: Principal, *, after: int = 0
    ) -> list[TurnExecutionEvent]:
        self.get(execution_id, principal)
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM turn_execution_events
                WHERE execution_id=? AND sequence>? ORDER BY sequence""",
                (execution_id, max(0, after)),
            ).fetchall()
        return [self._event(row) for row in rows]

    def _update(
        self,
        execution_id: str,
        principal: Principal,
        *,
        status: TurnExecutionStatus | None = None,
        cancel_requested: bool | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        result_json: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self.get(execution_id, principal)
        fields = ["updated_at=?"]
        values: list[Any] = [_now()]
        for column, value in (
            ("status", status.value if status is not None else None),
            ("cancel_requested", int(cancel_requested) if cancel_requested is not None else None),
            ("conversation_id", conversation_id),
            ("run_id", run_id),
            ("result_json", result_json),
            ("error_code", error_code),
        ):
            if value is not None:
                fields.append(f"{column}=?")
                values.append(value)
        values.extend([execution_id, principal.tenant_id, principal.user_id])
        with self._connect() as db:
            db.execute(
                f"UPDATE turn_executions SET {','.join(fields)} WHERE id=? AND tenant_id=? AND owner_user_id=?",
                values,
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> TurnExecutionRecord:
        result = WorkbenchTurnResponse.model_validate_json(row["result_json"]) if row["result_json"] else None
        return TurnExecutionRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            owner_user_id=row["owner_user_id"],
            status=row["status"],
            cancel_requested=bool(row["cancel_requested"]),
            conversation_id=row["conversation_id"],
            run_id=row["run_id"],
            result=result,
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _event(row: sqlite3.Row) -> TurnExecutionEvent:
        return TurnExecutionEvent(
            id=row["id"],
            execution_id=row["execution_id"],
            sequence=row["sequence"],
            event=row["event"],
            status=row["status"],
            summary=row["summary"],
            details=json.loads(row["details_json"]),
            created_at=row["created_at"],
        )


class TurnExecutionModule:
    """Deep async boundary: start, observe, recover, and safely cancel a workbench turn."""

    def __init__(
        self,
        repository: TurnExecutionRepository,
        workbench: WorkbenchService,
        *,
        max_workers: int = 2,
    ) -> None:
        self.repository = repository
        self.workbench = workbench
        self.usage_store = ModelUsageStore(workbench.settings)
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="unihr-turn")

    def start(self, *, principal: Principal, **turn: Any) -> TurnExecutionRecord:
        record = self.repository.create(principal)
        self.repository.append_event(
            record.id,
            principal,
            "turn.accepted",
            "请求已接收，正在进入执行队列",
            status="queued",
        )
        self.executor.submit(self._run, record.id, principal.model_copy(deep=True), turn)
        return record

    def steer(
        self,
        execution_id: str,
        *,
        principal: Principal,
        **turn: Any,
    ) -> TurnExecutionRecord:
        """Supersede an active turn and re-plan from its safe conversation boundary."""

        predecessor = self.repository.get(execution_id, principal)
        if predecessor.status not in TERMINAL_EXECUTION_STATUSES:
            self.repository.request_cancel(execution_id, principal)
            self.repository.append_event(
                execution_id,
                principal,
                "turn.steering.requested",
                "已收到新的优先指令；当前执行将在安全边界停止",
                status="cancelling",
            )
        replacement = self.repository.create(principal)
        self.repository.append_event(
            replacement.id,
            principal,
            "turn.steering.queued",
            "新的优先指令已进入队列，等待上一轮安全停止后重新规划",
            status="queued",
            details={"supersedes_execution_id": execution_id},
        )
        self.executor.submit(
            self._run_after_predecessor,
            replacement.id,
            execution_id,
            principal.model_copy(deep=True),
            turn,
        )
        return replacement

    def get(self, execution_id: str, principal: Principal) -> TurnExecutionRecord:
        return self.repository.get(execution_id, principal)

    def events(
        self, execution_id: str, principal: Principal, *, after: int = 0
    ) -> list[TurnExecutionEvent]:
        return self.repository.list_events(execution_id, principal, after=after)

    def cancel(self, execution_id: str, principal: Principal) -> TurnExecutionRecord:
        current = self.repository.request_cancel(execution_id, principal)
        if current.status not in TERMINAL_EXECUTION_STATUSES:
            self.repository.append_event(
                execution_id,
                principal,
                "turn.cancel.requested",
                "已收到取消请求，将在安全边界停止并不发布新结果",
                status="cancelling",
            )
        return self.repository.get(execution_id, principal)

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _run_after_predecessor(
        self,
        execution_id: str,
        predecessor_id: str,
        principal: Principal,
        turn: dict[str, Any],
    ) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            predecessor = self.repository.get(predecessor_id, principal)
            if predecessor.status in TERMINAL_EXECUTION_STATUSES:
                break
            time.sleep(0.1)
        else:
            self.repository.fail(execution_id, principal, "steering_predecessor_timeout")
            self.repository.append_event(
                execution_id,
                principal,
                "turn.steering.failed",
                "上一轮未能在安全时间内停止，新的指令没有执行",
                status="failed",
                details={"supersedes_execution_id": predecessor_id},
            )
            return

        if predecessor.conversation_id:
            # The newest user instruction continues the same conversation even
            # when the predecessor was cancelled before publishing a result.
            turn["conversation_id"] = predecessor.conversation_id
        self.repository.append_event(
            execution_id,
            principal,
            "turn.steering.predecessor_stopped",
            "上一轮已在安全边界停止，未发布旧计划的结果",
            status="completed",
            details={
                "supersedes_execution_id": predecessor_id,
                "predecessor_status": predecessor.status.value,
                "predecessor_run_id": predecessor.run_id,
            },
        )
        self.repository.append_event(
            execution_id,
            principal,
            "turn.steering.applied",
            "上一轮已安全停止，正在按最新指令重新规划",
            status="active",
            details={
                "supersedes_execution_id": predecessor_id,
                "conversation_id": predecessor.conversation_id,
            },
        )
        self._run(execution_id, principal, turn)

    def _run(self, execution_id: str, principal: Principal, turn: dict[str, Any]) -> None:
        def progress(event: str, summary: str, details: dict[str, Any]) -> None:
            event_status = str(details.get("event_status") or "running")
            safe_details = {key: value for key, value in details.items() if key != "event_status"}
            self.repository.append_event(
                execution_id,
                principal,
                event,
                summary,
                status=event_status,
                details=safe_details,
            )

        try:
            if self.repository.is_cancel_requested(execution_id, principal):
                raise TurnCancelledError("turn_cancelled")
            self.repository.set_running(execution_id, principal)
            self.repository.append_event(
                execution_id, principal, "turn.running", "正在处理本轮任务"
            )
            with model_usage_scope(principal.tenant_id, principal.user_id, execution_id):
                result = self.workbench.handle_turn(
                    **turn,
                    principal=principal,
                    progress=progress,
                    cancel_requested=lambda: self.repository.is_cancel_requested(execution_id, principal),
                )
            self.usage_store.bind_execution(
                principal.tenant_id,
                execution_id,
                conversation_id=result.conversation.id,
                workflow_run_id=result.run.id,
            )
            if self.repository.is_cancel_requested(execution_id, principal):
                raise TurnCancelledError("turn_cancelled")
            self.repository.complete(execution_id, principal, result)
            self.repository.append_event(
                execution_id,
                principal,
                "turn.completed",
                "本轮任务已完成",
                status="completed",
                details={"conversation_id": result.conversation.id, "run_id": result.run.id},
            )
        except TurnCancelledError:
            self.repository.mark_cancelled(execution_id, principal)
            self.repository.append_event(
                execution_id,
                principal,
                "turn.cancelled",
                "本轮任务已取消，未发布新的结果",
                status="cancelled",
            )
        except Exception as exc:
            self.repository.fail(execution_id, principal, type(exc).__name__)
            self.repository.append_event(
                execution_id,
                principal,
                "turn.failed",
                "本轮任务执行失败，已保留输入与审计记录",
                status="failed",
                details={"error_code": type(exc).__name__},
            )
