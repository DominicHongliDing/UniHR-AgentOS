from app.domains.conversations.repository import ConversationRepository
from app.domains.context_lifecycle import ContextLifecycleModule
from app.domains.identity import Principal


def _principal(user_id: str) -> Principal:
    return Principal(
        user_id=user_id,
        username=user_id,
        display_name=user_id,
        role="user",
        tenant_id="tenant-test",
        permissions=[],
    )


def test_semantic_fact_versions_supersede_and_hydrate_exact_snapshot(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    owner = _principal("owner")
    conversation = repository.create_conversation("招聘复核", owner)
    service = ContextLifecycleModule(repository, max_chars=8_000)

    first = repository.add_message(
        conversation.id, "user", "请最终选择三名候选人，岗位名称为产业拓展实习生。", principal=owner,
    )
    changes = service.capture_semantic_facts(
        conversation.id,
        first.id,
        first.content,
        {"mode_spec": {"selection_count": 3, "job_title": "产业拓展实习生"}},
        owner,
    )
    assert {item.slot_key for item in changes} == {"selection_count", "job_title"}
    first_context = service.assemble(conversation.id, owner)
    first_count = next(item for item in first_context.semantic_facts if item.slot_key == "selection_count")
    assert first_count.value == 3
    assert first_count.version == 1

    second = repository.add_message(
        conversation.id, "user", "人数改成两名，以这次为准。", principal=owner,
    )
    service.capture_semantic_facts(conversation.id, second.id, second.content, {}, owner)
    second_context = service.assemble(conversation.id, owner)
    current_count = next(item for item in second_context.semantic_facts if item.slot_key == "selection_count")

    assert current_count.value == 2
    assert current_count.version == 2
    history = [item for item in service.list_semantic_facts(
        conversation.id, owner, include_history=True,
    ) if item.slot_key == "selection_count"]
    assert [item.status for item in history] == ["active", "superseded"]
    assert history[0].supersedes_id == first_count.id

    restored_first = service.hydrate(service.to_checkpoint(first_context), owner)
    restored_second = service.hydrate(service.to_checkpoint(second_context), owner)
    assert next(item for item in restored_first.semantic_facts if item.slot_key == "selection_count").value == 3
    assert next(item for item in restored_second.semantic_facts if item.slot_key == "selection_count").value == 2


def test_semantic_fact_explicit_retraction_removes_current_value(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    owner = _principal("owner")
    conversation = repository.create_conversation("预算任务", owner)
    service = ContextLifecycleModule(repository)

    first = repository.add_message(conversation.id, "user", "预算上限为20万元。", principal=owner)
    service.capture_semantic_facts(conversation.id, first.id, first.content, {}, owner)
    assert service.list_semantic_facts(conversation.id, owner)[0].display_value == "20万元"

    second = repository.add_message(conversation.id, "user", "取消预算上限。", principal=owner)
    changes = service.capture_semantic_facts(conversation.id, second.id, second.content, {}, owner)

    assert changes[0].action == "tombstoned"
    assert service.list_semantic_facts(conversation.id, owner) == []
    history = service.list_semantic_facts(conversation.id, owner, include_history=True)
    assert [item.status for item in history] == ["tombstoned", "superseded"]


def test_semantic_fact_scope_is_owner_isolated(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    owner = _principal("owner")
    other = _principal("other")
    conversation = repository.create_conversation("隔离任务", owner)
    service = ContextLifecycleModule(repository)
    message = repository.add_message(conversation.id, "user", "目标调整为2026年。", principal=owner)
    service.capture_semantic_facts(conversation.id, message.id, message.content, {}, owner)

    try:
        service.list_semantic_facts(conversation.id, other)
    except KeyError:
        pass
    else:
        raise AssertionError("cross-owner semantic facts must not be readable")


def test_conflicted_candidate_does_not_replace_active_until_user_confirms(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    owner = _principal("owner")
    conversation = repository.create_conversation("冲突确认", owner)
    service = ContextLifecycleModule(repository)
    first = repository.add_message(conversation.id, "user", "最终选择三名候选人。", principal=owner)
    service.capture_semantic_facts(conversation.id, first.id, first.content, {}, owner)

    conflict = repository.add_message(
        conversation.id, "user", "最终选择三名，也可以最终选择两名。", principal=owner,
    )
    changes = service.capture_semantic_facts(
        conversation.id, conflict.id, conflict.content, {}, owner,
    )

    assert changes[0].action == "conflicted"
    assert service.list_semantic_facts(conversation.id, owner)[0].value == 3
    pending = service.list_pending_semantic_facts(conversation.id, owner)
    assert pending[0].status == "conflicted"
    assert pending[0].value == 2

    resolved = service.resolve_semantic_fact(pending[0].id, True, owner)
    assert resolved.status == "active"
    assert resolved.value == 2
    assert service.list_pending_semantic_facts(conversation.id, owner) == []
    assert service.list_semantic_facts(conversation.id, owner)[0].value == 2


def test_domain_slot_schema_captures_document_and_spreadsheet_requirements(tmp_path):
    repository = ConversationRepository(tmp_path / "unihr.db")
    owner = _principal("owner")
    conversation = repository.create_conversation("领域字段", owner)
    service = ContextLifecycleModule(repository)
    message = repository.add_message(conversation.id, "user", "生成会议通知。", principal=owner)
    service.capture_semantic_facts(
        conversation.id,
        message.id,
        message.content,
        {
            "kind": "document",
            "mode_spec": {
                "document_type": "会议通知",
                "title": "关于召开年度人事工作会议的通知",
                "issuer": "人事处",
            },
            "artifact_style": {
                "document": {
                    "body_font": "仿宋_GB2312",
                    "body_size_pt": 16,
                    "body_alignment": "justify",
                },
            },
        },
        owner,
    )

    facts = {item.slot_key: item for item in service.list_semantic_facts(conversation.id, owner)}
    assert facts["document.document_type"].domain == "document"
    assert facts["document.title"].display_value == "关于召开年度人事工作会议的通知"
    assert facts["document.body_font"].display_value == "仿宋_GB2312"
    assert facts["document.body_size_pt"].value == 16
