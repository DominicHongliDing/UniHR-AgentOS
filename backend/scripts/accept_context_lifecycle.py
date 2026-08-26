"""Deterministic acceptance harness for context compaction and fidelity."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.domains.context_lifecycle import ContextService
from app.domains.conversations.repository import ConversationRepository


PROTECTED = ["2026年", "20万元", "2项", "不得使用年龄", "地区待核实"]


def _scenario(message_count: int, repetitions: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        repository = ConversationRepository(Path(directory) / "acceptance.db")
        conversation = repository.create_conversation("context-acceptance")
        for index in range(message_count):
            facts = "2026年；预算上限20万元；最终最多2项；不得使用年龄；地区待核实。" if index == 0 else ""
            body = facts + f"第{index}轮：" + ("政策核对、文件处理和审批进度记录。" * repetitions)
            repository.add_message(
                conversation.id,
                "user" if index % 2 == 0 else "assistant",
                body,
            )
        prepared = ContextService(repository, max_chars=36_000).assemble(conversation.id)
        injected_text = prepared.compact_summary + "\n" + "\n".join(
            str(item.get("content") or "") for item in prepared.messages
        )
        return {
            "stored_message_count": len(repository.list_messages(conversation.id, limit=1_000)),
            "raw_chars": prepared.raw_chars,
            "injected_chars": prepared.injected_chars,
            "reduction_pct": round((1 - prepared.injected_chars / prepared.raw_chars) * 100, 1),
            "compaction_applied": prepared.compaction_applied,
            "injected_messages": len(prepared.messages),
            "protected_facts": {fact: fact in injected_text for fact in PROTECTED},
            "fidelity_status": prepared.fidelity_status,
        }


def _rolling_scenario() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        repository = ConversationRepository(Path(directory) / "rolling.db")
        conversation = repository.create_conversation("context-rolling-acceptance")
        for index in range(120):
            facts = "2026年；预算上限20万元；最终最多2项；不得使用年龄；地区待核实。" if index == 0 else ""
            repository.add_message(
                conversation.id, "user" if index % 2 == 0 else "assistant",
                facts + f"第{index}轮：" + ("政策核对、文件处理和审批进度记录。" * 22),
            )
        service = ContextService(repository, max_chars=36_000)
        first = service.assemble(conversation.id)
        for index in range(120, 300):
            change = "预算上限调整为25万元，以最新为准。" if index == 150 else ""
            repository.add_message(
                conversation.id, "user" if index % 2 == 0 else "assistant",
                change + f"第{index}轮：" + ("政策核对、文件处理和审批进度记录。" * 22),
            )
        second = service.assemble(conversation.id)
        latest = service._find_summary_by_id(second.summary_id, conversation.id)
        restored = ContextService(repository, max_chars=36_000).hydrate(service.to_checkpoint(second))
        return {
            "stored_message_count": 300,
            "summary_versions": latest.version if latest else 0,
            "parent_linked": bool(latest and latest.parent_summary_id == first.summary_id),
            "covered_message_count": latest.covered_message_count if latest else 0,
            "protected_facts": {fact: fact in restored.compact_summary for fact in PROTECTED},
            "supersession_preserved": "预算上限调整为25万元" in restored.compact_summary,
            "restart_hydration": restored.snapshot_id == second.snapshot_id,
        }


def main() -> None:
    print(json.dumps({
        "short_conversation": _scenario(12, 1),
        "long_conversation": _scenario(30, 45),
        "overflow_conversation": _scenario(120, 12),
        "rolling_300_conversation": _rolling_scenario(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
