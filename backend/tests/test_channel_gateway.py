from __future__ import annotations

from types import SimpleNamespace

from app.domains.channel_gateway import (
    ChannelBinding,
    ChannelContextPolicy,
    ChannelGateway,
    ChannelGatewayRepository,
    FeishuExternalGroupConfig,
    FeishuExternalRelayEvent,
    FeishuEventDecoder,
)
from app.domains.assistant.contracts import (
    BusinessIntent,
    DeliveryMode,
    RetrievalMode,
)
from app.domains.channel_gateway.contracts import ChannelDelivery
from app.domains.channel_gateway.contracts import ChannelResource
from app.domains.channel_gateway.feishu import _progress_card
from app.domains.identity import IdentityRepository
from app.settings import Settings


def _payload(*, event_id: str = "evt-1", open_id: str = "ou-hr", chat_id: str = "oc-team", text: str = "@_user_1 帮我总结本周招聘进展") -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "tenant_key": "tenant-feishu",
            "create_time": "1787184000000",
        },
        "event": {
            "sender": {
                "sender_type": "user",
                "sender_id": {"open_id": open_id},
            },
            "message": {
                "message_id": f"om-{event_id}",
                "chat_id": chat_id,
                "chat_type": "group",
                "message_type": "text",
                "content": '{"text":"' + text + '"}',
                "mentions": [{"key": "@_user_1"}],
            },
        },
    }


class FakeTransport:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.messages: list[tuple[str, str]] = []
        self.progress: list[tuple[str, str, str]] = []
        self.files: list[str] = []
        self.fail_once = fail_once

    def send_text(self, *, external_conversation_id: str, text: str) -> ChannelDelivery:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary_delivery_failure")
        self.messages.append((external_conversation_id, text))
        return ChannelDelivery(external_message_id=f"out-{len(self.messages)}")

    def start_processing(self, *, external_conversation_id: str, text: str) -> ChannelDelivery:
        self.progress.append(("start", external_conversation_id, text))
        return ChannelDelivery(external_message_id=f"progress-{len(self.progress)}")

    def update_processing(self, *, external_message_id: str, title: str, detail: str) -> None:
        self.progress.append((external_message_id, title, detail))

    def finish_processing(self, *, external_conversation_id: str, external_message_id: str | None, text: str, status: str, run_id: str | None = None) -> ChannelDelivery:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary_delivery_failure")
        self.messages.append((external_conversation_id, text))
        self.progress.append((external_message_id or "", status, text))
        return ChannelDelivery(external_message_id=external_message_id or f"out-{len(self.messages)}")

    def send_file(self, *, external_conversation_id: str, file_path, caption=None, display_name=None) -> ChannelDelivery:
        self.files.append(str(file_path))
        return ChannelDelivery(external_message_id=f"file-{len(self.files)}")


class FakeWorkbench:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.artifacts: list = []

    def handle_turn(self, **kwargs):
        self.calls.append(kwargs)
        sequence = len(self.calls)
        conversation_id = kwargs.get("conversation_id") or f"conv-{sequence}"
        return SimpleNamespace(
            conversation=SimpleNamespace(id=conversation_id),
            run=SimpleNamespace(id=f"run-{sequence}", requires_human_review=False),
            assistant_message=SimpleNamespace(content=f"这是第 {sequence} 次受治理回答"),
            artifacts=self.artifacts,
        )

    def preview_channel_intent(self, *, message, task_mode="自动选择"):
        if "政策" in message or "简历" in message:
            intent = (
                BusinessIntent.POLICY_QUESTION
                if "政策" in message else BusinessIntent.RECRUITMENT_SCREENING
            )
            retrieval = RetrievalMode.INTERNAL
        elif "润色" in message:
            intent = BusinessIntent.DOCUMENT_GENERATION
            retrieval = RetrievalMode.NONE
        elif "天气" in message or "新闻" in message:
            intent = BusinessIntent.PUBLIC_CURRENT_INFO
            retrieval = RetrievalMode.WEB
        else:
            intent = BusinessIntent.CONVERSATION
            retrieval = RetrievalMode.NONE
        return SimpleNamespace(
            business_intent=intent,
            delivery_mode=(
                DeliveryMode.INLINE_TRANSFORM
                if "润色" in message else DeliveryMode.INLINE_RESPONSE
            ),
            retrieval_mode=retrieval,
        )


def _gateway(tmp_path, *, transport=None, resource_loader=None, artifact_repository=None, approval_handler=None):
    settings = Settings(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "unihr.db",
        default_tenant_id="tenant_unihr_demo",
    )
    identity = IdentityRepository(settings.database_path, settings)
    repository = ChannelGatewayRepository(settings.database_path)
    workbench = FakeWorkbench()
    transport = transport or FakeTransport()
    gateway = ChannelGateway(
        repository=repository,
        identity=identity,
        workbench=workbench,
        transport=transport,
        decoder=FeishuEventDecoder(),
        default_tenant_id=settings.default_tenant_id,
        resource_loader=resource_loader,
        artifact_repository=artifact_repository,
        approval_handler=approval_handler,
    )
    return gateway, repository, workbench, transport, settings


def _bind(gateway: ChannelGateway, settings: Settings, *, open_id: str = "ou-hr") -> None:
    gateway.bind(
        ChannelBinding(
            external_tenant_key="tenant-feishu",
            external_user_id=open_id,
            tenant_id=settings.default_tenant_id,
            user_id="usr_unihr_demo",
        )
    )


def _configure_external_group(gateway, repository, settings):
    repository.save_external_group(FeishuExternalGroupConfig(
        group_id="group-public",
        display_name="外部协作群",
        owner_user_id="usr_unihr_demo",
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/demo-token",
    ))
    gateway.bind(ChannelBinding(
        provider="feishu_external",
        external_tenant_key="external:group-public",
        external_user_id="*",
        tenant_id=settings.default_tenant_id,
        user_id="usr_unihr_demo",
    ))


def test_feishu_event_is_normalized_deduplicated_and_delivered(tmp_path):
    gateway, repository, workbench, transport, settings = _gateway(tmp_path)
    _bind(gateway, settings)

    first = gateway.ingest_feishu(_payload())
    duplicate = gateway.ingest_feishu(_payload())
    redelivery_payload = _payload(event_id="evt-redelivery")
    redelivery_payload["event"]["message"]["message_id"] = "om-evt-1"
    duplicate_message = gateway.ingest_feishu(redelivery_payload)
    result = gateway.execute_next("worker-1")

    assert first.status == "queued"
    assert duplicate.status == "duplicate"
    assert duplicate_message.status == "duplicate"
    assert result is not None and result.status == "delivered"
    assert workbench.calls[0]["message"] == "帮我总结本周招聘进展"
    assert transport.messages == [("oc-team", "这是第 1 次受治理回答")]
    assert transport.progress[0][0] == "start"
    assert transport.progress[-1][1] == "completed"
    assert repository.status_counts()["delivered"] == 1


def test_group_session_is_isolated_by_external_user(tmp_path):
    gateway, _, workbench, _, settings = _gateway(tmp_path)
    _bind(gateway, settings, open_id="ou-a")
    _bind(gateway, settings, open_id="ou-b")
    for event_id, open_id in (("evt-a1", "ou-a"), ("evt-b1", "ou-b"), ("evt-a2", "ou-a")):
        gateway.ingest_feishu(_payload(event_id=event_id, open_id=open_id))
        assert gateway.execute_next("worker-1").status == "delivered"

    assert workbench.calls[0]["conversation_id"] is None
    assert workbench.calls[1]["conversation_id"] is None
    assert workbench.calls[2]["conversation_id"] == "conv-1"


def test_delivery_retry_reuses_result_without_rerunning_agent(tmp_path):
    transport = FakeTransport(fail_once=True)
    gateway, _, workbench, transport, settings = _gateway(tmp_path, transport=transport)
    _bind(gateway, settings)
    gateway.ingest_feishu(_payload())

    first = gateway.execute_next("worker-1")
    second = gateway.execute_next("worker-1")

    assert first.status == "ready_to_deliver"
    assert second.status == "delivered"
    assert len(workbench.calls) == 1
    assert transport.messages == [("oc-team", "这是第 1 次受治理回答")]


def test_unbound_user_does_not_enter_workbench(tmp_path):
    gateway, _, workbench, transport, _ = _gateway(tmp_path)
    gateway.ingest_feishu(_payload())

    result = gateway.execute_next("worker-1")

    assert result.status == "unbound"
    assert workbench.calls == []
    assert len(transport.messages) == 1
    assert "尚未绑定 UniHR" in transport.messages[0][1]


def test_pending_binding_disappears_after_identity_is_bound(tmp_path):
    gateway, repository, _, _, settings = _gateway(tmp_path)
    gateway.ingest_feishu(_payload())
    assert gateway.execute_next("worker-1").status == "unbound"
    assert repository.pending_bindings()[0]["external_user_id"] == "ou-hr"

    _bind(gateway, settings)

    assert repository.pending_bindings() == []


def test_file_event_is_loaded_and_passed_to_existing_workbench(tmp_path):
    class FakeResourceLoader:
        def load(self, message):
            assert message.attachments[0].resource_key == "file-key-1"
            return [
                ChannelResource(
                    filename="候选人简历.pdf",
                    mime_type="application/pdf",
                    content=b"resume bytes",
                )
            ]

    gateway, _, workbench, _, settings = _gateway(
        tmp_path, resource_loader=FakeResourceLoader(),
    )
    _bind(gateway, settings)
    payload = _payload(event_id="evt-file")
    payload["event"]["message"].update({
        "message_type": "file",
        "content": '{"file_key":"file-key-1","file_name":"候选人简历.pdf"}',
        "mentions": [],
    })

    assert gateway.ingest_feishu(payload).status == "queued"
    assert gateway.execute_next("worker-1").status == "delivered"
    upload = workbench.calls[0]["uploads"][0]
    assert upload.filename == "候选人简历.pdf"
    assert upload.content == b"resume bytes"


def test_artifact_delivery_retry_does_not_resend_completed_files(tmp_path):
    first_file = tmp_path / "result-1.docx"
    second_file = tmp_path / "result-2.xlsx"
    first_file.write_bytes(b"docx")
    second_file.write_bytes(b"xlsx")

    class ArtifactRepository:
        def get_artifact(self, artifact_id, principal):
            path = first_file if artifact_id == "art-1" else second_file
            return SimpleNamespace(path=str(path), display_name=path.name)

    class FileRetryTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.failed_second = False

        def send_file(self, *, external_conversation_id: str, file_path, caption=None, display_name=None):
            if str(file_path).endswith("result-2.xlsx") and not self.failed_second:
                self.failed_second = True
                raise RuntimeError("temporary_file_failure")
            return super().send_file(
                external_conversation_id=external_conversation_id,
                file_path=file_path,
                caption=caption,
                display_name=display_name,
            )

    transport = FileRetryTransport()
    gateway, _, workbench, _, settings = _gateway(
        tmp_path, transport=transport, artifact_repository=ArtifactRepository(),
    )
    workbench.artifacts = [SimpleNamespace(id="art-1", name="result-1.docx"), SimpleNamespace(id="art-2", name="result-2.xlsx")]
    _bind(gateway, settings)
    gateway.ingest_feishu(_payload(event_id="evt-artifacts"))

    first = gateway.execute_next("worker-1")
    second = gateway.execute_next("worker-1")

    assert first.status == "ready_to_deliver"
    assert second.status == "delivered"
    assert sum(path.endswith("result-1.docx") for path in transport.files) == 1
    assert sum(path.endswith("result-2.xlsx") for path in transport.files) == 1
    assert len(workbench.calls) == 1


def _action_payload(*, action="approve", event_id="evt-action", open_id="ou-hr", run_id="run-approval"):
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "card.action.trigger",
            "tenant_key": "tenant-feishu",
        },
        "event": {
            "operator": {"tenant_key": "tenant-feishu", "open_id": open_id},
            "action": {"value": {"action": action, "run_id": run_id}},
            "context": {"open_chat_id": "oc-team", "open_message_id": "om-card"},
        },
    }


def test_card_approval_is_queued_authorized_and_audited(tmp_path):
    class ApprovalHandler:
        def __init__(self):
            self.calls = []

        def decide(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(approved=kwargs["approved"])

    approvals = ApprovalHandler()
    gateway, repository, _, transport, settings = _gateway(
        tmp_path, approval_handler=approvals,
    )
    _bind(gateway, settings)

    receipt = gateway.ingest_feishu_action(_action_payload())
    duplicate = gateway.ingest_feishu_action(_action_payload())
    result = gateway.execute_next_action("worker-action")

    assert receipt.status == "queued"
    assert duplicate.status == "duplicate"
    assert result is not None and result.status == "delivered"
    assert approvals.calls[0]["run_id"] == "run-approval"
    assert approvals.calls[0]["approved"] is True
    assert approvals.calls[0]["principal"].user_id == "usr_unihr_demo"
    assert transport.progress[-1][1] == "审批已完成"
    assert repository.action_status_counts()["completed"] == 1


def test_card_approval_from_unbound_identity_cannot_change_run(tmp_path):
    class ApprovalHandler:
        def __init__(self):
            self.called = False

        def decide(self, **kwargs):
            self.called = True

    approvals = ApprovalHandler()
    gateway, repository, _, transport, _ = _gateway(
        tmp_path, approval_handler=approvals,
    )
    gateway.ingest_feishu_action(_action_payload(open_id="ou-unknown"))

    result = gateway.execute_next_action("worker-action")

    assert result is not None and result.error_code == "identity_unbound"
    assert approvals.called is False
    assert "没有 UniHR 审批权限" in transport.messages[-1][1]
    assert repository.action_status_counts()["denied"] == 1


def test_waiting_review_card_exposes_governed_approval_actions():
    card = _progress_card(
        "等待人工复核", "请核验后决定。", template="orange",
        approval_run_id="run-approval",
    )

    action_block = next(item for item in card["elements"] if item["tag"] == "action")
    assert card["config"]["update_multi"] is True
    assert [item["value"]["action"] for item in action_block["actions"]] == [
        "approve", "reject",
    ]
    assert all(
        item["value"]["run_id"] == "run-approval"
        for item in action_block["actions"]
    )


def test_authorized_group_context_is_observed_but_only_mention_executes(tmp_path):
    gateway, repository, workbench, transport, settings = _gateway(tmp_path)
    _bind(gateway, settings)
    gateway.configure_context(ChannelContextPolicy(
        external_tenant_key="tenant-feishu",
        external_conversation_id="oc-team",
        enabled=True,
        max_messages=5,
        retention_hours=24,
    ))
    discussion = _payload(
        event_id="evt-discussion", open_id="ou-colleague",
        text="本周五下午两点面试，地点会议室B。",
    )
    discussion["event"]["message"]["mentions"] = []

    observed = gateway.ingest_feishu(discussion)
    assert observed.status == "ignored"
    assert observed.reason == "context_observed"
    assert gateway.execute_next("worker-1") is None
    assert workbench.calls == []

    invoked = gateway.ingest_feishu(_payload(
        event_id="evt-summary", text="@_user_1 请总结刚才确定的面试安排",
    ))
    result = gateway.execute_next("worker-1")

    assert invoked.status == "queued"
    assert result is not None and result.status == "delivered"
    prompt = workbench.calls[0]["message"]
    assert "用户当前明确请求：请总结刚才确定的面试安排" in prompt
    assert "本周五下午两点面试，地点会议室B" in prompt
    assert "仅在与当前请求相关时使用" in prompt
    assert "参考了同群最近 1 条消息" in transport.messages[-1][1]
    assert repository.list_context_policies()[0].enabled is True


def test_group_context_is_not_captured_without_explicit_admin_policy(tmp_path):
    gateway, repository, workbench, _, _ = _gateway(tmp_path)
    discussion = _payload(event_id="evt-private-by-default", text="候选人信息不应默认保存")
    discussion["event"]["message"]["mentions"] = []

    receipt = gateway.ingest_feishu(discussion)

    assert receipt.status == "ignored"
    assert receipt.reason == "not_invoked_or_context_disabled"
    assert workbench.calls == []
    assert repository.list_context_policies() == []


def test_external_group_public_query_uses_wildcard_owner_binding(tmp_path):
    gateway, repository, workbench, transport, settings = _gateway(tmp_path)
    _configure_external_group(gateway, repository, settings)
    receipt = gateway.ingest_external_feishu(FeishuExternalRelayEvent(
        group_id="group-public",
        message_id="msg-weather",
        sender_id="external-person-a",
        text="查一下杭州今天的天气",
    ))

    result = gateway.execute_next("worker-external")

    assert receipt.status == "queued"
    assert result is not None and result.status == "delivered"
    assert workbench.calls[0]["principal"].user_id == "usr_unihr_demo"
    assert workbench.calls[0]["task_mode"] == "联网信息"
    assert transport.messages[-1][0] == "external:group-public"


def test_allowlisted_external_group_members_use_app_bot_without_personal_binding(tmp_path):
    gateway, repository, workbench, transport, settings = _gateway(tmp_path)
    repository.save_external_group(FeishuExternalGroupConfig(
        group_id="oc-external-partners",
        display_name="外部合作伙伴群",
        owner_user_id="usr_unihr_demo",
    ))

    first = gateway.ingest_feishu(_payload(
        event_id="evt-external-a", open_id="ou-external-a",
        chat_id="oc-external-partners", text="@_user_1 查一下宁波今天的天气",
    ))
    first_result = gateway.execute_next("worker-external-a")
    second = gateway.ingest_feishu(_payload(
        event_id="evt-external-b", open_id="ou-external-b",
        chat_id="oc-external-partners", text="@_user_1 帮我润色：材料赶紧发我",
    ))
    second_result = gateway.execute_next("worker-external-b")

    assert first.status == "queued"
    assert second.status == "queued"
    assert first_result is not None and first_result.status == "delivered"
    assert second_result is not None and second_result.status == "delivered"
    assert [call["principal"].user_id for call in workbench.calls] == [
        "usr_unihr_demo", "usr_unihr_demo",
    ]
    assert workbench.calls[0]["task_mode"] == "联网信息"
    assert workbench.calls[1]["task_mode"] == "自动选择"
    assert workbench.calls[0]["conversation_id"] is None
    assert workbench.calls[1]["conversation_id"] is None
    assert [target for target, _ in transport.messages] == [
        "oc-external-partners", "oc-external-partners",
    ]


def test_allowlisted_external_group_bare_mention_resumes_latest_same_sender_message(tmp_path):
    gateway, repository, workbench, _, _ = _gateway(tmp_path)
    repository.save_external_group(FeishuExternalGroupConfig(
        group_id="oc-external-partners",
        display_name="外部合作伙伴群",
        owner_user_id="usr_unihr_demo",
    ))
    question = _payload(
        event_id="evt-question-before-mention",
        open_id="ou-external-a",
        chat_id="oc-external-partners",
        text="请查询外包和派遣的区别",
    )
    question["event"]["message"]["mentions"] = []

    observed = gateway.ingest_feishu(question)
    invoked = gateway.ingest_feishu(_payload(
        event_id="evt-bare-mention",
        open_id="ou-external-a",
        chat_id="oc-external-partners",
        text="@_user_1",
    ))
    result = gateway.execute_next("worker-external-bare-mention")

    assert observed.status == "ignored"
    assert observed.reason == "context_observed"
    assert invoked.status == "queued"
    assert result is not None and result.status == "delivered"
    assert len(workbench.calls) == 1
    assert workbench.calls[0]["message"] == "请查询外包和派遣的区别"


def test_bare_mention_never_borrows_another_group_members_message(tmp_path):
    gateway, repository, workbench, _, _ = _gateway(tmp_path)
    repository.save_external_group(FeishuExternalGroupConfig(
        group_id="oc-external-partners",
        display_name="外部合作伙伴群",
        owner_user_id="usr_unihr_demo",
    ))
    other_members_question = _payload(
        event_id="evt-other-member-question",
        open_id="ou-external-b",
        chat_id="oc-external-partners",
        text="帮我查询内部薪酬政策",
    )
    other_members_question["event"]["message"]["mentions"] = []

    gateway.ingest_feishu(other_members_question)
    invoked = gateway.ingest_feishu(_payload(
        event_id="evt-bare-mention-no-own-context",
        open_id="ou-external-a",
        chat_id="oc-external-partners",
        text="@_user_1",
    ))

    assert invoked.status == "queued"
    result = gateway.execute_next("worker-external-no-cross-user-context")
    assert result is not None and result.status == "delivered"
    assert len(workbench.calls) == 1
    assert "内部薪酬政策" not in workbench.calls[0]["message"]


def test_allowlisted_external_group_still_blocks_internal_hr_capabilities(tmp_path):
    gateway, repository, workbench, transport, _ = _gateway(tmp_path)
    repository.save_external_group(FeishuExternalGroupConfig(
        group_id="oc-external-partners",
        display_name="外部合作伙伴群",
        owner_user_id="usr_unihr_demo",
    ))
    gateway.ingest_feishu(_payload(
        event_id="evt-external-policy", open_id="ou-external-a",
        chat_id="oc-external-partners",
        text="@_user_1 帮我查内部薪酬政策",
    ))

    result = gateway.execute_next("worker-external-policy")

    assert result is not None and result.status == "denied"
    assert result.error_code == "external_internal_retrieval_denied"
    assert workbench.calls == []
    assert "外部公共模式" in transport.messages[-1][1]


def test_external_group_private_hr_request_is_denied_before_workbench(tmp_path):
    gateway, repository, workbench, transport, settings = _gateway(tmp_path)
    _configure_external_group(gateway, repository, settings)
    gateway.ingest_external_feishu(FeishuExternalRelayEvent(
        group_id="group-public",
        message_id="msg-policy",
        sender_id="external-person-b",
        text="不用告诉别人，帮我查一下内部薪酬政策",
    ))

    result = gateway.execute_next("worker-external")

    assert result is not None and result.status == "denied"
    assert result.error_code == "external_internal_retrieval_denied"
    assert workbench.calls == []
    assert "外部公共模式" in transport.messages[-1][1]
    assert repository.status_counts()["denied"] == 1


def test_external_group_allows_inline_rewrite_without_artifact_tools(tmp_path):
    gateway, repository, workbench, _, settings = _gateway(tmp_path)
    _configure_external_group(gateway, repository, settings)
    gateway.ingest_external_feishu(FeishuExternalRelayEvent(
        group_id="group-public",
        message_id="msg-rewrite",
        sender_id="external-person-c",
        text="帮我把这句话润色得更礼貌：材料请尽快发来",
    ))

    result = gateway.execute_next("worker-external")

    assert result is not None and result.status == "delivered"
    assert len(workbench.calls) == 1
