from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.domains.conversations.contracts import MessageRecord
from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal
from app.models.gateway import ModelGateway, ModelGatewayError, parse_json_object

from .contracts import ContextSummaryRecord, PreparedContext, ProtectedFactLedger
from .semantic_facts import SemanticFact, SemanticFactChange, SemanticFactStore


_CONSTRAINT_MARKERS = (
    "必须", "不得", "禁止", "只能", "至少", "最多", "优先", "加分", "最终",
    "确认", "待核实", "未完成", "失败", "不要", "需要", "要求",
)
_EXACT_TOKEN_RE = re.compile(
    r"(?:20\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?|\d+(?:\.\d+)?%|"
    r"[¥￥]?\d+(?:\.\d+)?(?:元|万元|人|名|份|天|小时|分钟|轮|个|项))"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContextLifecycleModule:
    """Own context selection, versioned compaction, fidelity checks and snapshots."""

    def __init__(
        self,
        repository: ConversationRepository,
        max_chars: int = 36_000,
        *,
        model_gateway: ModelGateway | None = None,
        model_name: str = "qwen3.7-flash",
        recent_messages: int = 4,
        trigger_ratio: float = 0.65,
        minimum_messages: int = 12,
        minimum_reduction_ratio: float = 0.20,
    ) -> None:
        self.repository = repository
        self.database_path = Path(repository.database_path)
        self.max_chars = max(2_000, max_chars)
        self.model_gateway = model_gateway
        self.model_name = model_name
        self.recent_messages = max(2, recent_messages)
        self.trigger_ratio = min(0.9, max(0.25, trigger_ratio))
        self.minimum_messages = max(self.recent_messages + 2, minimum_messages)
        self.minimum_reduction_ratio = min(0.8, max(0.05, minimum_reduction_ratio))
        self._initialize()
        self.semantic_facts = SemanticFactStore(repository)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS context_summaries (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    source_message_ids_json TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    protected_facts_json TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    model_name TEXT NOT NULL DEFAULT '',
                    fidelity_status TEXT NOT NULL,
                    fidelity_issues_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    UNIQUE(conversation_id, version),
                    UNIQUE(conversation_id, source_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_context_summaries_conversation
                    ON context_summaries(conversation_id, version DESC);
                CREATE TABLE IF NOT EXISTS context_snapshots (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    summary_id TEXT REFERENCES context_summaries(id) ON DELETE SET NULL,
                    message_ids_json TEXT NOT NULL,
                    attachment_ids_json TEXT NOT NULL,
                    artifact_ids_json TEXT NOT NULL,
                    raw_chars INTEGER NOT NULL,
                    injected_chars INTEGER NOT NULL,
                    estimated_tokens INTEGER NOT NULL,
                    strategy TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    resource_query TEXT NOT NULL DEFAULT '',
                    hydration_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_context_snapshots_conversation
                    ON context_snapshots(conversation_id, created_at DESC);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(context_summaries)")}
            migrations = {
                "parent_summary_id": "ALTER TABLE context_summaries ADD COLUMN parent_summary_id TEXT NOT NULL DEFAULT ''",
                "covered_message_count": "ALTER TABLE context_summaries ADD COLUMN covered_message_count INTEGER NOT NULL DEFAULT 0",
                "covered_through_message_id": "ALTER TABLE context_summaries ADD COLUMN covered_through_message_id TEXT NOT NULL DEFAULT ''",
                "input_chars": "ALTER TABLE context_summaries ADD COLUMN input_chars INTEGER NOT NULL DEFAULT 0",
                "output_chars": "ALTER TABLE context_summaries ADD COLUMN output_chars INTEGER NOT NULL DEFAULT 0",
            }
            for name, statement in migrations.items():
                if name not in columns:
                    db.execute(statement)
            snapshot_columns = {row["name"] for row in db.execute("PRAGMA table_info(context_snapshots)")}
            if "resource_query" not in snapshot_columns:
                db.execute("ALTER TABLE context_snapshots ADD COLUMN resource_query TEXT NOT NULL DEFAULT ''")
            if "hydration_count" not in snapshot_columns:
                db.execute("ALTER TABLE context_snapshots ADD COLUMN hydration_count INTEGER NOT NULL DEFAULT 0")
            if "semantic_fact_ids_json" not in snapshot_columns:
                db.execute("ALTER TABLE context_snapshots ADD COLUMN semantic_fact_ids_json TEXT NOT NULL DEFAULT '[]'")

    def capture_semantic_facts(
        self,
        conversation_id: str,
        source_message_id: str,
        content: str,
        task_brief: dict[str, Any] | None = None,
        principal: Principal | None = None,
    ) -> list[SemanticFactChange]:
        return self.semantic_facts.capture(
            conversation_id, source_message_id, content, task_brief, principal,
        )

    def list_semantic_facts(
        self,
        conversation_id: str,
        principal: Principal | None = None,
        *,
        include_history: bool = False,
    ) -> list[SemanticFact]:
        if include_history:
            return self.semantic_facts.list_history(conversation_id, principal)
        return self.semantic_facts.list_current(conversation_id, principal)

    def list_pending_semantic_facts(
        self,
        conversation_id: str,
        principal: Principal | None = None,
    ) -> list[SemanticFact]:
        return self.semantic_facts.list_pending(conversation_id, principal)

    def resolve_semantic_fact(
        self,
        fact_id: str,
        approved: bool,
        principal: Principal | None = None,
    ) -> SemanticFact:
        return self.semantic_facts.resolve(fact_id, approved, principal)

    def assemble(
        self,
        conversation_id: str,
        principal: Principal | None = None,
        *,
        query: str = "",
    ) -> PreparedContext:
        self.repository.get_conversation(conversation_id, principal)
        previous = self._find_latest_summary(conversation_id)
        records = self._load_messages_after(
            conversation_id,
            previous.covered_through_message_id if previous else "",
            principal=principal,
        )
        attachments = self.repository.list_attachments(conversation_id, principal=principal)
        artifacts = self.repository.list_artifacts(conversation_id, principal=principal)
        semantic_facts = self.semantic_facts.list_current(conversation_id, principal)
        semantic_fact_text = self._render_semantic_facts(semantic_facts)
        raw_chars = (previous.input_chars if previous else 0) + sum(len(item.content) for item in records)
        summary = self.compact(conversation_id, principal=principal, records=records)

        if summary is not None:
            active_records = self._load_messages_after(
                conversation_id,
                summary.covered_through_message_id,
                principal=principal,
            )
        else:
            active_records = records

        if summary is None:
            message_budget = max(1_000, self.max_chars // 2 - len(semantic_fact_text))
            messages = self._bounded_messages(active_records, message_budget)
            compact_summary = "；".join(
                f"{item['role']}:{str(item['content'])[:160]}" for item in messages[-6:]
            )[:4_000]
            if semantic_fact_text:
                messages = [{"role": "system", "content": semantic_fact_text}, *messages]
                compact_summary = f"{semantic_fact_text}\n{compact_summary}"[:4_000]
            fidelity_status = "not_required"
        else:
            checkpoint = self._render_checkpoint(summary)
            if semantic_fact_text:
                checkpoint = f"{checkpoint}\n{semantic_fact_text}"
            raw_budget = max(1_000, self.max_chars - len(checkpoint))
            bounded_active = self._bounded_messages(active_records, raw_budget)
            messages = [
                {"role": "system", "content": checkpoint},
                *bounded_active,
            ]
            compact_summary = self._render_active_summary(summary, active_records)[:8_000]
            fidelity_status = summary.fidelity_status

        message_chars = sum(len(str(item.get("content", ""))) for item in messages)
        attachment_budget = max(0, self.max_chars - message_chars)
        attachment_text, usage = self._bounded_attachments(attachments, attachment_budget, query=query)
        injected_chars = message_chars + len(attachment_text)
        snapshot_id = f"ctxsnap_{uuid4().hex}"
        content_hash = hashlib.sha256(
            (compact_summary + "|" + "|".join(item.id for item in attachments)).encode("utf-8")
        ).hexdigest()
        with self._connect() as db:
            db.execute(
                """INSERT INTO context_snapshots(
                id,conversation_id,summary_id,message_ids_json,attachment_ids_json,
                artifact_ids_json,raw_chars,injected_chars,estimated_tokens,strategy,content_hash,
                resource_query,semantic_fact_ids_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot_id, conversation_id, summary.id if summary else None,
                    json.dumps([item.id for item in active_records], ensure_ascii=False),
                    json.dumps([item.id for item in attachments], ensure_ascii=False),
                    json.dumps([item.id for item in artifacts], ensure_ascii=False),
                    raw_chars, injected_chars, self._estimate_tokens(injected_chars),
                    "checkpoint_plus_recent" if summary else "bounded_recent",
                    content_hash, query.strip()[:1_000],
                    json.dumps([item.id for item in semantic_facts], ensure_ascii=False), _now(),
                ),
            )
        usage.insert(0, {
            "kind": "context_snapshot",
            "label": snapshot_id,
            "purpose": "versioned_compaction" if summary else "bounded_recent",
        })
        return PreparedContext(
            messages=messages,
            attachments=attachments,
            attachment_text=attachment_text,
            usage=usage,
            compact_summary=compact_summary,
            snapshot_id=snapshot_id,
            summary_id=summary.id if summary else "",
            compaction_applied=summary is not None,
            raw_chars=raw_chars,
            injected_chars=injected_chars,
            estimated_tokens=self._estimate_tokens(injected_chars),
            fidelity_status=fidelity_status,
            semantic_facts=semantic_facts,
        )

    @staticmethod
    def to_checkpoint(prepared: PreparedContext) -> dict[str, Any]:
        """Return a JSON-safe, reference-only payload for durable graph state."""
        if not prepared.snapshot_id:
            raise ValueError("context_snapshot_required")
        return {
            "schema_version": "context_ref_v1",
            "snapshot_id": prepared.snapshot_id,
            "summary_id": prepared.summary_id,
            "compaction_applied": prepared.compaction_applied,
            "raw_chars": prepared.raw_chars,
            "injected_chars": prepared.injected_chars,
            "estimated_tokens": prepared.estimated_tokens,
            "fidelity_status": prepared.fidelity_status,
        }

    def hydrate(self, payload: dict[str, Any], principal: Principal | None = None) -> PreparedContext:
        """Resolve a reference-only checkpoint into bounded execution context."""
        snapshot_id = str(payload.get("snapshot_id") or "")
        if not snapshot_id:
            raise ValueError("context_snapshot_required")
        snapshot = self._snapshot_row(snapshot_id, principal)
        with self._connect() as db:
            db.execute(
                "UPDATE context_snapshots SET hydration_count=hydration_count+1 WHERE id=?",
                (snapshot_id,),
            )
        conversation_id = str(snapshot["conversation_id"])
        message_ids = set(json.loads(snapshot["message_ids_json"]))
        attachment_ids = set(json.loads(snapshot["attachment_ids_json"]))
        artifact_ids = list(json.loads(snapshot["artifact_ids_json"]))
        keys = set(snapshot.keys())
        fact_ids = json.loads(snapshot["semantic_fact_ids_json"]) if "semantic_fact_ids_json" in keys else []
        semantic_facts = self.semantic_facts.get_by_ids(conversation_id, fact_ids)
        semantic_fact_text = self._render_semantic_facts(semantic_facts)
        records = [
            item for item in self._load_messages_after(conversation_id, principal=principal)
            if item.id in message_ids
        ]
        attachments = [
            item for item in self.repository.list_attachments(conversation_id, principal=principal)
            if item.id in attachment_ids
        ]
        summary = self._find_summary_by_id(str(snapshot["summary_id"] or ""), conversation_id)
        if summary is None:
            messages = self._bounded_messages(records, max(1_000, self.max_chars // 2 - len(semantic_fact_text)))
            compact_summary = "；".join(
                f"{item['role']}:{str(item['content'])[:160]}" for item in messages[-6:]
            )[:4_000]
            if semantic_fact_text:
                messages = [{"role": "system", "content": semantic_fact_text}, *messages]
                compact_summary = f"{semantic_fact_text}\n{compact_summary}"[:4_000]
            fidelity_status = "not_required"
        else:
            checkpoint = self._render_checkpoint(summary)
            if semantic_fact_text:
                checkpoint = f"{checkpoint}\n{semantic_fact_text}"
            messages = [
                {"role": "system", "content": checkpoint},
                *[{"role": item.role, "content": item.content.strip()} for item in records],
            ]
            compact_summary = self._render_active_summary(summary, records)[:8_000]
            fidelity_status = summary.fidelity_status
        message_chars = sum(len(str(item.get("content", ""))) for item in messages)
        attachment_text, usage = self._bounded_attachments(
            attachments, max(0, self.max_chars - message_chars),
            query=str(snapshot["resource_query"] or ""),
        )
        usage.insert(0, {
            "kind": "context_snapshot",
            "label": snapshot_id,
            "purpose": "lazy_hydration",
        })
        usage.extend({
            "kind": "artifact",
            "label": artifact_id,
            "purpose": "workspace_reference",
        } for artifact_id in artifact_ids)
        return PreparedContext(
            messages=messages,
            attachments=attachments,
            attachment_text=attachment_text,
            usage=usage,
            compact_summary=compact_summary,
            snapshot_id=snapshot_id,
            summary_id=str(snapshot["summary_id"] or ""),
            compaction_applied=bool(snapshot["summary_id"]),
            raw_chars=int(snapshot["raw_chars"]),
            injected_chars=int(snapshot["injected_chars"]),
            estimated_tokens=int(snapshot["estimated_tokens"]),
            fidelity_status=fidelity_status,
            semantic_facts=semantic_facts,
        )

    def compact(
        self,
        conversation_id: str,
        principal: Principal | None = None,
        *,
        force: bool = False,
        records: list[MessageRecord] | None = None,
    ) -> ContextSummaryRecord | None:
        self.repository.get_conversation(conversation_id, principal)
        parent = self._find_latest_summary(conversation_id)
        records = records if records is not None else self._load_messages_after(
            conversation_id,
            parent.covered_through_message_id if parent else "",
            principal=principal,
        )
        active_chars = sum(len(item.content) for item in records)
        current_injected = (len(self._render_checkpoint(parent)) if parent else 0) + active_chars
        trigger_chars = min(int(self.max_chars * self.trigger_ratio), 12_000)
        if not force and current_injected < trigger_chars:
            return parent
        stable = records[:-self.recent_messages]
        if not stable:
            return parent
        source_hash = self._source_hash(stable, parent.id if parent else "")
        existing = self._find_summary(conversation_id, source_hash)
        if existing is not None:
            return existing

        delta_ledger = self._extract_ledger(stable)
        ledger = self._merge_ledgers(parent.protected_facts if parent else None, delta_ledger)
        deterministic = self._deterministic_summary(stable, ledger, parent)
        summary_text = deterministic
        strategy = "deterministic"
        model_used = ""
        fidelity_status = "passed"
        issues: list[str] = []
        if self.model_gateway is not None:
            try:
                candidate, model_used = self._model_summary(stable, ledger, parent)
                issues = self._fidelity_issues(candidate, stable, ledger, parent)
                if issues:
                    fidelity_status = "fallback"
                else:
                    summary_text = candidate
                    strategy = "model_structured"
            except (ModelGatewayError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                fidelity_status = "fallback"
                issues = ["model_compaction_unavailable"]

        covered_count = (parent.covered_message_count if parent else 0) + len(stable)
        input_chars = (parent.input_chars if parent else 0) + sum(len(item.content) for item in stable)
        preview = ContextSummaryRecord(
            id="preview", conversation_id=conversation_id,
            version=(parent.version + 1 if parent else 1),
            source_message_ids=[item.id for item in stable], source_hash=source_hash,
            summary=summary_text, protected_facts=ledger, strategy=strategy,
            model_name=model_used, fidelity_status=fidelity_status,
            fidelity_issues=issues, created_at=_now(),
            parent_summary_id=parent.id if parent else "",
            covered_message_count=covered_count,
            covered_through_message_id=stable[-1].id,
            input_chars=input_chars,
        )
        output_chars = len(self._render_checkpoint(preview))
        candidate_injected = output_chars + sum(len(item.content) for item in records[-self.recent_messages:])
        if not force and candidate_injected > current_injected * (1 - self.minimum_reduction_ratio):
            return parent

        with self._connect() as db:
            version = int(db.execute(
                "SELECT COALESCE(MAX(version),0)+1 AS version FROM context_summaries WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()["version"])
            record_id = f"ctxsum_{uuid4().hex}"
            db.execute(
                """INSERT INTO context_summaries(
                id,conversation_id,version,source_message_ids_json,source_hash,summary,
                protected_facts_json,strategy,model_name,fidelity_status,fidelity_issues_json,created_at,
                parent_summary_id,covered_message_count,covered_through_message_id,input_chars,output_chars
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record_id, conversation_id, version,
                    json.dumps([item.id for item in stable], ensure_ascii=False), source_hash,
                    summary_text, json.dumps(ledger.as_dict(), ensure_ascii=False), strategy,
                    model_used, fidelity_status, json.dumps(issues, ensure_ascii=False), _now(),
                    parent.id if parent else "", covered_count, stable[-1].id,
                    input_chars, output_chars,
                ),
            )
        return self._find_summary(conversation_id, source_hash)

    def explain_usage(self, snapshot_id: str, principal: Principal | None = None) -> dict[str, Any]:
        row = self._snapshot_row(snapshot_id, principal)
        return {
            "snapshot_id": row["id"],
            "summary_id": row["summary_id"] or "",
            "message_ids": json.loads(row["message_ids_json"]),
            "attachment_ids": json.loads(row["attachment_ids_json"]),
            "artifact_ids": json.loads(row["artifact_ids_json"]),
            "semantic_fact_ids": json.loads(row["semantic_fact_ids_json"]) if "semantic_fact_ids_json" in set(row.keys()) else [],
            "raw_chars": row["raw_chars"],
            "injected_chars": row["injected_chars"],
            "estimated_tokens": row["estimated_tokens"],
            "strategy": row["strategy"],
            "hydration_count": int(row["hydration_count"]),
        }

    @staticmethod
    def _render_semantic_facts(facts: list[SemanticFact]) -> str:
        if not facts:
            return ""
        rows = [
            f"- {item.label}({item.slot_key}) = {item.display_value} "
            f"[v{item.version}; source={item.source_message_id}]"
            for item in facts
        ]
        return "[当前有效业务事实槽；新版本已替代旧值]\n" + "\n".join(rows)

    def _snapshot_row(self, snapshot_id: str, principal: Principal | None) -> sqlite3.Row:
        tenant_id, owner_user_id = self.repository._scope(principal)
        with self._connect() as db:
            row = db.execute(
                """SELECT s.* FROM context_snapshots s
                JOIN conversations c ON c.id=s.conversation_id
                WHERE s.id=? AND c.tenant_id=? AND c.owner_user_id=?""",
                (snapshot_id, tenant_id, owner_user_id),
            ).fetchone()
        if row is None:
            raise KeyError(snapshot_id)
        return row

    def _load_messages_after(
        self,
        conversation_id: str,
        after_message_id: str = "",
        principal: Principal | None = None,
    ) -> list[MessageRecord]:
        """Page through new messages without imposing a hidden history window."""
        result: list[MessageRecord] = []
        cursor = after_message_id
        while True:
            page = self.repository.list_messages_after(
                conversation_id, cursor, limit=500, principal=principal,
            )
            result.extend(page)
            if len(page) < 500:
                return result
            cursor = page[-1].id

    def _model_summary(
        self,
        records: list[MessageRecord],
        ledger: ProtectedFactLedger,
        parent: ContextSummaryRecord | None = None,
    ) -> tuple[str, str]:
        source = "\n".join(f"[{item.id}] {item.role}: {item.content}" for item in records)
        if parent is not None:
            source = f"[previous_checkpoint]\n{parent.summary}\n[new_messages]\n{source}"
        source = source[-24_000:]
        reply = self.model_gateway.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "你是上下文压缩器，不做业务决策。仅依据原文返回 JSON："
                        "task_goal, locked_constraints, completed_steps, confirmed_decisions, "
                        "open_questions, failures, next_step。不得发明日期、金额、人数或约束。"
                    ),
                },
                {"role": "user", "content": f"受保护事实：{json.dumps(ledger.as_dict(), ensure_ascii=False)}\n原文：\n{source}"},
            ],
            purpose="context_compaction",
            model=self.model_name,
            structured=True,
            max_tokens=900,
        )
        payload = parse_json_object(reply.content)
        allowed = (
            "task_goal", "locked_constraints", "completed_steps", "confirmed_decisions",
            "open_questions", "failures", "next_step",
        )
        lines = []
        for key in allowed:
            value = payload.get(key)
            if value in (None, "", []):
                continue
            text = "；".join(str(item) for item in value) if isinstance(value, list) else str(value)
            lines.append(f"{key}: {text}")
        if not lines:
            raise ValueError("empty_compaction")
        return "\n".join(lines)[:6_000], reply.model

    @staticmethod
    def _extract_ledger(records: list[MessageRecord]) -> ProtectedFactLedger:
        objectives: list[str] = []
        constraints: list[str] = []
        tokens: list[str] = []
        supersessions: list[str] = []
        source_ids: list[str] = []
        for item in records:
            source_ids.append(item.id)
            brief = item.task_brief or {}
            objective = str(brief.get("objective") or "").strip()
            if objective:
                objectives.append(objective[:500])
            for field in ("constraints", "deliverables", "locked_constraints"):
                value = brief.get(field) or []
                if isinstance(value, list):
                    constraints.extend(str(entry).strip()[:500] for entry in value if str(entry).strip())
            for sentence in re.split(r"[。！？!?\n]", item.content):
                clean = sentence.strip()
                if clean and any(marker in clean for marker in _CONSTRAINT_MARKERS):
                    constraints.append(clean[:500])
                if clean and any(marker in clean for marker in (
                    "改为", "调整为", "更新为", "替换为", "不再", "取消", "以此为准", "以最新为准",
                )):
                    supersessions.append(clean[:500])
            tokens.extend(_EXACT_TOKEN_RE.findall(item.content))
        return ProtectedFactLedger(
            objectives=list(dict.fromkeys(objectives))[-12:],
            hard_constraints=list(dict.fromkeys(constraints))[-40:],
            exact_tokens=list(dict.fromkeys(tokens))[-80:],
            supersessions=list(dict.fromkeys(supersessions))[-30:],
            source_message_ids=source_ids,
        )

    @staticmethod
    def _merge_ledgers(
        previous: ProtectedFactLedger | None,
        current: ProtectedFactLedger,
    ) -> ProtectedFactLedger:
        if previous is None:
            return current

        def union(left: list[str], right: list[str], limit: int) -> list[str]:
            return list(dict.fromkeys([*left, *right]))[-limit:]

        return ProtectedFactLedger(
            objectives=union(previous.objectives, current.objectives, 24),
            hard_constraints=union(previous.hard_constraints, current.hard_constraints, 80),
            exact_tokens=union(previous.exact_tokens, current.exact_tokens, 160),
            supersessions=union(previous.supersessions, current.supersessions, 60),
            source_message_ids=union(previous.source_message_ids, current.source_message_ids, 500),
        )

    @staticmethod
    def _deterministic_summary(
        records: list[MessageRecord],
        ledger: ProtectedFactLedger,
        parent: ContextSummaryRecord | None = None,
    ) -> str:
        recent = records[-8:]
        lines = ["较早对话的确定性检查点："]
        if parent is not None:
            lines.append("上一检查点：" + parent.summary[:3_000])
        if ledger.objectives:
            lines.append("任务目标：" + "；".join(ledger.objectives))
        if ledger.hard_constraints:
            lines.append("锁定约束：" + "；".join(ledger.hard_constraints))
        lines.append("近期进展：" + "；".join(f"{item.role}:{item.content[:240]}" for item in recent))
        return "\n".join(lines)[:6_000]

    @staticmethod
    def _fidelity_issues(
        candidate: str,
        records: list[MessageRecord],
        ledger: ProtectedFactLedger,
        parent: ContextSummaryRecord | None = None,
    ) -> list[str]:
        if not candidate.strip():
            return ["empty_summary"]
        source = "\n".join(item.content for item in records)
        if parent is not None:
            source = parent.summary + "\n" + "\n".join(parent.protected_facts.exact_tokens) + "\n" + source
        source_tokens = set(_EXACT_TOKEN_RE.findall(source))
        invented = set(_EXACT_TOKEN_RE.findall(candidate)) - source_tokens
        issues = [f"invented_critical_token:{token}" for token in sorted(invented)]
        if len(candidate) > 6_000:
            issues.append("summary_too_long")
        if not ledger.source_message_ids:
            issues.append("missing_provenance")
        if parent is not None:
            prior = parent.protected_facts
            for name in ("objectives", "hard_constraints", "exact_tokens", "supersessions"):
                if not set(getattr(prior, name)).issubset(set(getattr(ledger, name))):
                    issues.append(f"protected_fact_regression:{name}")
        return issues

    @staticmethod
    def _render_checkpoint(record: ContextSummaryRecord) -> str:
        ledger = record.protected_facts
        return (
            f"[较早对话检查点 v{record.version}，来源可追溯]\n{record.summary}\n"
            f"[受保护目标]\n{'；'.join(ledger.objectives) or '无'}\n"
            f"[受保护约束]\n{'；'.join(ledger.hard_constraints) or '无'}\n"
            f"[精确值]\n{'；'.join(ledger.exact_tokens) or '无'}\n"
            f"[事实变更（如与旧信息冲突，以这里较新的明确表述为准）]\n"
            f"{'；'.join(ledger.supersessions) or '无'}"
        )

    def _render_active_summary(self, record: ContextSummaryRecord, recent: list[MessageRecord]) -> str:
        recent_text = "；".join(f"{item.role}:{item.content[:200]}" for item in recent)
        return f"{self._render_checkpoint(record)}\n[最近原文]\n{recent_text}"

    @staticmethod
    def _source_hash(records: list[MessageRecord], parent_summary_id: str = "") -> str:
        payload = parent_summary_id + "\n" + "\n".join(f"{item.id}|{item.role}|{item.content}|{json.dumps(item.task_brief, sort_keys=True, ensure_ascii=False)}" for item in records)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _find_summary(self, conversation_id: str, source_hash: str) -> ContextSummaryRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM context_summaries WHERE conversation_id=? AND source_hash=?",
                (conversation_id, source_hash),
            ).fetchone()
        if row is None:
            return None
        return self._summary_from_row(row)

    def _find_summary_by_id(self, summary_id: str, conversation_id: str) -> ContextSummaryRecord | None:
        if not summary_id:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM context_summaries WHERE id=? AND conversation_id=?",
                (summary_id, conversation_id),
            ).fetchone()
        return self._summary_from_row(row) if row is not None else None

    def _find_latest_summary(self, conversation_id: str) -> ContextSummaryRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM context_summaries WHERE conversation_id=? ORDER BY version DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return self._summary_from_row(row) if row is not None else None

    @staticmethod
    def _summary_from_row(row: sqlite3.Row) -> ContextSummaryRecord:
        facts = json.loads(row["protected_facts_json"])
        facts.setdefault("supersessions", [])
        keys = set(row.keys())
        source_ids = json.loads(row["source_message_ids_json"])
        return ContextSummaryRecord(
            id=row["id"], conversation_id=row["conversation_id"], version=row["version"],
            source_message_ids=json.loads(row["source_message_ids_json"]), source_hash=row["source_hash"],
            summary=row["summary"], protected_facts=ProtectedFactLedger(**facts),
            strategy=row["strategy"], model_name=row["model_name"], fidelity_status=row["fidelity_status"],
            fidelity_issues=json.loads(row["fidelity_issues_json"]), created_at=row["created_at"],
            parent_summary_id=row["parent_summary_id"] if "parent_summary_id" in keys else "",
            covered_message_count=int(row["covered_message_count"]) if "covered_message_count" in keys else len(source_ids),
            covered_through_message_id=row["covered_through_message_id"] if "covered_through_message_id" in keys else (source_ids[-1] if source_ids else ""),
            input_chars=int(row["input_chars"]) if "input_chars" in keys else 0,
            output_chars=int(row["output_chars"]) if "output_chars" in keys else len(row["summary"]),
        )

    @staticmethod
    def _bounded_messages(records: list[MessageRecord], budget: int) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        used = 0
        for item in reversed(records):
            content = item.content.strip()
            if used + len(content) > budget:
                remaining = budget - used
                if remaining > 300:
                    content = content[-remaining:]
                else:
                    continue
            selected.append({"role": item.role, "content": content})
            used += len(content)
        return list(reversed(selected))

    @staticmethod
    def _bounded_attachments(
        attachments: list[Any],
        budget: int,
        *,
        query: str = "",
    ) -> tuple[str, list[dict[str, str]]]:
        if not attachments or budget <= 0:
            return "", []
        index_lines = ["[Resource Workspace 索引：需要精确内容时按 resource_id + locator 局部读取]"]
        for item in attachments:
            summary = item.summary or {}
            kind = str(summary.get("kind") or item.extension.lstrip(".") or "file")
            locator = (
                "page" if item.extension == ".pdf"
                else "section" if item.extension == ".docx"
                else "sheet/range" if item.extension == ".xlsx"
                else "line"
            )
            index_lines.append(f"- {item.id} | {item.display_name} | {kind} | locator={locator}")
        index_block = "\n".join(index_lines)
        usage: list[dict[str, str]] = [{
            "kind": "resource_index", "label": "workspace", "purpose": "progressive_disclosure",
        }]
        if len(index_block) >= budget:
            return index_block[:budget], usage

        blocks: list[str] = [index_block]
        used = len(index_block)
        terms = [term.casefold() for term in re.findall(r"[\w\u4e00-\u9fff]{2,}", query)][:20]

        def relevance(item: Any) -> tuple[int, str]:
            name = item.display_name.casefold()
            haystack = (
                item.display_name + " " + json.dumps(item.summary or {}, ensure_ascii=False)
                + " " + item.extracted_text[:4_000]
            ).casefold()
            score = sum(3 if term in name else 1 for term in terms if term in haystack)
            return score, item.created_at

        for item in sorted(attachments, key=relevance, reverse=True):
            if not item.extracted_text.strip():
                usage.append({"kind": "attachment", "label": item.display_name, "purpose": "multimodal_reference"})
                continue
            block = f"\n\n[资源摘录：{item.display_name} | resource_id={item.id}]\n{item.extracted_text}"
            if used + len(block) > budget:
                remaining = budget - used
                if remaining > 200:
                    blocks.append(block[:remaining])
                    usage.append({
                        "kind": "attachment", "label": item.display_name,
                        "purpose": "semantic_evidence" if terms else "task_evidence",
                    })
                break
            blocks.append(block)
            used += len(block)
            usage.append({
                "kind": "attachment", "label": item.display_name,
                "purpose": "semantic_evidence" if terms else "task_evidence",
            })
        return "".join(blocks), usage

    @staticmethod
    def _estimate_tokens(chars: int) -> int:
        return max(0, (chars + 1) // 2)


ContextService = ContextLifecycleModule
