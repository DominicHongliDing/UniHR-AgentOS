from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domains.conversations.contracts import AttachmentRecord

from .semantic_facts import SemanticFact


@dataclass(frozen=True)
class ProtectedFactLedger:
    objectives: list[str] = field(default_factory=list)
    hard_constraints: list[str] = field(default_factory=list)
    exact_tokens: list[str] = field(default_factory=list)
    supersessions: list[str] = field(default_factory=list)
    source_message_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "objectives": self.objectives,
            "hard_constraints": self.hard_constraints,
            "exact_tokens": self.exact_tokens,
            "supersessions": self.supersessions,
            "source_message_ids": self.source_message_ids,
        }


@dataclass(frozen=True)
class ContextSummaryRecord:
    id: str
    conversation_id: str
    version: int
    source_message_ids: list[str]
    source_hash: str
    summary: str
    protected_facts: ProtectedFactLedger
    strategy: str
    model_name: str
    fidelity_status: str
    fidelity_issues: list[str]
    created_at: str
    parent_summary_id: str = ""
    covered_message_count: int = 0
    covered_through_message_id: str = ""
    input_chars: int = 0
    output_chars: int = 0


@dataclass(frozen=True)
class PreparedContext:
    messages: list[dict[str, Any]]
    attachments: list[AttachmentRecord]
    attachment_text: str
    usage: list[dict[str, str]]
    compact_summary: str
    snapshot_id: str = ""
    summary_id: str = ""
    compaction_applied: bool = False
    raw_chars: int = 0
    injected_chars: int = 0
    estimated_tokens: int = 0
    fidelity_status: str = "not_required"
    semantic_facts: list[SemanticFact] = field(default_factory=list)
