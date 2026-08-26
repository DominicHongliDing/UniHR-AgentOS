from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from pydantic import BaseModel

from .contracts import OperationResult, RunPointer


class RunPayloadReference(BaseModel):
    schema_version: str = "run_payload_ref_v1"
    payload_id: str
    kind: str
    version: int
    content_hash: str
    byte_size: int


class RunPayloadStore:
    """Persist large Graph node results while checkpoints retain only references."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_payloads (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(tenant_id, owner_user_id, run_id, kind, version)
                );
                CREATE INDEX IF NOT EXISTS idx_run_payloads_scope
                    ON run_payloads(tenant_id, owner_user_id, run_id, kind, version DESC);
                """
            )

    def put(
        self,
        pointer: RunPointer,
        payload: OperationResult,
        *,
        kind: str = "operation_result",
    ) -> RunPayloadReference:
        return self.put_json(pointer, payload.model_dump(mode="json"), kind=kind)

    def put_json(
        self,
        pointer: RunPointer,
        payload: Any,
        *,
        kind: str,
    ) -> RunPayloadReference:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        encoded = serialized.encode("utf-8")
        content_hash = hashlib.sha256(encoded).hexdigest()
        with self._connect() as db:
            existing = db.execute(
                """SELECT * FROM run_payloads WHERE tenant_id=? AND owner_user_id=?
                AND run_id=? AND kind=? AND content_hash=? ORDER BY version DESC LIMIT 1""",
                (pointer.tenant_id, pointer.owner_user_id, pointer.run_id, kind, content_hash),
            ).fetchone()
            if existing is not None:
                return self._reference(existing)
            version = int(db.execute(
                """SELECT COALESCE(MAX(version),0)+1 AS version FROM run_payloads
                WHERE tenant_id=? AND owner_user_id=? AND run_id=? AND kind=?""",
                (pointer.tenant_id, pointer.owner_user_id, pointer.run_id, kind),
            ).fetchone()["version"])
            payload_id = f"runpayload_{uuid4().hex}"
            db.execute(
                """INSERT INTO run_payloads(
                id,tenant_id,owner_user_id,run_id,kind,version,content_hash,byte_size,payload_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload_id, pointer.tenant_id, pointer.owner_user_id, pointer.run_id,
                    kind, version, content_hash, len(encoded), serialized,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            row = db.execute("SELECT * FROM run_payloads WHERE id=?", (payload_id,)).fetchone()
        return self._reference(row)

    def get(self, pointer: RunPointer, reference: RunPayloadReference | dict[str, Any]) -> OperationResult:
        return OperationResult.model_validate(self.get_json(pointer, reference))

    def get_json(
        self,
        pointer: RunPointer,
        reference: RunPayloadReference | dict[str, Any],
    ) -> Any:
        ref = RunPayloadReference.model_validate(reference)
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM run_payloads WHERE id=? AND tenant_id=?
                AND owner_user_id=? AND run_id=? AND kind=?""",
                (
                    ref.payload_id, pointer.tenant_id, pointer.owner_user_id,
                    pointer.run_id, ref.kind,
                ),
            ).fetchone()
        if row is None:
            raise KeyError(ref.payload_id)
        serialized = str(row["payload_json"])
        if hashlib.sha256(serialized.encode("utf-8")).hexdigest() != ref.content_hash:
            raise ValueError("run_payload_hash_mismatch")
        return json.loads(serialized)

    def delete(self, pointer: RunPointer) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM run_payloads WHERE tenant_id=? AND owner_user_id=? AND run_id=?",
                (pointer.tenant_id, pointer.owner_user_id, pointer.run_id),
            )

    @staticmethod
    def _reference(row: sqlite3.Row) -> RunPayloadReference:
        return RunPayloadReference(
            payload_id=row["id"], kind=row["kind"], version=int(row["version"]),
            content_hash=row["content_hash"], byte_size=int(row["byte_size"]),
        )
