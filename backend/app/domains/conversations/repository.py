from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from app.domains.identity import Principal
from app.domains.identity.contracts import ANSWER_HISTORY_MANAGE
from app.domains.identity.repository import ADMIN_USER_ID

from .contracts import (
    AdminConversationSummary,
    AdminFeedbackRecord,
    AnswerFeedback,
    ArtifactRecord,
    AttachmentRecord,
    Conversation,
    FeedbackStats,
    MessageRecord,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationRepository:
    """Own user-scoped conversations and the governed answer-feedback lifecycle."""

    def __init__(
        self,
        database_path: str | Path,
        default_tenant_id: str = "tenant_unihr_demo",
        default_owner_user_id: str = ADMIN_USER_ID,
    ) -> None:
        self.database_path = Path(database_path)
        self.default_tenant_id = default_tenant_id
        self.default_owner_user_id = default_owner_user_id
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
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'tenant_unihr_demo',
                    owner_user_id TEXT NOT NULL DEFAULT 'usr_unihr_admin',
                    title TEXT NOT NULL,
                    preview TEXT NOT NULL DEFAULT '',
                    pinned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    trace_json TEXT NOT NULL DEFAULT '[]',
                    task_brief_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    display_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    saved_path TEXT NOT NULL,
                    extracted_text TEXT NOT NULL DEFAULT '',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS answer_feedback (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    helpful INTEGER NOT NULL,
                    comment TEXT NOT NULL DEFAULT '',
                    test_status TEXT NOT NULL DEFAULT 'unreviewed',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(message_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_answer_feedback_tenant_updated
                    ON answer_feedback(tenant_id, updated_at DESC);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(conversations)").fetchall()}
            if "tenant_id" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN tenant_id TEXT")
            if "owner_user_id" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN owner_user_id TEXT")
            db.execute(
                "UPDATE conversations SET tenant_id=? WHERE tenant_id IS NULL OR tenant_id=''",
                (self.default_tenant_id,),
            )
            db.execute(
                "UPDATE conversations SET owner_user_id=? WHERE owner_user_id IS NULL OR owner_user_id=''",
                (self.default_owner_user_id,),
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_owner_updated ON conversations(tenant_id, owner_user_id, pinned DESC, updated_at DESC)"
            )
            message_columns = {row["name"] for row in db.execute("PRAGMA table_info(messages)").fetchall()}
            if "task_brief_json" not in message_columns:
                db.execute("ALTER TABLE messages ADD COLUMN task_brief_json TEXT NOT NULL DEFAULT '{}'")

    def _scope(self, principal: Principal | None) -> tuple[str, str]:
        if principal is None:
            return self.default_tenant_id, self.default_owner_user_id
        return principal.tenant_id, principal.user_id

    @staticmethod
    def _require_admin(principal: Principal) -> None:
        if not principal.can(ANSWER_HISTORY_MANAGE):
            raise PermissionError(ANSWER_HISTORY_MANAGE)

    def create_conversation(self, title: str, principal: Principal | None = None) -> Conversation:
        tenant_id, owner_user_id = self._scope(principal)
        now = _now()
        item = Conversation(
            id=f"conv_{uuid4().hex}", tenant_id=tenant_id, owner_user_id=owner_user_id,
            title=title.strip()[:80] or "新对话", created_at=now, updated_at=now,
        )
        with self._connect() as db:
            db.execute(
                """INSERT INTO conversations(
                id,tenant_id,owner_user_id,title,preview,pinned,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (item.id, tenant_id, owner_user_id, item.title, item.preview, 0, now, now),
            )
        return item

    def list_conversations(self, principal: Principal | None = None) -> list[Conversation]:
        tenant_id, owner_user_id = self._scope(principal)
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM conversations WHERE tenant_id=? AND owner_user_id=?
                ORDER BY pinned DESC, updated_at DESC""",
                (tenant_id, owner_user_id),
            ).fetchall()
        return [self._conversation(row) for row in rows]

    def get_conversation(self, conversation_id: str, principal: Principal | None = None) -> Conversation:
        tenant_id, owner_user_id = self._scope(principal)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM conversations WHERE id=? AND tenant_id=? AND owner_user_id=?",
                (conversation_id, tenant_id, owner_user_id),
            ).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        return self._conversation(row)

    def update_conversation(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        preview: str | None = None,
        principal: Principal | None = None,
    ) -> Conversation:
        current = self.get_conversation(conversation_id, principal)
        next_title = title.strip()[:80] if title is not None and title.strip() else current.title
        next_pinned = current.pinned if pinned is None else pinned
        next_preview = current.preview if preview is None else preview.strip()[:240]
        updated_at = _now()
        with self._connect() as db:
            db.execute(
                """UPDATE conversations SET title=?,preview=?,pinned=?,updated_at=?
                WHERE id=? AND tenant_id=? AND owner_user_id=?""",
                (next_title, next_preview, int(next_pinned), updated_at, conversation_id, current.tenant_id, current.owner_user_id),
            )
        return self.get_conversation(conversation_id, principal)

    def delete_conversation(self, conversation_id: str, principal: Principal | None = None) -> list[str]:
        current = self.get_conversation(conversation_id, principal)
        return self._delete_owned(current.id, current.tenant_id, current.owner_user_id)

    def _delete_owned(self, conversation_id: str, tenant_id: str, owner_user_id: str | None = None) -> list[str]:
        with self._connect() as db:
            where = "id=? AND tenant_id=?"
            values: list[str] = [conversation_id, tenant_id]
            if owner_user_id is not None:
                where += " AND owner_user_id=?"
                values.append(owner_user_id)
            exists = db.execute(f"SELECT 1 FROM conversations WHERE {where}", values).fetchone()
            if exists is None:
                raise KeyError(conversation_id)
            paths = [str(row[0]) for row in db.execute(
                "SELECT saved_path FROM attachments WHERE conversation_id=? UNION SELECT path FROM artifacts WHERE conversation_id=?",
                (conversation_id, conversation_id),
            ).fetchall()]
            db.execute(f"DELETE FROM conversations WHERE {where}", values)
        return paths

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        trace: list[dict[str, object]] | None = None,
        task_brief: dict[str, object] | None = None,
        principal: Principal | None = None,
    ) -> MessageRecord:
        self.get_conversation(conversation_id, principal)
        item = MessageRecord(
            id=f"msg_{uuid4().hex}", conversation_id=conversation_id, role=role,  # type: ignore[arg-type]
            content=content, created_at=_now(), trace=trace or [], task_brief=task_brief or {},
        )
        with self._connect() as db:
            db.execute(
                """INSERT INTO messages(
                id,conversation_id,role,content,trace_json,task_brief_json,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    item.id, conversation_id, item.role, content,
                    json.dumps(item.trace, ensure_ascii=False),
                    json.dumps(item.task_brief, ensure_ascii=False), item.created_at,
                ),
            )
        self.update_conversation(conversation_id, preview=content, principal=principal)
        return item

    def list_messages(
        self, conversation_id: str, limit: int = 100, principal: Principal | None = None,
    ) -> list[MessageRecord]:
        self.get_conversation(conversation_id, principal)
        return self._list_messages_unchecked(conversation_id, limit)

    def _list_messages_unchecked(self, conversation_id: str, limit: int = 100) -> list[MessageRecord]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM (SELECT * FROM messages WHERE conversation_id=?
                ORDER BY created_at DESC LIMIT ?) ORDER BY created_at""",
                (conversation_id, limit),
            ).fetchall()
        return [self._message(row) for row in rows]

    def list_messages_after(
        self,
        conversation_id: str,
        after_message_id: str = "",
        limit: int = 1_000,
        principal: Principal | None = None,
    ) -> list[MessageRecord]:
        """Return messages after a durable cursor, oldest first.

        Context compaction uses this cursor API so an old conversation does not
        need to be reloaded in full whenever a new turn arrives.
        """
        self.get_conversation(conversation_id, principal)
        with self._connect() as db:
            if not after_message_id:
                rows = db.execute(
                    """SELECT * FROM messages WHERE conversation_id=?
                    ORDER BY created_at, id LIMIT ?""",
                    (conversation_id, limit),
                ).fetchall()
            else:
                cursor = db.execute(
                    "SELECT created_at,id FROM messages WHERE id=? AND conversation_id=?",
                    (after_message_id, conversation_id),
                ).fetchone()
                if cursor is None:
                    raise KeyError(after_message_id)
                rows = db.execute(
                    """SELECT * FROM messages WHERE conversation_id=? AND
                    (created_at > ? OR (created_at = ? AND id > ?))
                    ORDER BY created_at, id LIMIT ?""",
                    (
                        conversation_id, cursor["created_at"], cursor["created_at"],
                        cursor["id"], limit,
                    ),
                ).fetchall()
        return [self._message(row) for row in rows]

    def add_attachment(self, record: AttachmentRecord, principal: Principal | None = None) -> AttachmentRecord:
        self.get_conversation(record.conversation_id, principal)
        with self._connect() as db:
            db.execute(
                """INSERT INTO attachments(
                id,conversation_id,message_id,display_name,mime_type,extension,byte_size,
                saved_path,extracted_text,summary_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.id, record.conversation_id, record.message_id, record.display_name,
                    record.mime_type, record.extension, record.byte_size, record.saved_path,
                    record.extracted_text, json.dumps(record.summary, ensure_ascii=False), record.created_at,
                ),
            )
        return record

    def list_attachments(self, conversation_id: str, principal: Principal | None = None) -> list[AttachmentRecord]:
        self.get_conversation(conversation_id, principal)
        return self._list_attachments_unchecked(conversation_id)

    def get_attachment(self, attachment_id: str, principal: Principal | None = None) -> AttachmentRecord:
        tenant_id, owner_user_id = self._scope(principal)
        with self._connect() as db:
            row = db.execute(
                """SELECT a.* FROM attachments a JOIN conversations c ON c.id=a.conversation_id
                WHERE a.id=? AND c.tenant_id=? AND c.owner_user_id=?""",
                (attachment_id, tenant_id, owner_user_id),
            ).fetchone()
        if row is None:
            raise KeyError(attachment_id)
        return self._attachment(row)

    def _list_attachments_unchecked(self, conversation_id: str) -> list[AttachmentRecord]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM attachments WHERE conversation_id=? ORDER BY created_at", (conversation_id,)).fetchall()
        return [self._attachment(row) for row in rows]

    def add_artifact(self, record: ArtifactRecord, principal: Principal | None = None) -> ArtifactRecord:
        self.get_conversation(record.conversation_id, principal)
        with self._connect() as db:
            db.execute(
                """INSERT INTO artifacts(
                id,conversation_id,message_id,kind,display_name,path,mime_type,byte_size,metadata_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.id, record.conversation_id, record.message_id, record.kind, record.display_name,
                    record.path, record.mime_type, record.byte_size, json.dumps(record.metadata, ensure_ascii=False), record.created_at,
                ),
            )
        return record

    def get_artifact(self, artifact_id: str, principal: Principal | None = None) -> ArtifactRecord:
        tenant_id, owner_user_id = self._scope(principal)
        with self._connect() as db:
            row = db.execute(
                """SELECT a.* FROM artifacts a JOIN conversations c ON c.id=a.conversation_id
                WHERE a.id=? AND c.tenant_id=? AND c.owner_user_id=?""",
                (artifact_id, tenant_id, owner_user_id),
            ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return self._artifact(row)

    def list_artifacts(self, conversation_id: str, principal: Principal | None = None) -> list[ArtifactRecord]:
        self.get_conversation(conversation_id, principal)
        return self._list_artifacts_unchecked(conversation_id)

    def _list_artifacts_unchecked(self, conversation_id: str) -> list[ArtifactRecord]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM artifacts WHERE conversation_id=? ORDER BY created_at", (conversation_id,)).fetchall()
        return [self._artifact(row) for row in rows]

    def list_feedback(self, conversation_id: str, principal: Principal | None = None) -> list[AnswerFeedback]:
        current = self.get_conversation(conversation_id, principal)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM answer_feedback WHERE conversation_id=? AND tenant_id=? AND user_id=? ORDER BY created_at",
                (conversation_id, current.tenant_id, current.owner_user_id),
            ).fetchall()
        return [self._feedback(row) for row in rows]

    def upsert_feedback(
        self,
        message_id: str,
        helpful: bool,
        comment: str = "",
        principal: Principal | None = None,
    ) -> AnswerFeedback:
        tenant_id, owner_user_id = self._scope(principal)
        now = _now()
        clean_comment = " ".join(comment.split())[:1000]
        with self._connect() as db:
            target = db.execute(
                """SELECT m.conversation_id FROM messages m
                JOIN conversations c ON c.id=m.conversation_id
                WHERE m.id=? AND m.role='assistant' AND c.tenant_id=? AND c.owner_user_id=?""",
                (message_id, tenant_id, owner_user_id),
            ).fetchone()
            if target is None:
                raise KeyError(message_id)
            existing = db.execute(
                "SELECT * FROM answer_feedback WHERE message_id=? AND user_id=?",
                (message_id, owner_user_id),
            ).fetchone()
            if existing is None:
                feedback_id = f"fb_{uuid4().hex}"
                db.execute(
                    """INSERT INTO answer_feedback(
                    id,tenant_id,conversation_id,message_id,user_id,helpful,comment,test_status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,'unreviewed',?,?)""",
                    (feedback_id, tenant_id, target["conversation_id"], message_id, owner_user_id, int(helpful), clean_comment, now, now),
                )
            else:
                feedback_id = existing["id"]
                db.execute(
                    """UPDATE answer_feedback SET helpful=?,comment=?,test_status='unreviewed',updated_at=?
                    WHERE id=?""",
                    (int(helpful), clean_comment, now, feedback_id),
                )
            row = db.execute("SELECT * FROM answer_feedback WHERE id=?", (feedback_id,)).fetchone()
        assert row is not None
        return self._feedback(row)

    def admin_list_history(
        self, principal: Principal, query: str = "", limit: int = 100,
    ) -> list[AdminConversationSummary]:
        self._require_admin(principal)
        clean = f"%{query.strip()}%"
        with self._connect() as db:
            rows = db.execute(
                """SELECT c.*, u.username AS owner_username, u.display_name AS owner_display_name,
                (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count,
                (SELECT COUNT(*) FROM answer_feedback f WHERE f.conversation_id=c.id) AS feedback_count
                FROM conversations c JOIN users u ON u.id=c.owner_user_id
                WHERE c.tenant_id=? AND (?='%%' OR c.title LIKE ? OR c.preview LIKE ? OR u.username LIKE ?)
                ORDER BY c.updated_at DESC LIMIT ?""",
                (principal.tenant_id, clean, clean, clean, clean, max(1, min(limit, 500))),
            ).fetchall()
        return [self._admin_conversation(row) for row in rows]

    def admin_get_conversation(self, conversation_id: str, principal: Principal) -> Conversation:
        self._require_admin(principal)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM conversations WHERE id=? AND tenant_id=?",
                (conversation_id, principal.tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        return self._conversation(row)

    def admin_conversation_content(
        self, conversation_id: str, principal: Principal,
    ) -> tuple[list[MessageRecord], list[AttachmentRecord], list[ArtifactRecord], list[AnswerFeedback]]:
        self.admin_get_conversation(conversation_id, principal)
        with self._connect() as db:
            feedback_rows = db.execute(
                "SELECT * FROM answer_feedback WHERE conversation_id=? ORDER BY created_at",
                (conversation_id,),
            ).fetchall()
        return (
            self._list_messages_unchecked(conversation_id), self._list_attachments_unchecked(conversation_id),
            self._list_artifacts_unchecked(conversation_id), [self._feedback(row) for row in feedback_rows],
        )

    def admin_delete_conversation(self, conversation_id: str, principal: Principal) -> list[str]:
        current = self.admin_get_conversation(conversation_id, principal)
        return self._delete_owned(conversation_id, current.tenant_id)

    def admin_feedback_stats(self, principal: Principal) -> FeedbackStats:
        self._require_admin(principal)
        with self._connect() as db:
            row = db.execute(
                """SELECT COUNT(*) AS total, COALESCE(SUM(helpful),0) AS helpful,
                COALESCE(SUM(CASE WHEN helpful=0 THEN 1 ELSE 0 END),0) AS not_helpful,
                COALESCE(SUM(CASE WHEN test_status='unreviewed' THEN 1 ELSE 0 END),0) AS pending_test_review,
                COALESCE(SUM(CASE WHEN test_status='added_to_test' THEN 1 ELSE 0 END),0) AS added_to_test
                FROM answer_feedback WHERE tenant_id=?""",
                (principal.tenant_id,),
            ).fetchone()
        assert row is not None
        total = int(row["total"])
        helpful_count = int(row["helpful"])
        return FeedbackStats(
            total=total, helpful=helpful_count, not_helpful=int(row["not_helpful"]),
            helpful_rate=round(helpful_count / total, 4) if total else None,
            pending_test_review=int(row["pending_test_review"]), added_to_test=int(row["added_to_test"]),
        )

    def admin_list_feedback(
        self, principal: Principal, helpful: bool | None = None, limit: int = 200,
    ) -> list[AdminFeedbackRecord]:
        self._require_admin(principal)
        values: list[object] = [principal.tenant_id]
        helpful_filter = ""
        if helpful is not None:
            helpful_filter = " AND f.helpful=?"
            values.append(int(helpful))
        values.append(max(1, min(limit, 500)))
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT f.*, c.title AS conversation_title, u.username AS owner_username,
                u.display_name AS owner_display_name, m.content AS answer,
                COALESCE((SELECT q.content FROM messages q WHERE q.conversation_id=m.conversation_id
                    AND q.role='user' AND q.created_at<m.created_at ORDER BY q.created_at DESC LIMIT 1),'') AS question
                FROM answer_feedback f
                JOIN conversations c ON c.id=f.conversation_id
                JOIN messages m ON m.id=f.message_id
                JOIN users u ON u.id=f.user_id
                WHERE f.tenant_id=?{helpful_filter}
                ORDER BY f.updated_at DESC LIMIT ?""",
                values,
            ).fetchall()
        return [self._admin_feedback(row) for row in rows]

    def admin_update_feedback_status(
        self, feedback_id: str, test_status: str, principal: Principal,
    ) -> AdminFeedbackRecord:
        self._require_admin(principal)
        if test_status not in {"unreviewed", "added_to_test", "resolved"}:
            raise ValueError("invalid_test_status")
        with self._connect() as db:
            result = db.execute(
                "UPDATE answer_feedback SET test_status=?,updated_at=? WHERE id=? AND tenant_id=?",
                (test_status, _now(), feedback_id, principal.tenant_id),
            )
            if result.rowcount == 0:
                raise KeyError(feedback_id)
        rows = [item for item in self.admin_list_feedback(principal, limit=500) if item.id == feedback_id]
        if not rows:
            raise KeyError(feedback_id)
        return rows[0]

    @staticmethod
    def _conversation(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"], tenant_id=row["tenant_id"], owner_user_id=row["owner_user_id"],
            title=row["title"], preview=row["preview"], pinned=bool(row["pinned"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _message(row: sqlite3.Row) -> MessageRecord:
        return MessageRecord(
            id=row["id"], conversation_id=row["conversation_id"], role=row["role"],
            content=row["content"], created_at=row["created_at"], trace=json.loads(row["trace_json"]),
            task_brief=json.loads(row["task_brief_json"] or "{}"),
        )

    @staticmethod
    def _attachment(row: sqlite3.Row) -> AttachmentRecord:
        return AttachmentRecord(
            id=row["id"], conversation_id=row["conversation_id"], message_id=row["message_id"],
            display_name=row["display_name"], mime_type=row["mime_type"], extension=row["extension"],
            byte_size=row["byte_size"], saved_path=row["saved_path"], extracted_text=row["extracted_text"],
            summary=json.loads(row["summary_json"]), created_at=row["created_at"],
        )

    @staticmethod
    def _artifact(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            id=row["id"], conversation_id=row["conversation_id"], message_id=row["message_id"],
            kind=row["kind"], display_name=row["display_name"], path=row["path"],
            mime_type=row["mime_type"], byte_size=row["byte_size"],
            metadata=json.loads(row["metadata_json"]), created_at=row["created_at"],
        )

    @staticmethod
    def _feedback(row: sqlite3.Row) -> AnswerFeedback:
        return AnswerFeedback(
            id=row["id"], conversation_id=row["conversation_id"], message_id=row["message_id"],
            user_id=row["user_id"], helpful=bool(row["helpful"]), comment=row["comment"],
            test_status=row["test_status"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @classmethod
    def _admin_conversation(cls, row: sqlite3.Row) -> AdminConversationSummary:
        item = cls._conversation(row)
        return AdminConversationSummary(
            **item.model_dump(), owner_username=row["owner_username"], owner_display_name=row["owner_display_name"],
            message_count=int(row["message_count"]), feedback_count=int(row["feedback_count"]),
        )

    @classmethod
    def _admin_feedback(cls, row: sqlite3.Row) -> AdminFeedbackRecord:
        item = cls._feedback(row)
        return AdminFeedbackRecord(
            **item.model_dump(), owner_username=row["owner_username"], owner_display_name=row["owner_display_name"],
            conversation_title=row["conversation_title"], question=row["question"], answer=row["answer"],
        )
