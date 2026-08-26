from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import pytest

from app.domains.identity import Principal
from app.domains.turns import TurnExecutionModule, TurnExecutionRepository, TurnExecutionStatus
from app.settings import Settings


def _principal(user_id: str = "user_a", tenant_id: str = "tenant_a") -> Principal:
    return Principal(
        user_id=user_id,
        username=user_id,
        display_name=user_id,
        role="user",
        tenant_id=tenant_id,
        permissions=[],
    )


def test_turn_execution_events_are_cursor_based_and_owner_scoped(tmp_path):
    repository = TurnExecutionRepository(tmp_path / "turns.db")
    owner = _principal()
    record = repository.create(owner)

    first = repository.append_event(record.id, owner, "turn.accepted", "accepted", status="queued")
    second = repository.append_event(record.id, owner, "turn.running", "running")

    assert [event.sequence for event in repository.list_events(record.id, owner)] == [1, 2]
    assert repository.list_events(record.id, owner, after=first.sequence) == [second]
    with pytest.raises(KeyError):
        repository.get(record.id, _principal("user_b"))
    with pytest.raises(KeyError):
        repository.get(record.id, _principal("user_a", "tenant_b"))


def test_turn_execution_cancel_is_idempotent_and_durable(tmp_path):
    database = tmp_path / "turns.db"
    owner = _principal()
    repository = TurnExecutionRepository(database)
    record = repository.create(owner)

    cancelled = repository.request_cancel(record.id, owner)
    cancelled_again = repository.request_cancel(record.id, owner)

    assert cancelled.cancel_requested is True
    assert cancelled_again.cancel_requested is True
    reopened = TurnExecutionRepository(database)
    assert reopened.get(record.id, owner).cancel_requested is True
    assert reopened.get(record.id, owner).status == TurnExecutionStatus.FAILED
    assert reopened.get(record.id, owner).error_code == "process_restarted_before_completion"


def test_steering_cancels_predecessor_and_replans_on_same_conversation(tmp_path):
    database = tmp_path / "turns.db"
    owner = _principal()
    repository = TurnExecutionRepository(database)
    predecessor = repository.create(owner)
    repository.set_running(predecessor.id, owner)
    repository.append_event(
        predecessor.id,
        owner,
        "conversation.created",
        "conversation ready",
        details={"conversation_id": "conv_shared"},
    )
    fake_workbench = SimpleNamespace(settings=Settings(database_path=database))
    module = TurnExecutionModule(repository, fake_workbench)  # type: ignore[arg-type]
    applied = Event()
    captured: dict[str, object] = {}

    def fake_run(execution_id, principal, turn):
        captured.update({"execution_id": execution_id, "turn": turn})
        applied.set()

    module._run = fake_run  # type: ignore[method-assign]
    replacement = module.steer(
        predecessor.id,
        principal=owner,
        message="先别生成 Word，只在聊天里给我正文",
        conversation_id=None,
        task_mode="自动选择",
        task_brief=None,
        uploads=[],
        document_font="宋体",
        document_font_size=14,
    )

    assert repository.get(predecessor.id, owner).cancel_requested is True
    repository.mark_cancelled(predecessor.id, owner)
    assert applied.wait(2)
    assert captured["execution_id"] == replacement.id
    assert captured["turn"]["conversation_id"] == "conv_shared"  # type: ignore[index]
    events = repository.list_events(replacement.id, owner)
    assert [item.event for item in events] == [
        "turn.steering.queued",
        "turn.steering.predecessor_stopped",
        "turn.steering.applied",
    ]
    assert events[0].details["supersedes_execution_id"] == predecessor.id
    assert events[1].details["predecessor_status"] == "cancelled"
    module.close()
