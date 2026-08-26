from __future__ import annotations

import sqlite3
from pathlib import Path

import psycopg

from .repository import QualityRepository


_TABLES = (
    "quality_eval_suites",
    "quality_eval_cases",
    "quality_eval_runs",
    "quality_eval_results",
    "quality_release_decisions",
    "quality_feedback_candidates",
    "quality_eval_jobs",
)


def migrate_sqlite_quality_evidence(
    sqlite_path: str | Path,
    postgres_dsn: str,
) -> dict[str, int]:
    """Idempotently copy quality-only evidence; business tables are never touched."""
    source_path = Path(sqlite_path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    # Ensure the destination schema uses the same repository contract first.
    QualityRepository(
        source_path, database_dsn=postgres_dsn, allow_sqlite_fallback=False,
    )
    copied: dict[str, int] = {}
    with sqlite3.connect(source_path) as source, psycopg.connect(postgres_dsn) as target:
        source.row_factory = sqlite3.Row
        available = {
            str(row["name"]) for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in _TABLES:
            if table not in available:
                copied[table] = 0
                continue
            columns = [
                str(row["name"]) for row in source.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            ]
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                copied[table] = 0
                continue
            column_sql = ",".join(columns)
            placeholders = ",".join("%s" for _ in columns)
            statement = (
                f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) "
                "ON CONFLICT DO NOTHING"
            )
            inserted = 0
            for row in rows:
                cursor = target.execute(statement, tuple(row[column] for column in columns))
                inserted += max(0, int(cursor.rowcount or 0))
            copied[table] = inserted
    return copied

