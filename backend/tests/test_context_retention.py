from __future__ import annotations

import sqlite3

from app.domains.context_lifecycle import (
    ContextRetentionPolicy,
    ContextRetentionService,
    ContextService,
)
from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal


def _principal(tenant: str, user: str) -> Principal:
    return Principal(
        user_id=user,
        username=user,
        display_name=user,
        role="admin",
        tenant_id=tenant,
        permissions=[],
    )


def test_retention_preview_is_non_destructive_and_execute_is_tenant_scoped(tmp_path):
    database = tmp_path / "unihr.db"
    repository = ConversationRepository(database)
    ContextService(repository)
    tenant_a = _principal("tenant_a", "admin_a")
    tenant_b = _principal("tenant_b", "admin_b")
    conversation_a = repository.create_conversation("A", tenant_a)
    conversation_b = repository.create_conversation("B", tenant_b)
    old_dates = [
        "2025-01-01T00:00:00+00:00",
        "2025-01-02T00:00:00+00:00",
        "2025-01-03T00:00:00+00:00",
        "2025-01-04T00:00:00+00:00",
    ]
    with sqlite3.connect(database) as db:
        for conversation in (conversation_a, conversation_b):
            for index, created_at in enumerate(old_dates):
                db.execute(
                    """INSERT INTO context_snapshots(
                    id,conversation_id,summary_id,message_ids_json,attachment_ids_json,
                    artifact_ids_json,raw_chars,injected_chars,estimated_tokens,strategy,
                    content_hash,resource_query,hydration_count,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"snapshot_{conversation.id}_{index}", conversation.id, None, "[]", "[]",
                        "[]", 100, 50, 20, "test", f"hash_{conversation.id}_{index}", "", 0,
                        created_at,
                    ),
                )

    service = ContextRetentionService(database)
    policy = ContextRetentionPolicy(snapshot_days=30, snapshot_keep_latest=2)
    preview = service.run("tenant_a", policy)

    assert preview.dry_run is True
    assert next(item.count for item in preview.candidates if item.kind == "context_snapshots") == 2
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM context_snapshots").fetchone()[0] == 8

    executed = service.run("tenant_a", policy, execute=True)

    assert executed.status == "executed"
    assert executed.deleted_counts["context_snapshots"] == 2
    with sqlite3.connect(database) as db:
        remaining_a = db.execute(
            "SELECT COUNT(*) FROM context_snapshots WHERE conversation_id=?", (conversation_a.id,)
        ).fetchone()[0]
        remaining_b = db.execute(
            "SELECT COUNT(*) FROM context_snapshots WHERE conversation_id=?", (conversation_b.id,)
        ).fetchone()[0]
        audits = db.execute(
            "SELECT COUNT(*) FROM context_retention_audits WHERE tenant_id='tenant_a'"
        ).fetchone()[0]
    assert remaining_a == 2
    assert remaining_b == 4
    assert audits == 2
