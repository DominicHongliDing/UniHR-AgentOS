from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class KnowledgeIngestionJob:
    job_id: str
    tenant_id: str
    actor_id: str
    status: str
    total_files: int
    processed_files: int
    current_file: str
    current_stage: str
    result: dict[str, object]
    error: str
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


class KnowledgeIngestionJobRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        with self._connect() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_ingestion_jobs (
                job_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                status TEXT NOT NULL,
                total_files INTEGER NOT NULL DEFAULT 0,
                processed_files INTEGER NOT NULL DEFAULT 0,
                current_file TEXT NOT NULL DEFAULT '',
                current_stage TEXT NOT NULL DEFAULT 'queued',
                result_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_ingestion_jobs_tenant
            ON knowledge_ingestion_jobs(tenant_id,created_at);
            """)

    def create(self, *, tenant_id: str, actor_id: str) -> KnowledgeIngestionJob:
        job_id = f"kjob_{uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO knowledge_ingestion_jobs(job_id,tenant_id,actor_id,status,created_at,updated_at) VALUES(?,?,?,'queued',?,?)",
                (job_id, tenant_id, actor_id, now, now),
            )
        return self.get(job_id, tenant_id=tenant_id)

    def progress(
        self, job_id: str, *, status: str = "running", total: int | None = None,
        processed: int | None = None, current_file: str = "", stage: str = "",
    ) -> None:
        assignments = ["status=?", "updated_at=?"]
        values: list[object] = [status, datetime.now(timezone.utc).isoformat()]
        for column, value in (("total_files", total), ("processed_files", processed),
                              ("current_file", current_file or None), ("current_stage", stage or None)):
            if value is not None:
                assignments.append(f"{column}=?")
                values.append(value)
        values.append(job_id)
        with self._connect() as connection:
            connection.execute(f"UPDATE knowledge_ingestion_jobs SET {','.join(assignments)} WHERE job_id=?", values)

    def complete(self, job_id: str, result: dict[str, object]) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE knowledge_ingestion_jobs SET status='completed',current_stage='completed',
                current_file='',result_json=?,updated_at=? WHERE job_id=?""",
                (json.dumps(result, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE knowledge_ingestion_jobs SET status='failed',current_stage='failed',
                error=?,updated_at=? WHERE job_id=?""",
                (error[:1000], datetime.now(timezone.utc).isoformat(), job_id),
            )

    def get(self, job_id: str, *, tenant_id: str) -> KnowledgeIngestionJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_ingestion_jobs WHERE job_id=? AND tenant_id=?", (job_id, tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError("knowledge_ingestion_job_not_found")
        return self._record(row)

    def latest(self, *, tenant_id: str) -> KnowledgeIngestionJob | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_ingestion_jobs WHERE tenant_id=? ORDER BY created_at DESC LIMIT 1",
                (tenant_id,),
            ).fetchone()
        return self._record(row) if row else None

    @staticmethod
    def _record(row: tuple[object, ...]) -> KnowledgeIngestionJob:
        return KnowledgeIngestionJob(
            job_id=str(row[0]), tenant_id=str(row[1]), actor_id=str(row[2]), status=str(row[3]),
            total_files=int(row[4]), processed_files=int(row[5]), current_file=str(row[6] or ""),
            current_stage=str(row[7]), result=json.loads(str(row[8] or "{}")), error=str(row[9] or ""),
            created_at=str(row[10]), updated_at=str(row[11]),
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)
