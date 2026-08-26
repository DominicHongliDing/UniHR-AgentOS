from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .contracts import (
    ChannelBinding,
    ChannelContextPolicy,
    FeishuExternalGroupConfig,
    FeishuExternalGroupView,
    IncomingChannelAction,
    IncomingChannelMessage,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ChannelGatewayRepository:
    """Durable identity, session, event and delivery state for chat channels."""

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
                CREATE TABLE IF NOT EXISTS channel_identity_bindings (
                    provider TEXT NOT NULL,
                    external_tenant_key TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(provider, external_tenant_key, external_user_id)
                );
                CREATE TABLE IF NOT EXISTS channel_sessions (
                    provider TEXT NOT NULL,
                    external_tenant_key TEXT NOT NULL,
                    external_conversation_id TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    internal_conversation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(provider, external_tenant_key, external_conversation_id, external_user_id)
                );
                CREATE TABLE IF NOT EXISTS channel_events (
                    provider TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    external_message_id TEXT,
                    message_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    leased_by TEXT,
                    leased_until TEXT,
                    internal_conversation_id TEXT,
                    internal_run_id TEXT,
                    result_text TEXT,
                    result_payload_json TEXT,
                    external_result_message_id TEXT,
                    progress_message_id TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(provider, event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_channel_events_work
                ON channel_events(status, created_at);
                CREATE TABLE IF NOT EXISTS channel_delivery_parts (
                    provider TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    part_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    external_message_id TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(provider, event_id, part_key)
                );
                CREATE TABLE IF NOT EXISTS channel_actions (
                    provider TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    leased_by TEXT,
                    leased_until TEXT,
                    result_text TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(provider, event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_channel_actions_work
                ON channel_actions(status, created_at);
                CREATE TABLE IF NOT EXISTS channel_context_policies (
                    provider TEXT NOT NULL,
                    external_tenant_key TEXT NOT NULL,
                    external_conversation_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    max_messages INTEGER NOT NULL DEFAULT 12,
                    retention_hours INTEGER NOT NULL DEFAULT 72,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(provider, external_tenant_key, external_conversation_id)
                );
                CREATE TABLE IF NOT EXISTS channel_context_messages (
                    provider TEXT NOT NULL,
                    external_tenant_key TEXT NOT NULL,
                    external_conversation_id TEXT NOT NULL,
                    external_message_id TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(provider, external_message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_channel_context_window
                ON channel_context_messages(
                    provider,external_tenant_key,external_conversation_id,created_at
                );
                CREATE TABLE IF NOT EXISTS feishu_external_groups (
                    group_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    webhook_url TEXT NOT NULL,
                    webhook_secret TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(channel_events)").fetchall()
            }
            if "external_message_id" not in columns:
                db.execute("ALTER TABLE channel_events ADD COLUMN external_message_id TEXT")
            if "progress_message_id" not in columns:
                db.execute("ALTER TABLE channel_events ADD COLUMN progress_message_id TEXT")
            if "result_payload_json" not in columns:
                db.execute("ALTER TABLE channel_events ADD COLUMN result_payload_json TEXT")
            db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_events_message
                ON channel_events(provider, external_message_id)
                WHERE external_message_id IS NOT NULL"""
            )

    def bind(self, binding: ChannelBinding) -> ChannelBinding:
        now = _now().isoformat()
        with self._connect() as db:
            db.execute(
                """INSERT INTO channel_identity_bindings(
                provider, external_tenant_key, external_user_id, tenant_id, user_id,
                active, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(provider, external_tenant_key, external_user_id) DO UPDATE SET
                tenant_id=excluded.tenant_id, user_id=excluded.user_id,
                active=excluded.active, updated_at=excluded.updated_at""",
                (
                    binding.provider, binding.external_tenant_key,
                    binding.external_user_id, binding.tenant_id, binding.user_id,
                    int(binding.active), now, now,
                ),
            )
        return binding

    def resolve_binding(
        self, provider: str, external_tenant_key: str, external_user_id: str
    ) -> ChannelBinding | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM channel_identity_bindings WHERE provider=?
                AND external_tenant_key=? AND external_user_id IN (?, '*') AND active=1
                ORDER BY CASE external_user_id WHEN ? THEN 0 ELSE 1 END LIMIT 1""",
                (provider, external_tenant_key, external_user_id, external_user_id),
            ).fetchone()
        if row is None:
            return None
        values = dict(row)
        values["active"] = bool(values["active"])
        return ChannelBinding.model_validate(values)

    def save_external_group(
        self, config: FeishuExternalGroupConfig,
    ) -> FeishuExternalGroupView:
        now = _now().isoformat()
        with self._connect() as db:
            db.execute(
                """INSERT INTO feishu_external_groups(
                group_id,display_name,owner_user_id,webhook_url,webhook_secret,
                enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(group_id) DO UPDATE SET
                display_name=excluded.display_name,
                owner_user_id=excluded.owner_user_id,
                webhook_url=excluded.webhook_url,
                webhook_secret=excluded.webhook_secret,
                enabled=excluded.enabled,
                updated_at=excluded.updated_at""",
                (
                    config.group_id, config.display_name, config.owner_user_id,
                    config.webhook_url, config.webhook_secret, int(config.enabled),
                    now, now,
                ),
            )
        return self.external_group_view(config.group_id)

    def external_group(self, group_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM feishu_external_groups WHERE group_id=? AND enabled=1",
                (group_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def external_group_view(self, group_id: str) -> FeishuExternalGroupView:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM feishu_external_groups WHERE group_id=?", (group_id,),
            ).fetchone()
        if row is None:
            raise KeyError("external_group_not_found")
        return FeishuExternalGroupView(
            group_id=str(row["group_id"]),
            display_name=str(row["display_name"]),
            owner_user_id=str(row["owner_user_id"]),
            enabled=bool(row["enabled"]),
            webhook_configured=bool(row["webhook_url"]),
            webhook_secret_configured=bool(row["webhook_secret"]),
        )

    def list_external_groups(self) -> list[FeishuExternalGroupView]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT group_id FROM feishu_external_groups ORDER BY updated_at DESC"
            ).fetchall()
        return [self.external_group_view(str(row["group_id"])) for row in rows]

    def save_context_policy(self, policy: ChannelContextPolicy) -> ChannelContextPolicy:
        now = _now().isoformat()
        with self._connect() as db:
            db.execute(
                """INSERT INTO channel_context_policies(
                provider,external_tenant_key,external_conversation_id,enabled,
                max_messages,retention_hours,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(provider,external_tenant_key,external_conversation_id)
                DO UPDATE SET enabled=excluded.enabled,max_messages=excluded.max_messages,
                retention_hours=excluded.retention_hours,updated_at=excluded.updated_at""",
                (
                    policy.provider, policy.external_tenant_key,
                    policy.external_conversation_id, int(policy.enabled),
                    policy.max_messages, policy.retention_hours, now, now,
                ),
            )
        return policy

    def context_policy(
        self, *, provider: str, external_tenant_key: str,
        external_conversation_id: str,
    ) -> ChannelContextPolicy | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM channel_context_policies WHERE provider=?
                AND external_tenant_key=? AND external_conversation_id=?""",
                (provider, external_tenant_key, external_conversation_id),
            ).fetchone()
        if row is None:
            return None
        values = dict(row)
        values["enabled"] = bool(values["enabled"])
        return ChannelContextPolicy.model_validate(values)

    def list_context_policies(self, provider: str = "feishu") -> list[ChannelContextPolicy]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM channel_context_policies WHERE provider=?
                ORDER BY updated_at DESC""",
                (provider,),
            ).fetchall()
        policies: list[ChannelContextPolicy] = []
        for row in rows:
            values = dict(row)
            values["enabled"] = bool(values["enabled"])
            policies.append(ChannelContextPolicy.model_validate(values))
        return policies

    def observe_context_message(
        self, message: IncomingChannelMessage, policy: ChannelContextPolicy,
    ) -> bool:
        if (
            not policy.enabled or message.chat_type != "group"
            or message.attachments or not message.text.strip()
        ):
            return False
        now = _now()
        cutoff = (now - timedelta(hours=policy.retention_hours)).isoformat()
        with self._connect() as db:
            db.execute(
                """DELETE FROM channel_context_messages WHERE provider=?
                AND external_tenant_key=? AND external_conversation_id=?
                AND created_at<?""",
                (
                    message.provider, message.external_tenant_key,
                    message.external_conversation_id, cutoff,
                ),
            )
            cursor = db.execute(
                """INSERT OR IGNORE INTO channel_context_messages(
                provider,external_tenant_key,external_conversation_id,
                external_message_id,external_user_id,text,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    message.provider, message.external_tenant_key,
                    message.external_conversation_id, message.external_message_id,
                    message.external_user_id, message.text[:4000], now.isoformat(),
                ),
            )
            # Keep the context store bounded even in busy groups.  The policy
            # controls both retrieval and storage, not just the final Top-K.
            db.execute(
                """DELETE FROM channel_context_messages WHERE rowid IN (
                    SELECT rowid FROM channel_context_messages WHERE provider=?
                    AND external_tenant_key=? AND external_conversation_id=?
                    ORDER BY created_at DESC LIMIT -1 OFFSET ?
                )""",
                (
                    message.provider, message.external_tenant_key,
                    message.external_conversation_id, policy.max_messages,
                ),
            )
        return cursor.rowcount == 1

    def latest_context_from_sender(
        self, message: IncomingChannelMessage, policy: ChannelContextPolicy,
    ) -> dict[str, str] | None:
        """Return only the invoking member's latest short-lived group message."""
        cutoff = (_now() - timedelta(hours=policy.retention_hours)).isoformat()
        with self._connect() as db:
            row = db.execute(
                """SELECT external_user_id,text,created_at,external_message_id
                FROM channel_context_messages WHERE provider=?
                AND external_tenant_key=? AND external_conversation_id=?
                AND external_user_id=? AND external_message_id<>? AND created_at>=?
                ORDER BY created_at DESC LIMIT 1""",
                (
                    message.provider, message.external_tenant_key,
                    message.external_conversation_id, message.external_user_id,
                    message.external_message_id, cutoff,
                ),
            ).fetchone()
        return dict(row) if row is not None else None

    def recent_context(
        self, message: IncomingChannelMessage, policy: ChannelContextPolicy,
    ) -> list[dict[str, str]]:
        cutoff = (_now() - timedelta(hours=policy.retention_hours)).isoformat()
        with self._connect() as db:
            rows = db.execute(
                """SELECT external_user_id,text,created_at,external_message_id
                FROM channel_context_messages WHERE provider=?
                AND external_tenant_key=? AND external_conversation_id=?
                AND external_message_id<>? AND created_at>=?
                ORDER BY created_at DESC LIMIT ?""",
                (
                    message.provider, message.external_tenant_key,
                    message.external_conversation_id, message.external_message_id,
                    cutoff, policy.max_messages,
                ),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def enqueue(self, message: IncomingChannelMessage) -> bool:
        now = _now().isoformat()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO channel_events(
                provider,event_id,external_message_id,message_json,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""",
                (message.provider, message.event_id, message.external_message_id,
                 message.model_dump_json(), "queued", now, now),
            )
        return cursor.rowcount == 1

    def enqueue_action(self, action: IncomingChannelAction) -> bool:
        now = _now().isoformat()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO channel_actions(
                provider,event_id,action_json,status,created_at,updated_at)
                VALUES(?,?,?,'queued',?,?)""",
                (action.provider, action.event_id, action.model_dump_json(), now, now),
            )
        return cursor.rowcount == 1

    def claim_next_action(
        self, worker_id: str, *, lease_seconds: int, retry_limit: int,
    ) -> dict[str, Any] | None:
        now = _now()
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE channel_actions SET status='queued', leased_by=NULL,
                leased_until=NULL, updated_at=?
                WHERE status='processing' AND leased_until<?""",
                (now.isoformat(), now.isoformat()),
            )
            row = db.execute(
                """SELECT * FROM channel_actions
                WHERE status IN ('queued','failed') AND attempts<?
                ORDER BY created_at LIMIT 1""",
                (retry_limit,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                """UPDATE channel_actions SET status='processing', attempts=attempts+1,
                leased_by=?,leased_until=?,updated_at=? WHERE provider=? AND event_id=?""",
                (worker_id, lease, now.isoformat(), row["provider"], row["event_id"]),
            )
            refreshed = db.execute(
                "SELECT * FROM channel_actions WHERE provider=? AND event_id=?",
                (row["provider"], row["event_id"]),
            ).fetchone()
        return dict(refreshed) if refreshed else None

    def finish_action(
        self, *, provider: str, event_id: str, status: str,
        result_text: str = "", error_code: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE channel_actions SET status=?,result_text=?,error_code=?,
                leased_by=NULL,leased_until=NULL,updated_at=?
                WHERE provider=? AND event_id=?""",
                (status, result_text, error_code, _now().isoformat(), provider, event_id),
            )

    def claim_next(
        self, worker_id: str, *, lease_seconds: int, retry_limit: int
    ) -> dict[str, Any] | None:
        now = _now()
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE channel_events SET status=CASE
                WHEN result_text IS NULL THEN 'queued' ELSE 'ready_to_deliver' END,
                leased_by=NULL, leased_until=NULL, updated_at=?
                WHERE status='processing' AND leased_until<?""",
                (now.isoformat(), now.isoformat()),
            )
            row = db.execute(
                """SELECT * FROM channel_events
                WHERE status IN ('queued','failed','ready_to_deliver') AND attempts<?
                ORDER BY CASE status WHEN 'ready_to_deliver' THEN 0 ELSE 1 END, created_at
                LIMIT 1""",
                (retry_limit,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                """UPDATE channel_events SET status='processing', attempts=attempts+1,
                leased_by=?, leased_until=?, updated_at=? WHERE provider=? AND event_id=?""",
                (worker_id, lease, now.isoformat(), row["provider"], row["event_id"]),
            )
            refreshed = db.execute(
                "SELECT * FROM channel_events WHERE provider=? AND event_id=?",
                (row["provider"], row["event_id"]),
            ).fetchone()
        return dict(refreshed) if refreshed else None

    def save_result(
        self, *, provider: str, event_id: str, result_text: str,
        internal_conversation_id: str, internal_run_id: str,
        result_payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE channel_events SET status='ready_to_deliver', result_text=?,
                result_payload_json=?,
                internal_conversation_id=?, internal_run_id=?, error_code=NULL,
                leased_by=NULL, leased_until=NULL, updated_at=?
                WHERE provider=? AND event_id=?""",
                (result_text, json.dumps(result_payload or {}, ensure_ascii=False),
                 internal_conversation_id, internal_run_id, _now().isoformat(), provider, event_id),
            )

    def delivery_part_status(
        self, *, provider: str, event_id: str, part_key: str,
    ) -> str:
        now = _now().isoformat()
        with self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO channel_delivery_parts(
                provider,event_id,part_key,status,created_at,updated_at)
                VALUES(?,?,?,'pending',?,?)""",
                (provider, event_id, part_key, now, now),
            )
            row = db.execute(
                """SELECT status FROM channel_delivery_parts
                WHERE provider=? AND event_id=? AND part_key=?""",
                (provider, event_id, part_key),
            ).fetchone()
        return str(row["status"])

    def mark_delivery_part(
        self, *, provider: str, event_id: str, part_key: str,
        status: str, external_message_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE channel_delivery_parts SET status=?, attempts=attempts+1,
                external_message_id=COALESCE(?,external_message_id), error_code=?,
                updated_at=? WHERE provider=? AND event_id=? AND part_key=?""",
                (status, external_message_id, error_code,
                 _now().isoformat(), provider, event_id, part_key),
            )

    def save_progress_message(
        self, *, provider: str, event_id: str, progress_message_id: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE channel_events SET progress_message_id=?, updated_at=?
                WHERE provider=? AND event_id=?""",
                (progress_message_id, _now().isoformat(), provider, event_id),
            )

    def mark_delivered(
        self, provider: str, event_id: str, external_result_message_id: str
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE channel_events SET status='delivered',
                external_result_message_id=?, error_code=NULL, leased_by=NULL,
                leased_until=NULL, updated_at=? WHERE provider=? AND event_id=?""",
                (external_result_message_id, _now().isoformat(), provider, event_id),
            )

    def mark_unbound(
        self, provider: str, event_id: str,
        external_result_message_id: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE channel_events SET status='unbound', error_code='identity_unbound',
                external_result_message_id=?,
                leased_by=NULL, leased_until=NULL, updated_at=? WHERE provider=? AND event_id=?""",
                (external_result_message_id, _now().isoformat(), provider, event_id),
            )

    def mark_denied(
        self, provider: str, event_id: str, *, reason: str,
        external_result_message_id: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE channel_events SET status='denied', error_code=?,
                external_result_message_id=?, leased_by=NULL, leased_until=NULL,
                updated_at=? WHERE provider=? AND event_id=?""",
                (
                    reason[:160], external_result_message_id,
                    _now().isoformat(), provider, event_id,
                ),
            )

    def mark_delivery_retry(self, provider: str, event_id: str, error_code: str) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE channel_events SET status='ready_to_deliver', error_code=?,
                leased_by=NULL, leased_until=NULL, updated_at=? WHERE provider=? AND event_id=?""",
                (error_code[:160], _now().isoformat(), provider, event_id),
            )

    def mark_failed(self, provider: str, event_id: str, error_code: str) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE channel_events SET status='failed', error_code=?,
                leased_by=NULL, leased_until=NULL, updated_at=? WHERE provider=? AND event_id=?""",
                (error_code[:160], _now().isoformat(), provider, event_id),
            )

    def session(self, message: IncomingChannelMessage) -> str | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT internal_conversation_id FROM channel_sessions
                WHERE provider=? AND external_tenant_key=?
                AND external_conversation_id=? AND external_user_id=?""",
                (message.provider, message.external_tenant_key,
                 message.external_conversation_id, message.external_user_id),
            ).fetchone()
        return str(row[0]) if row else None

    def save_session(self, message: IncomingChannelMessage, conversation_id: str) -> None:
        now = _now().isoformat()
        with self._connect() as db:
            db.execute(
                """INSERT INTO channel_sessions(provider,external_tenant_key,
                external_conversation_id,external_user_id,internal_conversation_id,
                created_at,updated_at) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(provider,external_tenant_key,external_conversation_id,external_user_id)
                DO UPDATE SET internal_conversation_id=excluded.internal_conversation_id,
                updated_at=excluded.updated_at""",
                (message.provider, message.external_tenant_key,
                 message.external_conversation_id, message.external_user_id,
                 conversation_id, now, now),
            )

    def status_counts(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS count FROM channel_events GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def action_status_counts(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS count FROM channel_actions GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def pending_bindings(self, provider: str = "feishu") -> list[dict[str, Any]]:
        """List only identity metadata; message text is intentionally excluded."""
        with self._connect() as db:
            rows = db.execute(
                """SELECT json_extract(message_json, '$.external_tenant_key') AS external_tenant_key,
                json_extract(message_json, '$.external_user_id') AS external_user_id,
                COUNT(*) AS event_count, MAX(updated_at) AS last_seen_at
                FROM channel_events WHERE provider=? AND status='unbound'
                AND NOT EXISTS (
                    SELECT 1 FROM channel_identity_bindings binding
                    WHERE binding.provider=channel_events.provider
                    AND binding.external_tenant_key=json_extract(
                        channel_events.message_json, '$.external_tenant_key'
                    )
                    AND binding.external_user_id=json_extract(
                        channel_events.message_json, '$.external_user_id'
                    )
                    AND binding.active=1
                )
                GROUP BY external_tenant_key, external_user_id
                ORDER BY last_seen_at DESC""",
                (provider,),
            ).fetchall()
        return [dict(row) for row in rows]
