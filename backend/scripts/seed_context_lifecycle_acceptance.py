"""Create one visible, user-scoped conversation for Context Lifecycle acceptance."""

from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal
from app.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    assert settings.database_path is not None
    repository = ConversationRepository(settings.database_path)
    principal = Principal(
        user_id="usr_unihr_demo",
        username="hr-user",
        display_name="HR 用户",
        role="user",
        tenant_id=settings.default_tenant_id,
        permissions=[],
    )
    for existing in repository.list_conversations(principal):
        if existing.title.startswith("Context Lifecycle"):
            repository.delete_conversation(existing.id, principal)
    conversation = repository.create_conversation("Context Lifecycle 长对话验收", principal)
    turns = [
        ("user", "验收目标：基于已有讨论形成2026年度人才工作方案，最终最多保留2项优先行动。"),
        ("assistant", "已记录目标，并将人数和年度作为受保护事实。"),
        ("user", "硬性要求：不得使用年龄作为筛选依据，所有结论必须注明待人工复核。"),
        ("assistant", "已锁定公平性与人工复核要求。"),
        ("user", "第一步先梳理政策依据，不要直接生成最终文件。"),
        ("assistant", "已完成政策依据范围梳理。"),
        ("user", "第二步整理执行清单，预算上限为20万元。"),
        ("assistant", "已记录执行清单和预算上限。"),
        ("user", "第三步保留尚未确认的地区条件，不能擅自推断。"),
        ("assistant", "地区条件保持待核实状态。"),
        ("user", "请记住前面所有锁定条件，下一轮我会追问。"),
        ("assistant", "已保留任务目标、关键约束、完成步骤和待核实事项。"),
    ]
    for role, content in turns:
        repository.add_message(conversation.id, role, content, principal=principal)
    print(conversation.id)


if __name__ == "__main__":
    main()
