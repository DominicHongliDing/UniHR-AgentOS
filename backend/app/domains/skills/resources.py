from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from app.domains.identity import Principal

from .contracts import SkillRecord, SkillResourceKind, SkillResourceRecord


_SAFE_NAME = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,180}$")
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_FILES_PER_SKILL = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SkillResourceStore:
    """Tenant-scoped storage for Skill resources; uploaded scripts are never executed."""

    def __init__(self, database_path: str | Path, storage_root: str | Path) -> None:
        self.database_path = Path(database_path)
        self.storage_root = Path(storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS skill_resources (
                    id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scripts_executed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_skill_resources_skill ON skill_resources(skill_id, created_at)")

    def add(
        self,
        skill: SkillRecord,
        principal: Principal | None,
        *,
        kind: SkillResourceKind,
        filename: str,
        content: bytes,
        mime_type: str = "application/octet-stream",
    ) -> SkillResourceRecord:
        self._assert_writable(skill, principal)
        name = Path(filename).name
        if name != filename or not _SAFE_NAME.fullmatch(name):
            raise ValueError("skill_resource_unsafe_name")
        if not content:
            raise ValueError("skill_resource_empty")
        if len(content) > _MAX_FILE_BYTES:
            raise ValueError("skill_resource_limit_10mb")
        with self._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM skill_resources WHERE skill_id=?", (skill.id,)).fetchone()[0]
            if count >= _MAX_FILES_PER_SKILL:
                raise ValueError("skill_resource_limit_50_files")

        resource_id = f"skillres_{uuid4().hex}"
        digest = hashlib.sha256(content).hexdigest()
        folder = self.storage_root / skill.tenant_id / skill.owner_user_id / skill.id / kind
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{resource_id}-{name}"
        target.write_bytes(content)
        status = "quarantined" if kind == "scripts" else "ready"
        created_at = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO skill_resources(
                    id,skill_id,tenant_id,owner_user_id,kind,name,mime_type,size_bytes,
                    sha256,storage_path,status,scripts_executed,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    resource_id, skill.id, skill.tenant_id, skill.owner_user_id, kind, name,
                    mime_type[:160], len(content), digest, str(target), status, 0, created_at,
                ),
            )
        return SkillResourceRecord(
            id=resource_id, skill_id=skill.id, tenant_id=skill.tenant_id,
            owner_user_id=skill.owner_user_id, kind=kind, name=name,
            mime_type=mime_type[:160], size_bytes=len(content), sha256=digest,
            status=status, scripts_executed=False, created_at=created_at,
        )

    def list(self, skill: SkillRecord, principal: Principal | None) -> list[SkillResourceRecord]:
        self._assert_readable(skill, principal)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM skill_resources WHERE skill_id=? ORDER BY created_at DESC", (skill.id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def read(self, skill: SkillRecord, resource_id: str, principal: Principal | None) -> tuple[SkillResourceRecord, bytes]:
        self._assert_readable(skill, principal)
        row = self._get_row(skill.id, resource_id)
        path = Path(row["storage_path"])
        if not path.is_file():
            raise KeyError(resource_id)
        return self._record(row), path.read_bytes()

    def resolve_path(
        self, skill: SkillRecord, resource_id: str, principal: Principal | None,
    ) -> tuple[SkillResourceRecord, Path]:
        """Return a trusted local path for an already-authorized runtime resolver."""
        self._assert_readable(skill, principal)
        row = self._get_row(skill.id, resource_id)
        path = Path(row["storage_path"])
        if not path.is_file():
            raise KeyError(resource_id)
        return self._record(row), path

    def delete(self, skill: SkillRecord, resource_id: str, principal: Principal | None) -> None:
        self._assert_writable(skill, principal)
        row = self._get_row(skill.id, resource_id)
        path = Path(row["storage_path"])
        with self._connect() as db:
            db.execute("DELETE FROM skill_resources WHERE id=? AND skill_id=?", (resource_id, skill.id))
        if path.is_file():
            path.unlink()

    def delete_all(self, skill: SkillRecord, principal: Principal | None) -> None:
        for resource in self.list(skill, principal):
            self.delete(skill, resource.id, principal)

    def _get_row(self, skill_id: str, resource_id: str) -> sqlite3.Row:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM skill_resources WHERE id=? AND skill_id=?", (resource_id, skill_id),
            ).fetchone()
        if row is None:
            raise KeyError(resource_id)
        return row

    @staticmethod
    def _assert_readable(skill: SkillRecord, principal: Principal | None) -> None:
        if principal is not None and skill.tenant_id != principal.tenant_id:
            raise PermissionError("skill_resource_access_denied")
        if (
            principal is not None
            and skill.source_type != "system"
            and skill.owner_user_id not in {principal.user_id, "*"}
        ):
            raise PermissionError("skill_resource_access_denied")

    @staticmethod
    def _assert_writable(skill: SkillRecord, principal: Principal | None) -> None:
        if skill.source_type == "system":
            raise ValueError("system_skill_is_read_only")
        SkillResourceStore._assert_readable(skill, principal)
        if skill.owner_user_id == "*" and (principal is None or principal.role != "admin"):
            raise PermissionError("organization_skill_admin_only")

    @staticmethod
    def _record(row: sqlite3.Row) -> SkillResourceRecord:
        return SkillResourceRecord(
            id=row["id"], skill_id=row["skill_id"], tenant_id=row["tenant_id"],
            owner_user_id=row["owner_user_id"], kind=row["kind"], name=row["name"],
            mime_type=row["mime_type"], size_bytes=int(row["size_bytes"]), sha256=row["sha256"],
            status=row["status"], scripts_executed=bool(row["scripts_executed"]), created_at=row["created_at"],
        )
