from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .contracts import (
    EvalCase,
    EvalCaseResult,
    EvalRun,
    EvalSuite,
    EvaluationSnapshot,
    FeedbackCandidate,
    QualityQueueJob,
    QualityQueueStatus,
    QualityStorageStatus,
    ReleaseDecision,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QualityRepository:
    """Versioned quality evidence store; business conversations never live here."""

    def __init__(
        self,
        database_path: Path,
        *,
        database_dsn: str = "",
        allow_sqlite_fallback: bool = True,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_dsn = database_dsn.strip()
        self.backend = "postgresql" if self.database_dsn else "sqlite"
        self.fallback_reason = ""
        try:
            self._ensure_schema()
        except Exception as exc:
            if self.backend != "postgresql" or not allow_sqlite_fallback:
                raise
            self.backend = "sqlite"
            self.fallback_reason = f"{type(exc).__name__}:{exc}"[:500]
            self._ensure_schema()

    def storage_status(self) -> QualityStorageStatus:
        return QualityStorageStatus(
            backend=self.backend,
            production_ready=self.backend == "postgresql",
            fallback_reason=self.fallback_reason,
        )

    def save_suite(self, suite: EvalSuite) -> EvalSuite:
        created_at = suite.created_at or _now()
        value = suite.model_copy(update={"created_at": created_at})
        with self._connect() as db:
            db.execute(
                """INSERT INTO quality_eval_suites
                (id,tenant_id,version,name,description,domain,frozen,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (value.id, value.tenant_id, value.version, value.name, value.description,
                 value.domain, int(value.frozen), value.created_at),
            )
        return value

    def save_case(self, case: EvalCase) -> EvalCase:
        created_at = case.created_at or _now()
        value = case.model_copy(update={"created_at": created_at})
        with self._connect() as db:
            db.execute(
                """INSERT INTO quality_eval_cases
                (id,tenant_id,suite_id,suite_version,version,title,prompt,task_mode,
                 active_context,has_active_result,attachment_fixtures_json,expectation_json,
                 criticality,tags_json,source,enabled,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (value.id, value.tenant_id, value.suite_id, value.suite_version, value.version,
                 value.title, value.prompt, value.task_mode, value.active_context,
                 int(value.has_active_result), _json(value.attachment_fixtures),
                 _json(value.expectation.model_dump(mode="json")), value.criticality,
                 _json(value.tags), value.source, int(value.enabled), value.created_at),
            )
        return value

    def create_suite_version(self, suite_id: str, tenant_id: str) -> EvalSuite:
        current = self.get_suite(suite_id, tenant_id)
        cases = self.list_cases(suite_id, tenant_id, current.version)
        next_version = current.version + 1
        created = self.save_suite(current.model_copy(update={
            "version": next_version, "frozen": False, "case_ids": [], "created_at": "",
        }))
        for case in cases:
            self.save_case(case.model_copy(update={
                "suite_version": next_version, "version": case.version + 1, "created_at": "",
            }))
        return self.get_suite(created.id, tenant_id, created.version)

    def list_suites(self, tenant_id: str) -> list[EvalSuite]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT s.* FROM quality_eval_suites s JOIN (
                SELECT id,MAX(version) version FROM quality_eval_suites
                WHERE tenant_id=? GROUP BY id) latest
                ON latest.id=s.id AND latest.version=s.version
                WHERE s.tenant_id=? ORDER BY s.name""", (tenant_id, tenant_id),
            ).fetchall()
        return [self._suite(row) for row in rows]

    def get_suite(self, suite_id: str, tenant_id: str, version: int | None = None) -> EvalSuite:
        query = "SELECT * FROM quality_eval_suites WHERE id=? AND tenant_id=?"
        values: list[object] = [suite_id, tenant_id]
        if version is None:
            query += " ORDER BY version DESC LIMIT 1"
        else:
            query += " AND version=?"
            values.append(version)
        with self._connect() as db:
            row = db.execute(query, values).fetchone()
        if row is None:
            raise KeyError(suite_id)
        return self._suite(row)

    def list_cases(self, suite_id: str, tenant_id: str, suite_version: int) -> list[EvalCase]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM quality_eval_cases WHERE tenant_id=? AND suite_id=?
                AND suite_version=? AND enabled=1 ORDER BY id""",
                (tenant_id, suite_id, suite_version),
            ).fetchall()
        return [self._case(row) for row in rows]

    def start_run(
        self, *, tenant_id: str, suite: EvalSuite, candidate_label: str,
        baseline_run_id: str | None, snapshot: EvaluationSnapshot, case_count: int,
    ) -> EvalRun:
        run = EvalRun(
            id=f"evalrun_{uuid4().hex}", tenant_id=tenant_id, suite_id=suite.id,
            suite_version=suite.version, candidate_label=candidate_label,
            baseline_run_id=baseline_run_id, status="queued", snapshot=snapshot,
            case_count=case_count, started_at=_now(),
        )
        with self._connect() as db:
            db.execute(
                """INSERT INTO quality_eval_runs
                (id,tenant_id,suite_id,suite_version,candidate_label,baseline_run_id,status,
                 snapshot_json,case_count,passed_count,started_at,completed_at,error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run.id, run.tenant_id, run.suite_id, run.suite_version,
                 run.candidate_label, run.baseline_run_id, run.status,
                 _json(run.snapshot.model_dump(mode="json")), run.case_count,
                 run.passed_count, run.started_at, None, ""),
            )
        return run

    def enqueue_run(self, run: EvalRun, principal: dict[str, object]) -> QualityQueueJob:
        now = _now()
        job = QualityQueueJob(
            id=f"qualityjob_{uuid4().hex}", run_id=run.id, tenant_id=run.tenant_id,
            status="queued", available_at=now, principal=principal,
            created_at=now, updated_at=now,
        )
        with self._connect() as db:
            db.execute(
                """INSERT INTO quality_eval_jobs
                (id,run_id,tenant_id,status,attempts,max_attempts,available_at,leased_until,
                 worker_id,principal_json,last_error,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job.id, job.run_id, job.tenant_id, job.status, job.attempts,
                 job.max_attempts, job.available_at, job.leased_until, job.worker_id,
                 _json(job.principal), job.last_error, job.created_at, job.updated_at),
            )
        return job

    def claim_job(
        self,
        worker_id: str,
        *,
        run_id: str | None = None,
        lease_seconds: int = 300,
    ) -> QualityQueueJob | None:
        now = _now()
        leased_until = (
            datetime.now(timezone.utc) + timedelta(seconds=max(30, lease_seconds))
        ).isoformat()
        where = "tenant_id<>'' AND attempts<max_attempts AND available_at<=?"
        values: list[object] = [now]
        if run_id:
            where += " AND run_id=?"
            values.append(run_id)
        where += " AND (status='queued' OR (status='running' AND leased_until<>'' AND leased_until<=?))"
        values.append(now)
        with self._connect() as db:
            row = db.execute(
                f"SELECT * FROM quality_eval_jobs WHERE {where} ORDER BY created_at LIMIT 1",
                values,
            ).fetchone()
            if row is None:
                return None
            result = db.execute(
                """UPDATE quality_eval_jobs SET status='running',attempts=attempts+1,
                leased_until=?,worker_id=?,updated_at=? WHERE id=? AND attempts<max_attempts
                AND (status='queued' OR (status='running' AND leased_until<>'' AND leased_until<=?))""",
                (leased_until, worker_id, now, row["id"], now),
            )
            if result.rowcount == 0:
                return None
            db.execute(
                """UPDATE quality_eval_runs SET status='running'
                WHERE id=? AND tenant_id=? AND status='queued'""",
                (row["run_id"], row["tenant_id"]),
            )
            claimed = db.execute(
                "SELECT * FROM quality_eval_jobs WHERE id=?", (row["id"],)
            ).fetchone()
        return self._job(claimed) if claimed is not None else None

    def complete_job(self, run_id: str, tenant_id: str) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE quality_eval_jobs SET status='completed',leased_until='',
                worker_id='',updated_at=? WHERE run_id=? AND tenant_id=?""",
                (_now(), run_id, tenant_id),
            )

    def fail_job(self, run_id: str, tenant_id: str, error: str) -> None:
        now = datetime.now(timezone.utc)
        with self._connect() as db:
            row = db.execute(
                "SELECT attempts,max_attempts FROM quality_eval_jobs WHERE run_id=? AND tenant_id=?",
                (run_id, tenant_id),
            ).fetchone()
            if row is None:
                return
            exhausted = int(row["attempts"]) >= int(row["max_attempts"])
            next_status = "failed" if exhausted else "queued"
            db.execute(
                """UPDATE quality_eval_jobs SET status=?,available_at=?,leased_until='',
                worker_id='',last_error=?,updated_at=? WHERE run_id=? AND tenant_id=?""",
                (
                    next_status,
                    (now + timedelta(seconds=10)).isoformat(), error[:2000], now.isoformat(),
                    run_id, tenant_id,
                ),
            )
            db.execute(
                """UPDATE quality_eval_runs SET status=?,completed_at=?,error=?
                WHERE id=? AND tenant_id=?""",
                (
                    next_status, now.isoformat() if exhausted else None,
                    error[:2000] if exhausted else "", run_id, tenant_id,
                ),
            )

    def cancel_job(self, run_id: str, tenant_id: str) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE quality_eval_jobs SET status='cancelled',leased_until='',
                worker_id='',updated_at=? WHERE run_id=? AND tenant_id=?
                AND status IN ('queued','running')""",
                (_now(), run_id, tenant_id),
            )

    def queue_status(self, tenant_id: str) -> QualityQueueStatus:
        now = _now()
        with self._connect() as db:
            rows = db.execute(
                """SELECT status,COUNT(*) count FROM quality_eval_jobs
                WHERE tenant_id=? GROUP BY status""", (tenant_id,),
            ).fetchall()
            retryable = db.execute(
                """SELECT COUNT(*) count FROM quality_eval_jobs WHERE tenant_id=?
                AND attempts<max_attempts AND (status='queued' OR
                (status='running' AND leased_until<>'' AND leased_until<=?))""",
                (tenant_id, now),
            ).fetchone()
        counts = {str(row["status"]): int(row["count"] or 0) for row in rows}
        return QualityQueueStatus(
            queued=counts.get("queued", 0), running=counts.get("running", 0),
            retryable=int(retryable["count"] or 0) if retryable else 0,
            failed=counts.get("failed", 0),
        )

    def complete_run(self, run_id: str, tenant_id: str, *, passed_count: int,
                     error: str = "") -> EvalRun:
        status = "failed" if error else "completed"
        with self._connect() as db:
            result = db.execute(
                """UPDATE quality_eval_runs SET status=?,passed_count=?,completed_at=?,error=?
                WHERE id=? AND tenant_id=?""",
                (status, passed_count, _now(), error, run_id, tenant_id),
            )
            if result.rowcount == 0:
                raise KeyError(run_id)
        return self.get_run(run_id, tenant_id)

    def cancel_run(self, run_id: str, tenant_id: str) -> EvalRun:
        with self._connect() as db:
            result = db.execute(
                """UPDATE quality_eval_runs SET status='cancelled',completed_at=?
                WHERE id=? AND tenant_id=? AND status IN ('queued','running')""",
                (_now(), run_id, tenant_id),
            )
            if result.rowcount == 0:
                current = self.get_run(run_id, tenant_id)
                if current.status not in {"completed", "failed", "cancelled"}:
                    raise KeyError(run_id)
        self.cancel_job(run_id, tenant_id)
        return self.get_run(run_id, tenant_id)

    def is_cancelled(self, run_id: str, tenant_id: str) -> bool:
        return self.get_run(run_id, tenant_id).status == "cancelled"

    def save_result(self, result: EvalCaseResult) -> EvalCaseResult:
        value = result.model_copy(update={"created_at": result.created_at or _now()})
        with self._connect() as db:
            # A leased job may be retried after a worker crash. Replace only this
            # run/case evidence so retries are idempotent while old runs stay immutable.
            db.execute(
                "DELETE FROM quality_eval_results WHERE run_id=? AND case_id=? AND tenant_id=?",
                (value.run_id, value.case_id, value.tenant_id),
            )
            db.execute(
                """INSERT INTO quality_eval_results
                (id,run_id,case_id,tenant_id,passed,criticality,assertions_json,failures_json,
                 root_failure_json,observation_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (value.id, value.run_id, value.case_id, value.tenant_id, int(value.passed),
                 value.criticality, _json([item.model_dump(mode="json") for item in value.assertions]),
                 _json([item.model_dump(mode="json") for item in value.failures]),
                 _json(value.root_failure.model_dump(mode="json") if value.root_failure else None),
                 _json(value.observation.model_dump(mode="json")), value.created_at),
            )
        return value

    def get_run(self, run_id: str, tenant_id: str) -> EvalRun:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM quality_eval_runs WHERE id=? AND tenant_id=?",
                (run_id, tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run(row)

    def list_runs(self, tenant_id: str, limit: int = 50) -> list[EvalRun]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM quality_eval_runs WHERE tenant_id=?
                ORDER BY started_at DESC LIMIT ?""", (tenant_id, max(1, min(limit, 200))),
            ).fetchall()
        return [self._run(row) for row in rows]

    def has_active_run(self, tenant_id: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                """SELECT 1 FROM quality_eval_runs WHERE tenant_id=?
                AND status IN ('queued','running') LIMIT 1""",
                (tenant_id,),
            ).fetchone()
        return row is not None

    def list_results(self, run_id: str, tenant_id: str) -> list[EvalCaseResult]:
        self.get_run(run_id, tenant_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM quality_eval_results WHERE run_id=? AND tenant_id=? ORDER BY case_id",
                (run_id, tenant_id),
            ).fetchall()
        return [self._result(row) for row in rows]

    def save_decision(self, decision: ReleaseDecision, tenant_id: str) -> ReleaseDecision:
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO quality_release_decisions
                (run_id,tenant_id,status,payload_json,decided_at) VALUES (?,?,?,?,?)""",
                (decision.run_id, tenant_id, decision.status,
                 _json(decision.model_dump(mode="json")), decision.decided_at),
            )
        return decision

    def create_feedback_candidate(
        self, *, tenant_id: str, feedback_id: str, source_message_id: str,
        scrubbed_prompt: str, expected_correction: str,
    ) -> FeedbackCandidate:
        candidate = FeedbackCandidate(
            id=f"feedbackcase_{uuid4().hex}", tenant_id=tenant_id,
            feedback_id=feedback_id, source_message_id=source_message_id,
            scrubbed_prompt=scrubbed_prompt, expected_correction=expected_correction,
            created_at=_now(),
        )
        with self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO quality_feedback_candidates
                (id,tenant_id,feedback_id,source_message_id,scrubbed_prompt,
                 expected_correction,status,created_at) VALUES (?,?,?,?,?,?,?,?)""",
                (candidate.id, candidate.tenant_id, candidate.feedback_id,
                 candidate.source_message_id, candidate.scrubbed_prompt,
                 candidate.expected_correction, candidate.status, candidate.created_at),
            )
            row = db.execute(
                "SELECT * FROM quality_feedback_candidates WHERE tenant_id=? AND feedback_id=?",
                (tenant_id, feedback_id),
            ).fetchone()
        assert row is not None
        return FeedbackCandidate(**dict(row))

    def list_feedback_candidates(self, tenant_id: str) -> list[FeedbackCandidate]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM quality_feedback_candidates WHERE tenant_id=?
                ORDER BY created_at DESC""", (tenant_id,),
            ).fetchall()
        return [FeedbackCandidate(**dict(row)) for row in rows]

    def get_feedback_candidate(self, candidate_id: str, tenant_id: str) -> FeedbackCandidate:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM quality_feedback_candidates WHERE id=? AND tenant_id=?",
                (candidate_id, tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return FeedbackCandidate(**dict(row))

    def review_feedback_candidate(
        self, candidate_id: str, tenant_id: str, status: str,
    ) -> FeedbackCandidate:
        if status not in {"accepted", "rejected"}:
            raise ValueError("invalid_feedback_candidate_status")
        with self._connect() as db:
            result = db.execute(
                """UPDATE quality_feedback_candidates SET status=?
                WHERE id=? AND tenant_id=?""", (status, candidate_id, tenant_id),
            )
            if result.rowcount == 0:
                raise KeyError(candidate_id)
        return self.get_feedback_candidate(candidate_id, tenant_id)

    def _ensure_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS quality_eval_suites (
                    id TEXT NOT NULL, tenant_id TEXT NOT NULL, version INTEGER NOT NULL,
                    name TEXT NOT NULL, description TEXT NOT NULL, domain TEXT NOT NULL,
                    frozen INTEGER NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (id,tenant_id,version));
                CREATE TABLE IF NOT EXISTS quality_eval_cases (
                    id TEXT NOT NULL, tenant_id TEXT NOT NULL, suite_id TEXT NOT NULL,
                    suite_version INTEGER NOT NULL, version INTEGER NOT NULL,
                    title TEXT NOT NULL, prompt TEXT NOT NULL, task_mode TEXT NOT NULL,
                    active_context TEXT NOT NULL, has_active_result INTEGER NOT NULL,
                    attachment_fixtures_json TEXT NOT NULL, expectation_json TEXT NOT NULL,
                    criticality TEXT NOT NULL, tags_json TEXT NOT NULL, source TEXT NOT NULL,
                    enabled INTEGER NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (id,tenant_id,suite_version,version));
                CREATE TABLE IF NOT EXISTS quality_eval_runs (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, suite_id TEXT NOT NULL,
                    suite_version INTEGER NOT NULL, candidate_label TEXT NOT NULL,
                    baseline_run_id TEXT, status TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                    case_count INTEGER NOT NULL, passed_count INTEGER NOT NULL,
                    started_at TEXT NOT NULL, completed_at TEXT, error TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS quality_eval_results (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, case_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL, passed INTEGER NOT NULL, criticality TEXT NOT NULL,
                    assertions_json TEXT NOT NULL, failures_json TEXT NOT NULL,
                    root_failure_json TEXT, observation_json TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS quality_release_decisions (
                    run_id TEXT NOT NULL, tenant_id TEXT NOT NULL, status TEXT NOT NULL,
                    payload_json TEXT NOT NULL, decided_at TEXT NOT NULL,
                    PRIMARY KEY (run_id,tenant_id));
                CREATE TABLE IF NOT EXISTS quality_feedback_candidates (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, feedback_id TEXT NOT NULL,
                    source_message_id TEXT NOT NULL, scrubbed_prompt TEXT NOT NULL,
                    expected_correction TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE (tenant_id,feedback_id));
                CREATE TABLE IF NOT EXISTS quality_eval_jobs (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL, status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    available_at TEXT NOT NULL, leased_until TEXT NOT NULL DEFAULT '',
                    worker_id TEXT NOT NULL DEFAULT '', principal_json TEXT NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_quality_runs_tenant_started
                    ON quality_eval_runs(tenant_id,started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_quality_results_run ON quality_eval_results(run_id);
                CREATE INDEX IF NOT EXISTS idx_quality_jobs_claim
                    ON quality_eval_jobs(status,available_at,leased_until);
                """
            )

    def _connect(self) -> Any:
        if self.backend == "postgresql":
            return _PostgresConnection(self.database_dsn)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _suite(self, row: Any) -> EvalSuite:
        with self._connect() as db:
            ids = [item["id"] for item in db.execute(
                """SELECT id FROM quality_eval_cases WHERE tenant_id=? AND suite_id=?
                AND suite_version=? AND enabled=1 ORDER BY id""",
                (row["tenant_id"], row["id"], row["version"]),
            ).fetchall()]
        payload = dict(row)
        payload["frozen"] = bool(row["frozen"])
        payload["case_ids"] = ids
        return EvalSuite(**payload)

    @staticmethod
    def _case(row: Any) -> EvalCase:
        return EvalCase(
            id=row["id"], tenant_id=row["tenant_id"], suite_id=row["suite_id"],
            suite_version=row["suite_version"], version=row["version"], title=row["title"],
            prompt=row["prompt"], task_mode=row["task_mode"],
            active_context=row["active_context"], has_active_result=bool(row["has_active_result"]),
            attachment_fixtures=json.loads(row["attachment_fixtures_json"]),
            expectation=json.loads(row["expectation_json"]), criticality=row["criticality"],
            tags=json.loads(row["tags_json"]), source=row["source"],
            enabled=bool(row["enabled"]), created_at=row["created_at"],
        )

    @staticmethod
    def _run(row: Any) -> EvalRun:
        return EvalRun(
            id=row["id"], tenant_id=row["tenant_id"], suite_id=row["suite_id"],
            suite_version=row["suite_version"], candidate_label=row["candidate_label"],
            baseline_run_id=row["baseline_run_id"], status=row["status"],
            snapshot=json.loads(row["snapshot_json"]), case_count=row["case_count"],
            passed_count=row["passed_count"], started_at=row["started_at"],
            completed_at=row["completed_at"], error=row["error"],
        )

    @staticmethod
    def _result(row: Any) -> EvalCaseResult:
        return EvalCaseResult(
            id=row["id"], run_id=row["run_id"], case_id=row["case_id"],
            tenant_id=row["tenant_id"], passed=bool(row["passed"]),
            criticality=row["criticality"], assertions=json.loads(row["assertions_json"]),
            failures=json.loads(row["failures_json"]),
            root_failure=json.loads(row["root_failure_json"]) if row["root_failure_json"] else None,
            observation=json.loads(row["observation_json"]), created_at=row["created_at"],
        )

    @staticmethod
    def _job(row: Any) -> QualityQueueJob:
        return QualityQueueJob(
            id=row["id"], run_id=row["run_id"], tenant_id=row["tenant_id"],
            status=row["status"], attempts=row["attempts"], max_attempts=row["max_attempts"],
            available_at=row["available_at"], leased_until=row["leased_until"],
            worker_id=row["worker_id"], principal=json.loads(row["principal_json"]),
            last_error=row["last_error"], created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class _PostgresConnection:
    """Small qmark compatibility layer so the repository contract stays database-neutral."""

    def __init__(self, dsn: str) -> None:
        self.connection = psycopg.connect(dsn, row_factory=dict_row)

    def __enter__(self) -> "_PostgresConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.connection.close()

    def execute(self, sql: str, parameters: object = ()):
        statement = sql.replace("?", "%s")
        if "INSERT OR REPLACE INTO quality_release_decisions" in statement:
            statement = statement.replace("INSERT OR REPLACE INTO", "INSERT INTO")
            statement += (
                " ON CONFLICT (run_id,tenant_id) DO UPDATE SET "
                "status=EXCLUDED.status,payload_json=EXCLUDED.payload_json,"
                "decided_at=EXCLUDED.decided_at"
            )
        elif "INSERT OR IGNORE INTO quality_feedback_candidates" in statement:
            statement = statement.replace("INSERT OR IGNORE INTO", "INSERT INTO")
            statement += " ON CONFLICT (tenant_id,feedback_id) DO NOTHING"
        return self.connection.execute(statement, parameters)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self.connection.execute(statement)
