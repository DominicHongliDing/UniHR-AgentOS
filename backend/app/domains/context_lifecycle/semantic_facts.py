from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.domains.conversations.repository import ConversationRepository
from app.domains.identity import Principal


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SemanticFact(BaseModel):
    id: str
    conversation_id: str
    slot_key: str
    domain: str = "general"
    label: str
    value: Any = None
    display_value: str = ""
    version: int
    status: Literal[
        "active", "superseded", "tombstoned", "conflicted",
        "pending_confirmation", "rejected",
    ]
    source_message_id: str
    supersedes_id: str = ""
    extraction_method: str = "deterministic"
    confidence: float = 1.0
    created_at: str


class SemanticFactChange(BaseModel):
    slot_key: str
    action: Literal["created", "superseded", "tombstoned", "unchanged", "conflicted"]
    current: SemanticFact | None = None
    previous_id: str = ""


_SLOT_LABELS = {
    "target_year": "目标年度",
    "selection_count": "最终数量",
    "budget_limit": "预算上限",
    "region": "适用地区",
    "effective_date": "生效日期",
    "approval_status": "审批状态",
    "job_title": "岗位名称",
    "recruitment.must_haves": "必须条件",
    "recruitment.nice_to_haves": "加分项",
    "policy.topic": "政策主题",
    "policy.scope": "适用范围",
    "policy.as_of_date": "查询时点",
    "policy.knowledge_scope": "知识范围",
    "spreadsheet.goal": "表格目标",
    "spreadsheet.source_sheet": "源工作表",
    "spreadsheet.header_row": "表头行",
    "spreadsheet.mapping_notes": "字段映射",
    "spreadsheet.calculation_rules": "计算规则",
    "spreadsheet.filter_sort_rules": "筛选排序规则",
    "spreadsheet.data_validation_rules": "数据验证规则",
    "spreadsheet.summary_rules": "汇总规则",
    "spreadsheet.chart_type": "图表类型",
    "spreadsheet.header_fill": "表头颜色",
    "spreadsheet.horizontal_alignment": "水平对齐",
    "spreadsheet.freeze_header": "冻结表头",
    "spreadsheet.preserve_existing_style": "保留原样式",
    "document.document_type": "公文类型",
    "document.title": "公文标题",
    "document.addressee": "主送对象",
    "document.issuer": "发文单位",
    "document.date_text": "发文日期",
    "document.purpose": "发文目的",
    "document.required_points": "必须包含",
    "document.prohibited_content": "禁止内容",
    "document.tone": "行文语气",
    "document.body_font": "正文字体",
    "document.body_size_pt": "正文字号",
    "document.body_alignment": "正文对齐",
    "document.line_spacing": "行距",
}

_DOMAIN_SLOT_SCHEMAS: dict[str, dict[str, tuple[str, str]]] = {
    "recruitment": {
        "must_haves": ("recruitment.must_haves", "必须条件"),
        "nice_to_haves": ("recruitment.nice_to_haves", "加分项"),
    },
    "policy": {
        "topic": ("policy.topic", "政策主题"),
        "scope": ("policy.scope", "适用范围"),
        "as_of_date": ("policy.as_of_date", "查询时点"),
        "knowledge_scope": ("policy.knowledge_scope", "知识范围"),
    },
    "spreadsheet": {
        "goal": ("spreadsheet.goal", "表格目标"),
        "source_sheet": ("spreadsheet.source_sheet", "源工作表"),
        "header_row": ("spreadsheet.header_row", "表头行"),
        "mapping_notes": ("spreadsheet.mapping_notes", "字段映射"),
        "calculation_rules": ("spreadsheet.calculation_rules", "计算规则"),
        "filter_sort_rules": ("spreadsheet.filter_sort_rules", "筛选排序规则"),
        "data_validation_rules": ("spreadsheet.data_validation_rules", "数据验证规则"),
        "summary_rules": ("spreadsheet.summary_rules", "汇总规则"),
        "chart_type": ("spreadsheet.chart_type", "图表类型"),
    },
    "document": {
        "document_type": ("document.document_type", "公文类型"),
        "title": ("document.title", "公文标题"),
        "addressee": ("document.addressee", "主送对象"),
        "issuer": ("document.issuer", "发文单位"),
        "date_text": ("document.date_text", "发文日期"),
        "purpose": ("document.purpose", "发文目的"),
        "required_points": ("document.required_points", "必须包含"),
        "prohibited_content": ("document.prohibited_content", "禁止内容"),
        "tone": ("document.tone", "行文语气"),
    },
}

_STYLE_SLOT_SCHEMAS: dict[str, dict[str, tuple[str, str]]] = {
    "spreadsheet": {
        "target_sheet": ("spreadsheet.source_sheet", "目标工作表"),
        "header_row": ("spreadsheet.header_row", "表头行"),
        "header_fill": ("spreadsheet.header_fill", "表头颜色"),
        "horizontal_alignment": ("spreadsheet.horizontal_alignment", "水平对齐"),
        "freeze_header": ("spreadsheet.freeze_header", "冻结表头"),
        "preserve_existing_style": ("spreadsheet.preserve_existing_style", "保留原样式"),
    },
    "document": {
        "body_font": ("document.body_font", "正文字体"),
        "body_size_pt": ("document.body_size_pt", "正文字号"),
        "body_alignment": ("document.body_alignment", "正文对齐"),
        "line_spacing": ("document.line_spacing", "行距"),
    },
}

_MODE_SPEC_KEYS = {
    "target_year": ("target_year", "adjustment_year", "effective_year", "year"),
    "selection_count": ("selection_count", "candidate_count", "final_count", "shortlist_count"),
    "budget_limit": ("budget_limit", "max_budget", "budget"),
    "region": ("region", "location"),
    "effective_date": ("effective_date", "start_date"),
    "approval_status": ("approval_status", "review_status"),
    "job_title": ("job_title", "position_name", "role_name"),
}

_TEXT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "target_year": (
        re.compile(r"(?:目标|调整|适用|生成|改成|更新为|切换到)\s*(20\d{2})\s*年?"),
        re.compile(r"(20\d{2})\s*年度(?:调整|方案|表格|政策|任务)"),
    ),
    "selection_count": (
        re.compile(r"(?:最终|最后|进入后续|保留|选出|选择|推荐)\s*(?:最多|不超过)?\s*([一二两三四五六七八九十\d]+)\s*(?:人|名|份|个)"),
        re.compile(r"(?:人数|数量)\s*(?:改成|调整为|设为|是|为|[:：])\s*([一二两三四五六七八九十\d]+)"),
        re.compile(r"(?:改成|调整为|设为)\s*([一二两三四五六七八九十\d]+)\s*(?:人|名|份|个)"),
    ),
    "budget_limit": (
        re.compile(r"预算(?:上限)?\s*(?:改成|调整为|设为|是|为|[:：])?\s*([¥￥]?\s*\d+(?:\.\d+)?\s*(?:万元|万|元)?)"),
    ),
    "region": (
        re.compile(r"(?:适用地区|地区|工作地点|地点|区域|范围)\s*(?:改成|调整为|设为|是|为|[:：])\s*([\u4e00-\u9fff]{2,16})"),
    ),
    "effective_date": (
        re.compile(r"(?:生效日期|执行日期|从)\s*(?:改成|调整为|设为|是|为|[:：])?\s*(20\d{2}[年\-/\.][01]?\d(?:[月\-/\.][0-3]?\d日?)?)"),
    ),
    "job_title": (
        re.compile(r"(?:岗位名称|招聘岗位|职位)\s*(?:改成|调整为|设为|是|为|[:：])\s*([^，。；;\n]{2,40})"),
    ),
}

_TOMBSTONE_MARKERS = ("取消", "删除", "清除", "不再限制", "不设", "无需")
_CHINESE_NUMBERS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


class SemanticFactStore:
    """Versioned field-level facts with provenance, supersession and retraction."""

    def __init__(self, repository: ConversationRepository) -> None:
        self.repository = repository
        self.database_path = Path(repository.database_path)
        self._initialize()

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
                CREATE TABLE IF NOT EXISTS context_semantic_facts (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    slot_key TEXT NOT NULL,
                    domain TEXT NOT NULL DEFAULT 'general',
                    label TEXT NOT NULL,
                    value_json TEXT,
                    display_value TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    source_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    supersedes_id TEXT NOT NULL DEFAULT '',
                    extraction_method TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(conversation_id, slot_key, version)
                );
                CREATE INDEX IF NOT EXISTS idx_context_semantic_facts_current
                    ON context_semantic_facts(conversation_id, slot_key, status, version DESC);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(context_semantic_facts)")}
            if "domain" not in columns:
                db.execute("ALTER TABLE context_semantic_facts ADD COLUMN domain TEXT NOT NULL DEFAULT 'general'")

    def capture(
        self,
        conversation_id: str,
        source_message_id: str,
        content: str,
        task_brief: dict[str, Any] | None,
        principal: Principal | None = None,
    ) -> list[SemanticFactChange]:
        self.repository.get_conversation(conversation_id, principal)
        candidates = self._extract(content, task_brief or {})
        changes: list[SemanticFactChange] = []
        for slot_key, candidate in candidates.items():
            changes.append(self._write_candidate(
                conversation_id,
                source_message_id,
                slot_key,
                candidate["value"],
                candidate["display_value"],
                candidate["method"],
                candidate["confidence"],
                domain=str(candidate.get("domain") or "general"),
                tombstone=bool(candidate.get("tombstone")),
                conflict=bool(candidate.get("conflict")),
            ))
        return changes

    def list_current(
        self, conversation_id: str, principal: Principal | None = None
    ) -> list[SemanticFact]:
        self.repository.get_conversation(conversation_id, principal)
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM context_semantic_facts
                WHERE conversation_id=? AND status='active'
                ORDER BY slot_key""",
                (conversation_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_pending(
        self, conversation_id: str, principal: Principal | None = None
    ) -> list[SemanticFact]:
        self.repository.get_conversation(conversation_id, principal)
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM context_semantic_facts
                WHERE conversation_id=? AND status IN ('pending_confirmation','conflicted')
                ORDER BY created_at DESC""",
                (conversation_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def resolve(
        self,
        fact_id: str,
        approved: bool,
        principal: Principal | None = None,
    ) -> SemanticFact:
        with self._connect() as db:
            candidate = db.execute(
                "SELECT * FROM context_semantic_facts WHERE id=?", (fact_id,),
            ).fetchone()
        if candidate is None:
            raise KeyError(fact_id)
        conversation_id = str(candidate["conversation_id"])
        self.repository.get_conversation(conversation_id, principal)
        if candidate["status"] not in {"pending_confirmation", "conflicted"}:
            raise ValueError("semantic_fact_not_pending")
        with self._connect() as db:
            if approved:
                previous = db.execute(
                    """SELECT id FROM context_semantic_facts
                    WHERE conversation_id=? AND slot_key=? AND status='active' AND id<>?
                    ORDER BY version DESC LIMIT 1""",
                    (conversation_id, candidate["slot_key"], fact_id),
                ).fetchone()
                if previous is not None:
                    db.execute(
                        "UPDATE context_semantic_facts SET status='superseded' WHERE id=?",
                        (previous["id"],),
                    )
                    db.execute(
                        "UPDATE context_semantic_facts SET supersedes_id=? WHERE id=?",
                        (previous["id"], fact_id),
                    )
                db.execute(
                    "UPDATE context_semantic_facts SET status='active',confidence=1.0 WHERE id=?",
                    (fact_id,),
                )
            else:
                db.execute(
                    "UPDATE context_semantic_facts SET status='rejected' WHERE id=?",
                    (fact_id,),
                )
            row = db.execute(
                "SELECT * FROM context_semantic_facts WHERE id=?", (fact_id,),
            ).fetchone()
        return self._from_row(row)

    def list_history(
        self, conversation_id: str, principal: Principal | None = None
    ) -> list[SemanticFact]:
        self.repository.get_conversation(conversation_id, principal)
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM context_semantic_facts
                WHERE conversation_id=? ORDER BY slot_key,version DESC""",
                (conversation_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_ids(self, conversation_id: str, fact_ids: list[str]) -> list[SemanticFact]:
        if not fact_ids:
            return []
        placeholders = ",".join("?" for _ in fact_ids)
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT * FROM context_semantic_facts
                WHERE conversation_id=? AND id IN ({placeholders}) ORDER BY slot_key""",
                [conversation_id, *fact_ids],
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def _write_candidate(
        self,
        conversation_id: str,
        source_message_id: str,
        slot_key: str,
        value: Any,
        display_value: str,
        method: str,
        confidence: float,
        *,
        domain: str,
        tombstone: bool,
        conflict: bool,
    ) -> SemanticFactChange:
        with self._connect() as db:
            previous = db.execute(
                """SELECT * FROM context_semantic_facts
                WHERE conversation_id=? AND slot_key=? AND status='active'
                ORDER BY version DESC LIMIT 1""",
                (conversation_id, slot_key),
            ).fetchone()
            if (
                previous is not None
                and not tombstone
                and not conflict
                and json.loads(previous["value_json"]) == value
            ):
                return SemanticFactChange(
                    slot_key=slot_key,
                    action="unchanged",
                    current=self._from_row(previous),
                    previous_id=previous["id"],
                )

            version = int(db.execute(
                """SELECT COALESCE(MAX(version),0)+1 FROM context_semantic_facts
                WHERE conversation_id=? AND slot_key=?""",
                (conversation_id, slot_key),
            ).fetchone()[0])
            requires_confirmation = conflict or confidence < 0.80
            if previous is not None and not requires_confirmation:
                db.execute(
                    "UPDATE context_semantic_facts SET status='superseded' WHERE id=?",
                    (previous["id"],),
                )
            status = (
                "tombstoned" if tombstone else
                ("conflicted" if conflict else ("pending_confirmation" if requires_confirmation else "active"))
            )
            record_id = f"ctxfact_{uuid4().hex}"
            db.execute(
                """INSERT INTO context_semantic_facts(
                id,conversation_id,slot_key,domain,label,value_json,display_value,version,status,
                source_message_id,supersedes_id,extraction_method,confidence,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record_id, conversation_id, slot_key, domain,
                    _SLOT_LABELS.get(slot_key, slot_key),
                    None if tombstone else json.dumps(value, ensure_ascii=False),
                    "" if tombstone else display_value, version, status, source_message_id,
                    previous["id"] if previous is not None else "", method, confidence, _now(),
                ),
            )
            row = db.execute(
                "SELECT * FROM context_semantic_facts WHERE id=?", (record_id,)
            ).fetchone()
        return SemanticFactChange(
            slot_key=slot_key,
            action=(
                "tombstoned" if tombstone else
                ("conflicted" if conflict else ("superseded" if previous is not None else "created"))
            ),
            current=self._from_row(row),
            previous_id=previous["id"] if previous is not None else "",
        )

    @classmethod
    def _extract(cls, content: str, task_brief: dict[str, Any]) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        kind = str(task_brief.get("kind") or "general")
        mode_spec = task_brief.get("mode_spec") or {}
        domain_schema = _DOMAIN_SLOT_SCHEMAS.get(kind, {})
        if isinstance(mode_spec, dict):
            for source_key, (slot_key, _label) in domain_schema.items():
                value = mode_spec.get(source_key)
                if value not in (None, "", []):
                    normalized, display = cls._normalize(slot_key, value)
                    found[slot_key] = {
                        "value": normalized, "display_value": display,
                        "method": f"task_brief.mode_spec.{source_key}", "confidence": 1.0,
                        "domain": kind,
                    }
            for slot_key, keys in _MODE_SPEC_KEYS.items():
                for key in keys:
                    value = mode_spec.get(key)
                    if value not in (None, "", []):
                        normalized, display = cls._normalize(slot_key, value)
                        found[slot_key] = {
                            "value": normalized, "display_value": display,
                            "method": f"task_brief.mode_spec.{key}", "confidence": 1.0,
                            "domain": kind if slot_key in {"job_title", "selection_count", "target_year"} else "general",
                        }
                        break
        artifact_style = task_brief.get("artifact_style") or {}
        if isinstance(artifact_style, dict):
            for style_domain, schema in _STYLE_SLOT_SCHEMAS.items():
                style_values = artifact_style.get(style_domain) or {}
                if not isinstance(style_values, dict):
                    continue
                for source_key, (slot_key, _label) in schema.items():
                    value = style_values.get(source_key)
                    if value not in (None, "", []):
                        normalized, display = cls._normalize(slot_key, value)
                        found[slot_key] = {
                            "value": normalized, "display_value": display,
                            "method": f"task_brief.artifact_style.{style_domain}.{source_key}",
                            "confidence": 1.0, "domain": style_domain,
                        }

        clean = " ".join(content.split())
        for slot_key, label in _SLOT_LABELS.items():
            if any(f"{marker}{label}" in clean or f"{marker} {label}" in clean for marker in _TOMBSTONE_MARKERS):
                found[slot_key] = {
                    "value": None, "display_value": "", "method": "explicit_retraction",
                    "confidence": 1.0, "tombstone": True,
                    "domain": "general",
                }
        for slot_key, patterns in _TEXT_PATTERNS.items():
            matches: list[str] = []
            for pattern in patterns:
                matches.extend(match.group(1).strip() for match in pattern.finditer(clean))
            if not matches or found.get(slot_key, {}).get("tombstone"):
                continue
            normalized = [cls._normalize(slot_key, item) for item in matches]
            unique = list(dict.fromkeys(json.dumps(item[0], ensure_ascii=False, sort_keys=True) for item in normalized))
            value, display = normalized[-1]
            found[slot_key] = {
                "value": value, "display_value": display, "method": "message_regex",
                "confidence": 0.96 if len(unique) == 1 else 0.65,
                "conflict": len(unique) > 1,
                "domain": kind if slot_key in {"job_title", "selection_count", "target_year"} else "general",
            }
        return found

    @staticmethod
    def _normalize(slot_key: str, value: Any) -> tuple[Any, str]:
        raw = str(value).strip()
        if slot_key in {"target_year", "selection_count"}:
            number = _CHINESE_NUMBERS.get(raw, None)
            if number is None:
                match = re.search(r"\d+", raw)
                number = int(match.group()) if match else raw
            return number, f"{number}年" if slot_key == "target_year" else f"{number}名"
        if slot_key == "budget_limit":
            compact = raw.replace(" ", "").replace("￥", "¥")
            return compact, compact
        if isinstance(value, bool):
            return value, "是" if value else "否"
        if isinstance(value, (int, float)):
            return value, str(value)
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            return cleaned, "；".join(cleaned)
        return raw, raw

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SemanticFact:
        return SemanticFact(
            id=row["id"], conversation_id=row["conversation_id"], slot_key=row["slot_key"],
            domain=row["domain"] if "domain" in set(row.keys()) else "general",
            label=row["label"], value=json.loads(row["value_json"]) if row["value_json"] is not None else None,
            display_value=row["display_value"], version=int(row["version"]), status=row["status"],
            source_message_id=row["source_message_id"], supersedes_id=row["supersedes_id"],
            extraction_method=row["extraction_method"], confidence=float(row["confidence"]),
            created_at=row["created_at"],
        )
