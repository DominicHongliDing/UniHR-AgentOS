from datetime import datetime, timezone

from app.domains.assistant.contracts import BusinessIntent, ExecutionMode, TurnAction
from app.domains.conversations.contracts import AttachmentRecord
from app.domains.conversations.lifecycle import ConversationLifecycleService
from app.domains.conversations.repository import ConversationRepository
from app.domains.context_lifecycle import ContextLifecycleModule
from app.domains.identity import Principal
from app.domains.runs import RunRepository, WorkflowRunRequest
from app.domains.turns import TurnExecutionRepository


class RecordingWorkflow:
    def __init__(self):
        self.deleted = []

    def delete(self, pointer):
        self.deleted.append(pointer)


def _principal() -> Principal:
    return Principal(
        user_id="owner", username="owner", display_name="Owner", role="user",
        tenant_id="tenant-test", permissions=[],
    )


def test_conversation_cleanup_removes_all_state_and_records_audit(tmp_path):
    database_path = tmp_path / "unihr.db"
    data_root = tmp_path / "data"
    data_root.mkdir()
    repository = ConversationRepository(database_path)
    runs = RunRepository(database_path)
    turns = TurnExecutionRepository(database_path)
    workflow = RecordingWorkflow()
    service = ConversationLifecycleService(repository, runs, turns, workflow, data_root)
    context = ContextLifecycleModule(repository)
    principal = _principal()
    conversation = repository.create_conversation("待删除任务", principal)
    message = repository.add_message(
        conversation.id, "user", "最终选择三名候选人。", principal=principal,
    )
    context.capture_semantic_facts(conversation.id, message.id, message.content, {}, principal)
    context.assemble(conversation.id, principal)

    source_path = data_root / "resume.txt"
    source_path.write_text("candidate", encoding="utf-8")
    repository.add_attachment(AttachmentRecord(
        id="att_test", conversation_id=conversation.id, message_id=message.id,
        display_name="resume.txt", mime_type="text/plain", extension=".txt",
        byte_size=9, saved_path=str(source_path), extracted_text="candidate",
        summary={}, created_at=datetime.now(timezone.utc).isoformat(),
    ), principal)
    request = WorkflowRunRequest(
        run_id="run_test", tenant_id=principal.tenant_id, owner_user_id=principal.user_id,
        conversation_id=conversation.id, business_intent=BusinessIntent.RECRUITMENT_SCREENING,
        turn_action=TurnAction.START_NEW, execution_mode=ExecutionMode.EXECUTE,
    )
    runs.start(request)
    execution = turns.create(principal)
    turns.append_event(
        execution.id, principal, "turn.prepared", "prepared",
        details={"conversation_id": conversation.id},
    )
    turns.mark_cancelled(execution.id, principal)

    report = service.delete(conversation.id, principal)

    assert report.verification_status == "passed"
    assert report.graph_threads_deleted == 1
    assert report.turn_executions_deleted == 1
    assert report.deleted_counts["context_semantic_facts"] == 1
    assert report.deleted_counts["context_snapshots"] == 1
    assert report.files_deleted == 1
    assert not source_path.exists()
    assert workflow.deleted[0].run_id == "run_test"
    assert service.list_audits(principal)[0].audit_id == report.audit_id
    assert repository.list_conversations(principal) == []


def test_conversation_cleanup_refuses_active_turn(tmp_path):
    database_path = tmp_path / "unihr.db"
    repository = ConversationRepository(database_path)
    runs = RunRepository(database_path)
    turns = TurnExecutionRepository(database_path)
    service = ConversationLifecycleService(repository, runs, turns, RecordingWorkflow(), tmp_path)
    principal = _principal()
    conversation = repository.create_conversation("执行中任务", principal)
    execution = turns.create(principal)
    turns.append_event(
        execution.id, principal, "turn.prepared", "prepared",
        details={"conversation_id": conversation.id},
    )

    try:
        service.delete(conversation.id, principal)
    except RuntimeError as exc:
        assert str(exc) == "conversation_has_active_turn"
    else:
        raise AssertionError("active turn must block conversation cleanup")
    assert repository.get_conversation(conversation.id, principal).id == conversation.id
