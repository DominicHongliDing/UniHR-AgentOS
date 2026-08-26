from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app.settings import Settings

from .contracts import (
    ANSWER_HISTORY_MANAGE,
    KNOWLEDGE_MANAGE,
    RUN_APPROVE,
    LoginResult,
    Principal,
    UserAccount,
)
from .security import hash_password, new_session_token, token_digest, verify_password


ADMIN_USER_ID = "usr_unihr_admin"
DEMO_USER_ID = "usr_unihr_demo"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IdentityRepository:
    """Own authentication, accounts, sessions, roles, and tenant membership."""

    def __init__(self, database_path: str | Path, settings: Settings) -> None:
        self.database_path = Path(database_path)
        self.settings = settings
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
                CREATE TABLE IF NOT EXISTS tenants (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','user')),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tenant_memberships (
                    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id, expires_at);
                """
            )
            now = _now().isoformat()
            db.execute(
                "INSERT OR IGNORE INTO tenants(id,name,created_at) VALUES(?,?,?)",
                (self.settings.default_tenant_id, self.settings.default_tenant_name, now),
            )
            self._seed_user(
                db, ADMIN_USER_ID, self.settings.admin_username, "管理员", self.settings.admin_password, "admin", now,
            )
            self._seed_user(
                db, DEMO_USER_ID, self.settings.demo_username, "HR 用户", self.settings.demo_password, "user", now,
            )
            for user_id in (ADMIN_USER_ID, DEMO_USER_ID):
                db.execute(
                    "INSERT OR IGNORE INTO tenant_memberships(tenant_id,user_id,created_at) VALUES(?,?,?)",
                    (self.settings.default_tenant_id, user_id, now),
                )
            db.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))

    @staticmethod
    def _seed_user(
        db: sqlite3.Connection,
        user_id: str,
        username: str,
        display_name: str,
        password: str,
        role: str,
        created_at: str,
    ) -> None:
        if db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is not None:
            return
        db.execute(
            "INSERT INTO users(id,username,display_name,password_hash,role,active,created_at) VALUES(?,?,?,?,?,1,?)",
            (user_id, username.strip(), display_name, hash_password(password), role, created_at),
        )

    def login(self, username: str, password: str) -> LoginResult:
        with self._connect() as db:
            row = db.execute(
                """SELECT u.*, m.tenant_id FROM users u
                JOIN tenant_memberships m ON m.user_id=u.id
                WHERE u.username=? COLLATE NOCASE AND u.active=1
                ORDER BY m.created_at LIMIT 1""",
                (username.strip(),),
            ).fetchone()
            if row is None or not verify_password(password, row["password_hash"]):
                raise ValueError("invalid_credentials")
            token = new_session_token()
            expires_at = _now() + timedelta(days=self.settings.session_expire_days)
            db.execute(
                "INSERT INTO auth_sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
                (token_digest(token), row["id"], expires_at.isoformat(), _now().isoformat()),
            )
        return LoginResult(
            access_token=token,
            expires_at=expires_at.isoformat(),
            user=self._principal(row),
        )

    def authenticate(self, token: str) -> Principal | None:
        now = _now().isoformat()
        with self._connect() as db:
            row = db.execute(
                """SELECT u.*, m.tenant_id FROM auth_sessions s
                JOIN users u ON u.id=s.user_id
                JOIN tenant_memberships m ON m.user_id=u.id
                WHERE s.token_hash=? AND s.expires_at>? AND u.active=1
                ORDER BY m.created_at LIMIT 1""",
                (token_digest(token), now),
            ).fetchone()
        return self._principal(row) if row is not None else None

    def logout(self, token: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_digest(token),))

    def list_users(self, tenant_id: str) -> list[UserAccount]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT u.*, m.tenant_id FROM users u
                JOIN tenant_memberships m ON m.user_id=u.id
                WHERE m.tenant_id=? ORDER BY u.role='admin' DESC, u.display_name""",
                (tenant_id,),
            ).fetchall()
        return [self._account(row) for row in rows]

    def get_principal(self, tenant_id: str, user_id: str) -> Principal | None:
        """Resolve a tenant-scoped user for trusted non-browser channels."""
        with self._connect() as db:
            row = db.execute(
                """SELECT u.*, m.tenant_id FROM users u
                JOIN tenant_memberships m ON m.user_id=u.id
                WHERE m.tenant_id=? AND u.id=? AND u.active=1
                LIMIT 1""",
                (tenant_id, user_id),
            ).fetchone()
        return self._principal(row) if row is not None else None

    @staticmethod
    def _principal(row: sqlite3.Row) -> Principal:
        role = row["role"]
        return Principal(
            user_id=row["id"], username=row["username"], display_name=row["display_name"],
            role=role, tenant_id=row["tenant_id"],
            permissions=[ANSWER_HISTORY_MANAGE, RUN_APPROVE, KNOWLEDGE_MANAGE]
            if role == "admin" else [],
        )

    @staticmethod
    def _account(row: sqlite3.Row) -> UserAccount:
        return UserAccount(
            id=row["id"], username=row["username"], display_name=row["display_name"],
            role=row["role"], tenant_id=row["tenant_id"], active=bool(row["active"]),
            created_at=row["created_at"],
        )
