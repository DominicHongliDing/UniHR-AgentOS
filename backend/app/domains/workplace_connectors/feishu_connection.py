from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from app.domains.identity import Principal
from app.settings import Settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TokenCipher(Protocol):
    def encrypt(self, value: str) -> str: ...
    def decrypt(self, value: str) -> str: ...


class FernetTokenCipher:
    """Lazy Fernet wrapper so an unconfigured demo never handles plaintext tokens."""

    def __init__(self, key: str) -> None:
        self.key = key.strip()

    def _fernet(self):
        if not self.key:
            raise RuntimeError("connector_token_encryption_key_missing")
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise RuntimeError("connector_token_encryption_dependency_missing") from exc
        return Fernet(self.key.encode("ascii"))

    def encrypt(self, value: str) -> str:
        return self._fernet().encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        return self._fernet().decrypt(value.encode("ascii")).decode("utf-8")


class FeishuConnectionStatus(BaseModel):
    provider: str = "feishu"
    configured: bool
    connected: bool
    state: str
    display_name: str | None = None
    external_user_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expires_at: str | None = None
    last_error: str | None = None
    missing_configuration: list[str] = Field(default_factory=list)
    adapter_ready: bool = False
    capabilities: list["FeishuCapabilityStatus"] = Field(default_factory=list)


class FeishuCapabilityStatus(BaseModel):
    capability: str
    label: str
    mode: str
    implemented: bool = True
    available: bool = False
    availability: str = "connection_required"


class FeishuAuthorizationStart(BaseModel):
    authorization_url: str
    expires_at: str


class FeishuConnectionRecord(BaseModel):
    tenant_id: str
    owner_user_id: str
    access_token_ciphertext: str
    refresh_token_ciphertext: str = ""
    scopes: list[str] = Field(default_factory=list)
    expires_at: str
    refresh_expires_at: str | None = None
    display_name: str | None = None
    external_user_id: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str


class FeishuConnectionRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=30)
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
                CREATE TABLE IF NOT EXISTS external_connections (
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    access_token_ciphertext TEXT NOT NULL,
                    refresh_token_ciphertext TEXT NOT NULL DEFAULT '',
                    scopes_json TEXT NOT NULL DEFAULT '[]',
                    expires_at TEXT NOT NULL,
                    refresh_expires_at TEXT,
                    display_name TEXT,
                    external_user_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, owner_user_id, provider)
                );
                CREATE TABLE IF NOT EXISTS external_oauth_states (
                    state_hash TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    code_verifier TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def save_state(
        self,
        *,
        state: str,
        principal: Principal,
        code_verifier: str,
        expires_at: str,
    ) -> None:
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute(
                """INSERT INTO external_oauth_states(
                state_hash,tenant_id,owner_user_id,provider,code_verifier,expires_at,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    state_hash,
                    principal.tenant_id,
                    principal.user_id,
                    "feishu",
                    code_verifier,
                    expires_at,
                    _now().isoformat(),
                ),
            )

    def consume_state(self, state: str, principal: Principal) -> str:
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM external_oauth_states
                WHERE state_hash=? AND tenant_id=? AND owner_user_id=? AND provider='feishu'""",
                (state_hash, principal.tenant_id, principal.user_id),
            ).fetchone()
            if row is None:
                raise ValueError("oauth_state_invalid")
            db.execute("DELETE FROM external_oauth_states WHERE state_hash=?", (state_hash,))
        if datetime.fromisoformat(row["expires_at"]) <= _now():
            raise ValueError("oauth_state_expired")
        return str(row["code_verifier"])

    def get(self, principal: Principal) -> FeishuConnectionRecord | None:
        return self.get_for_owner(principal.tenant_id, principal.user_id)

    def get_for_owner(
        self, tenant_id: str, owner_user_id: str
    ) -> FeishuConnectionRecord | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM external_connections
                WHERE tenant_id=? AND owner_user_id=? AND provider='feishu'""",
                (tenant_id, owner_user_id),
            ).fetchone()
        if row is None:
            return None
        return FeishuConnectionRecord(
            tenant_id=row["tenant_id"],
            owner_user_id=row["owner_user_id"],
            access_token_ciphertext=row["access_token_ciphertext"],
            refresh_token_ciphertext=row["refresh_token_ciphertext"],
            scopes=json.loads(row["scopes_json"]),
            expires_at=row["expires_at"],
            refresh_expires_at=row["refresh_expires_at"],
            display_name=row["display_name"],
            external_user_id=row["external_user_id"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def upsert(self, record: FeishuConnectionRecord) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO external_connections(
                tenant_id,owner_user_id,provider,access_token_ciphertext,
                refresh_token_ciphertext,scopes_json,expires_at,refresh_expires_at,
                display_name,external_user_id,last_error,created_at,updated_at
                ) VALUES(?,?,'feishu',?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id,owner_user_id,provider) DO UPDATE SET
                    access_token_ciphertext=excluded.access_token_ciphertext,
                    refresh_token_ciphertext=excluded.refresh_token_ciphertext,
                    scopes_json=excluded.scopes_json,
                    expires_at=excluded.expires_at,
                    refresh_expires_at=excluded.refresh_expires_at,
                    display_name=excluded.display_name,
                    external_user_id=excluded.external_user_id,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at""",
                (
                    record.tenant_id,
                    record.owner_user_id,
                    record.access_token_ciphertext,
                    record.refresh_token_ciphertext,
                    json.dumps(record.scopes, ensure_ascii=False),
                    record.expires_at,
                    record.refresh_expires_at,
                    record.display_name,
                    record.external_user_id,
                    record.last_error,
                    record.created_at,
                    record.updated_at,
                ),
            )

    def delete(self, principal: Principal) -> None:
        with self._connect() as db:
            db.execute(
                """DELETE FROM external_connections
                WHERE tenant_id=? AND owner_user_id=? AND provider='feishu'""",
                (principal.tenant_id, principal.user_id),
            )


class FeishuConnectionModule:
    AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
    USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
    CAPABILITIES = (
        ("calendar.read_availability", "读取日历忙闲", "read"),
        ("directory.get_people", "读取指定人员", "read"),
        ("directory.list_departments", "读取组织部门", "read"),
        ("workspace.search_documents", "搜索云文档", "read"),
        ("calendar.create_event", "创建日程", "write"),
        ("messaging.send_text", "发送消息", "write"),
        ("bitable.create_record", "写入多维表格", "write"),
        ("drive.upload_artifact", "上传结果文件", "write"),
    )

    def __init__(
        self,
        settings: Settings,
        repository: FeishuConnectionRepository,
        *,
        cipher: TokenCipher | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.cipher = cipher or FernetTokenCipher(settings.connector_token_encryption_key)
        self.client = client or httpx.Client(timeout=20)

    def status(self, principal: Principal) -> FeishuConnectionStatus:
        missing = self._missing_configuration()
        record = self.repository.get(principal)
        reauthorization_required = self._reauthorization_required(record)
        connected = record is not None and not reauthorization_required
        state = (
            "not_configured"
            if missing
            else "reauthorization_required"
            if reauthorization_required
            else "connected"
            if connected
            else "not_connected"
        )
        return FeishuConnectionStatus(
            configured=not missing,
            connected=connected,
            state=state,
            display_name=record.display_name if record else None,
            external_user_id=record.external_user_id if record else None,
            scopes=record.scopes if record else self._scopes(),
            expires_at=record.expires_at if record else None,
            last_error=record.last_error if record else None,
            missing_configuration=missing,
            adapter_ready=not missing,
            capabilities=[
                FeishuCapabilityStatus(
                    capability=capability,
                    label=label,
                    mode=mode,
                    available=connected,
                    availability=(
                        "ready"
                        if connected
                        else "reauthorization_required"
                        if reauthorization_required
                        else "not_configured"
                        if missing
                        else "connection_required"
                    ),
                )
                for capability, label, mode in self.CAPABILITIES
            ],
        )

    def start(self, principal: Principal) -> FeishuAuthorizationStart:
        missing = self._missing_configuration()
        if missing:
            raise RuntimeError("feishu_not_configured:" + ",".join(missing))
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        expires_at = (_now() + timedelta(minutes=10)).isoformat()
        self.repository.save_state(
            state=state,
            principal=principal,
            code_verifier=verifier,
            expires_at=expires_at,
        )
        query = urlencode(
            {
                "client_id": self.settings.feishu_app_id,
                "response_type": "code",
                "redirect_uri": self.settings.feishu_oauth_redirect_uri,
                "scope": " ".join(self._scopes()),
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return FeishuAuthorizationStart(
            authorization_url=f"{self.AUTHORIZE_URL}?{query}",
            expires_at=expires_at,
        )

    def complete(self, principal: Principal, *, state: str, code: str) -> FeishuConnectionStatus:
        verifier = self.repository.consume_state(state, principal)
        response = self.client.post(
            self.TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": self.settings.feishu_app_id,
                "client_secret": self.settings.feishu_app_secret,
                "code": code,
                "redirect_uri": self.settings.feishu_oauth_redirect_uri,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("code") or 0) != 0 or not payload.get("access_token"):
            raise RuntimeError(str(payload.get("msg") or "feishu_oauth_exchange_failed"))
        access_token = str(payload["access_token"])
        user_info_response = self.client.get(
            self.USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_info_response.raise_for_status()
        user_payload: dict[str, Any] = user_info_response.json()
        if int(user_payload.get("code") or 0) != 0:
            raise RuntimeError(str(user_payload.get("msg") or "feishu_user_info_failed"))
        now = _now()
        expires_in = max(1, int(payload.get("expires_in") or 1))
        refresh_expires_in = int(payload.get("refresh_token_expires_in") or 0)
        existing = self.repository.get(principal)
        record = FeishuConnectionRecord(
            tenant_id=principal.tenant_id,
            owner_user_id=principal.user_id,
            access_token_ciphertext=self.cipher.encrypt(access_token),
            refresh_token_ciphertext=(
                self.cipher.encrypt(str(payload["refresh_token"]))
                if payload.get("refresh_token")
                else ""
            ),
            scopes=str(payload.get("scope") or "").split(),
            expires_at=(now + timedelta(seconds=expires_in)).isoformat(),
            refresh_expires_at=(
                (now + timedelta(seconds=refresh_expires_in)).isoformat()
                if refresh_expires_in
                else None
            ),
            display_name=str(user_payload.get("name") or "") or None,
            external_user_id=str(user_payload.get("open_id") or "") or None,
            created_at=existing.created_at if existing else now.isoformat(),
            updated_at=now.isoformat(),
        )
        self.repository.upsert(record)
        return self.status(principal)

    def disconnect(self, principal: Principal) -> FeishuConnectionStatus:
        self.repository.delete(principal)
        return self.status(principal)

    def access_token_for(self, *, tenant_id: str, owner_user_id: str) -> str:
        """Return a usable user token, refreshing it without exposing it to Agent state."""
        if self._missing_configuration():
            raise RuntimeError("feishu_not_configured")
        record = self.repository.get_for_owner(tenant_id, owner_user_id)
        if record is None:
            raise RuntimeError("feishu_not_connected")
        if datetime.fromisoformat(record.expires_at) > _now() + timedelta(seconds=60):
            return self.cipher.decrypt(record.access_token_ciphertext)
        if self._reauthorization_required(record) or not record.refresh_token_ciphertext:
            raise RuntimeError("feishu_reauthorization_required")

        refresh_token = self.cipher.decrypt(record.refresh_token_ciphertext)
        response = self.client.post(
            self.TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": self.settings.feishu_app_id,
                "client_secret": self.settings.feishu_app_secret,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("code") or 0) != 0 or not payload.get("access_token"):
            raise RuntimeError("feishu_token_refresh_failed")
        now = _now()
        expires_in = max(1, int(payload.get("expires_in") or 1))
        refresh_expires_in = int(payload.get("refresh_token_expires_in") or 0)
        refreshed = record.model_copy(
            update={
                "access_token_ciphertext": self.cipher.encrypt(str(payload["access_token"])),
                "refresh_token_ciphertext": self.cipher.encrypt(
                    str(payload.get("refresh_token") or refresh_token)
                ),
                "scopes": str(payload.get("scope") or "").split() or record.scopes,
                "expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
                "refresh_expires_at": (
                    (now + timedelta(seconds=refresh_expires_in)).isoformat()
                    if refresh_expires_in
                    else record.refresh_expires_at
                ),
                "last_error": None,
                "updated_at": now.isoformat(),
            }
        )
        self.repository.upsert(refreshed)
        return str(payload["access_token"])

    def _missing_configuration(self) -> list[str]:
        missing: list[str] = []
        if not self.settings.feishu_app_id:
            missing.append("app_id")
        if not self.settings.feishu_app_secret:
            missing.append("app_secret")
        if not self.settings.connector_token_encryption_key:
            missing.append("token_encryption_key")
        return missing

    def _scopes(self) -> list[str]:
        return list(dict.fromkeys(self.settings.feishu_oauth_scopes.split()))

    @staticmethod
    def _reauthorization_required(record: FeishuConnectionRecord | None) -> bool:
        if record is None:
            return False
        if datetime.fromisoformat(record.expires_at) > _now():
            return False
        if not record.refresh_token_ciphertext or not record.refresh_expires_at:
            return True
        return datetime.fromisoformat(record.refresh_expires_at) <= _now()
