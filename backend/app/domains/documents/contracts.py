from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domains.task_briefs import DocumentStyleSpec, TaskBrief


class FactStatus(StrEnum):
    SUPPORTED = "supported"
    MISSING = "missing"
    CONFLICTING = "conflicting"
    UNSUPPORTED = "unsupported"


class DocumentFact(BaseModel):
    key: str = Field(default="", max_length=80)
    label: str = Field(default="", max_length=120)
    value: str = Field(default="", max_length=2_000)
    source_name: str = Field(default="", max_length=260)
    evidence_excerpt: str = Field(default="", max_length=1_000)
    required: bool = False
    status: FactStatus = FactStatus.UNSUPPORTED

    @field_validator("key", "label", "value", "source_name", "evidence_excerpt")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return " ".join(str(value).split())


class DocumentSection(BaseModel):
    heading: str = Field(default="", max_length=300)
    paragraphs: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("heading")
    @classmethod
    def clean_heading(cls, value: str) -> str:
        return " ".join(str(value).split())

    @field_validator("paragraphs")
    @classmethod
    def clean_paragraphs(cls, values: list[str]) -> list[str]:
        return [" ".join(str(value).split())[:6_000] for value in values if str(value).strip()]


class DocumentTable(BaseModel):
    title: str = Field(default="", max_length=300)
    headers: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    rows: list[list[str]] = Field(default_factory=list, max_length=200)
    blank_rows: int = Field(default=0, ge=0, le=30)
    column_widths_cm: list[float] = Field(default_factory=list, max_length=20)
    alignment: str = Field(default="center", pattern="^(left|center|right)$")
    style: str = Field(default="table_grid", pattern="^(table_grid|light_grid)$")

    @model_validator(mode="before")
    @classmethod
    def normalize_model_table(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        raw_headers = payload.get("headers") or payload.get("columns") or payload.get("fields") or []
        headers = [
            str(item.get("label") or item.get("name") or item.get("title") or "")
            if isinstance(item, dict) else str(item)
            for item in raw_headers
        ] if isinstance(raw_headers, list) else []
        payload["headers"] = [item for item in headers if item.strip()]
        raw_rows = payload.get("rows") or payload.get("data") or []
        if isinstance(raw_rows, list):
            payload["rows"] = [
                [str(row.get(header, "")) for header in payload["headers"]]
                if isinstance(row, dict) else list(row) if isinstance(row, (list, tuple)) else [str(row)]
                for row in raw_rows
            ]
        else:
            payload["rows"] = []
        if "blank_rows" not in payload:
            payload["blank_rows"] = payload.get("blank_row_count", 3 if not payload["rows"] else 0)
        try:
            payload["blank_rows"] = int(payload.get("blank_rows") or 0)
        except (TypeError, ValueError):
            payload["blank_rows"] = 3 if not payload["rows"] else 0
        alignment = str(payload.get("alignment") or "center").casefold()
        payload["alignment"] = {
            "左对齐": "left", "居中": "center", "居中对齐": "center", "右对齐": "right",
        }.get(alignment, alignment if alignment in {"left", "center", "right"} else "center")
        style = str(payload.get("style") or "table_grid").casefold().replace(" ", "_")
        payload["style"] = "light_grid" if "light" in style or "浅色" in style else "table_grid"
        widths = payload.get("column_widths_cm") or payload.get("column_widths") or []
        payload["column_widths_cm"] = widths if isinstance(widths, list) else []
        return payload

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return " ".join(str(value).split())

    @field_validator("headers")
    @classmethod
    def clean_headers(cls, values: list[str]) -> list[str]:
        return [" ".join(str(value).split())[:120] for value in values if str(value).strip()]

    @field_validator("rows")
    @classmethod
    def clean_rows(cls, values: list[list[str]]) -> list[list[str]]:
        return [
            [" ".join(str(cell).split())[:2_000] for cell in row]
            for row in values
            if isinstance(row, list)
        ]


class DocumentBrief(BaseModel):
    document_type: str = Field(default="公文草稿", max_length=100)
    title: str = Field(default="", max_length=300)
    addressee: str = Field(default="", max_length=300)
    purpose: str = Field(default="", max_length=2_000)
    required_points: list[str] = Field(default_factory=list, max_length=30)
    prohibited_content: list[str] = Field(default_factory=list, max_length=30)
    issuer: str = Field(default="", max_length=300)
    date_text: str = Field(default="", max_length=100)
    tone: str = Field(default="正式、简洁、审慎", max_length=200)
    deliverables: list[str] = Field(default_factory=lambda: ["Word 文档", "内容与版式校验摘要"])
    required_fields: list[str] = Field(default_factory=lambda: ["title"])
    task_brief_version: str = "1.0"
    selected_skill_ids: list[str] = Field(default_factory=list)
    requires_table: bool = False
    required_table_headers: list[str] = Field(default_factory=list, max_length=20)
    required_blank_rows: int = Field(default=0, ge=0, le=30)

    @field_validator("required_points", "prohibited_content", "deliverables", "required_fields")
    @classmethod
    def clean_list(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = " ".join(str(value).split())[:500]
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned

    @classmethod
    def from_task_brief(cls, task_brief: TaskBrief, message: str) -> "DocumentBrief":
        mode = task_brief.mode_spec
        required_points = _string_list(
            mode.get("required_points") or mode.get("must_include") or mode.get("key_points")
        )
        if not required_points:
            required_points = list(task_brief.constraints)
        prohibited = _string_list(
            mode.get("prohibited_content") or mode.get("must_avoid") or mode.get("forbidden_content")
        )
        required_fields = _string_list(mode.get("required_fields")) or ["title"]
        table_context = "\n".join([message, task_brief.objective, *task_brief.constraints])
        table_headers = _string_list(mode.get("table_headers") or mode.get("required_table_headers"))
        if not table_headers:
            table_headers = _extract_table_headers(table_context)
        requires_table = bool(mode.get("requires_table")) or _requests_table(table_context)
        required_blank_rows = _extract_blank_rows(table_context)
        if mode.get("required_blank_rows") is not None:
            try:
                required_blank_rows = int(mode["required_blank_rows"])
            except (TypeError, ValueError):
                pass
        issuer = str(mode.get("issuer") or _extract_labelled_value(
            table_context, ("落款单位", "发文单位", "发布单位", "落款"),
        ))
        date_text = str(mode.get("date_text") or _extract_labelled_date(table_context))
        return cls(
            document_type=str(mode.get("document_type") or "公文草稿"),
            title=str(mode.get("title") or ""),
            addressee=str(mode.get("addressee") or ""),
            purpose=str(mode.get("purpose") or task_brief.objective or message),
            required_points=required_points,
            prohibited_content=prohibited,
            issuer=issuer,
            date_text=date_text,
            tone=str(mode.get("tone") or "正式、简洁、审慎"),
            deliverables=list(task_brief.deliverables) or ["Word 文档", "内容与版式校验摘要"],
            required_fields=required_fields,
            task_brief_version=task_brief.version,
            selected_skill_ids=list(task_brief.selected_skill_ids),
            requires_table=requires_table,
            required_table_headers=table_headers,
            required_blank_rows=required_blank_rows,
        )


class DocumentPlan(BaseModel):
    title: str = Field(default="", max_length=300)
    document_type: str = Field(default="公文草稿", max_length=100)
    addressee: str = Field(default="", max_length=300)
    lead: str = Field(default="", max_length=6_000)
    sections: list[DocumentSection] = Field(default_factory=list, max_length=40)
    tables: list[DocumentTable] = Field(default_factory=list, max_length=20)
    closing: str = Field(default="", max_length=6_000)
    issuer: str = Field(default="", max_length=300)
    date_text: str = Field(default="", max_length=100)
    facts: list[DocumentFact] = Field(default_factory=list, max_length=100)
    missing_fields: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def normalize_model_tables(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        tables = payload.get("tables")
        if tables is None and payload.get("table") is not None:
            tables = payload.get("table")
        if isinstance(tables, dict):
            tables = [tables]
        collected = list(tables) if isinstance(tables, list) else []
        for section in payload.get("sections") or []:
            if not isinstance(section, dict):
                continue
            nested = section.get("tables") or section.get("table")
            if isinstance(nested, dict):
                collected.append(nested)
            elif isinstance(nested, list):
                collected.extend(nested)
        payload["tables"] = collected
        return payload

    def full_text(self) -> str:
        parts = [self.title, self.addressee, self.lead]
        for section in self.sections:
            parts.extend([section.heading, *section.paragraphs])
        for table in self.tables:
            parts.extend([table.title, *table.headers])
            parts.extend(cell for row in table.rows for cell in row)
        parts.extend([self.closing, self.issuer, self.date_text])
        return "\n".join(part for part in parts if part.strip())


class EvidenceSource(BaseModel):
    name: str = Field(max_length=260)
    text: str = Field(default="", max_length=120_000)


class DocumentValidationReport(BaseModel):
    passed: bool
    status: str = Field(pattern="^(passed|warning|failed)$")
    checks: dict[str, bool] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    metrics: dict[str, int | float | str | bool] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class DocumentAgentRequest(BaseModel):
    message: str = Field(max_length=12_000)
    task_brief: TaskBrief
    evidence_sources: list[EvidenceSource] = Field(default_factory=list, max_length=30)
    skill_prompt: str = Field(default="", max_length=12_000)
    style: DocumentStyleSpec = Field(default_factory=DocumentStyleSpec)
    model: str = Field(default="", max_length=100)
    max_repairs: int = Field(default=2, ge=0, le=3)


class DocumentAgentResult(BaseModel):
    answer: str
    artifact_path: Path
    display_name: str
    plan: DocumentPlan
    validation: DocumentValidationReport
    metadata: dict[str, Any]
    trace: list[dict[str, Any]] = Field(default_factory=list)
    graph_path: list[str] = Field(default_factory=list)
    repair_count: int = 0


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace("；", "\n").splitlines() if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _requests_table(value: str) -> bool:
    compact = "".join(str(value).split())
    return any(token in compact for token in ("表格", "信息表", "确认表", "登记表", "统计表", "名单表"))


def _extract_table_headers(message: str) -> list[str]:
    compact = "".join(str(message).split())
    if not _requests_table(compact):
        return []
    explicit_candidates: list[list[str]] = []
    for explicit in re.finditer(
        r"(?:列为|列包括|字段为|字段包括|表头为|列名为|(?:\d+|[一二两三四五六七八九十]+)列表格)[“\"：:]?([^”\"。；]+)",
        compact,
    ):
        headers = [
            item.strip(" ，、,；;：:")
            for item in re.split(r"[、，,；;|]", re.split(r"(?:预留|留出)\d+行", explicit.group(1))[0])
            if item.strip(" ，、,；;：:")
        ]
        if 1 < len(headers) <= 20:
            explicit_candidates.append(headers)
    if explicit_candidates:
        # Rewritten context may contain a shortened copy before the complete locked
        # Task Brief. Prefer the richest explicit schema instead of the first match.
        return max(explicit_candidates, key=len)
    segment = compact
    table_index = max(segment.find("表格"), segment.find("信息表"), segment.find("确认表"))
    if table_index >= 0:
        segment = segment[table_index:]
    for stop in ("最后", "表格后", "表后", "并留下", "并预留"):
        if stop in segment:
            segment = segment.split(stop, 1)[0]
    aliases = [
        ("身份证号码", "身份证号码"), ("身份证号", "身份证号"), ("身份证", "身份证"),
        ("毕业院校", "毕业院校"), ("所属中心", "所属中心"), ("联系电话", "联系电话"),
        ("入职日期", "入职日期"), ("人员姓名", "姓名"), ("员工姓名", "姓名"),
        ("姓名", "姓名"), ("员工编号", "工号"), ("工号", "工号"),
        ("所属部门", "所属部门"), ("部门", "部门"), ("岗位名称", "岗位名称"),
        ("材料是否齐全", "材料是否齐全"), ("材料齐全情况", "材料齐全情况"),
        ("岗位", "岗位"), ("职级", "职级"), ("邮箱", "邮箱"),
    ]
    found: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []
    for token, label in aliases:
        start = segment.find(token)
        if start < 0 or any(left <= start < right for left, right in occupied):
            continue
        found.append((start, label))
        occupied.append((start, start + len(token)))
    return [label for _index, label in sorted(found) if label]


def _extract_blank_rows(message: str) -> int:
    compact = "".join(str(message).split())
    match = re.search(r"(?:预留|留出|保留)(\d{1,2})行(?:空白|可填写)?", compact)
    return min(int(match.group(1)), 30) if match else 0


def _extract_labelled_value(message: str, labels: tuple[str, ...]) -> str:
    pattern = "|".join(re.escape(item) for item in labels)
    match = re.search(rf"(?:{pattern})(?:为|是)?[：:]?([^，。；;\n]{{1,80}})", str(message))
    return match.group(1).strip() if match else ""


def _extract_labelled_date(message: str) -> str:
    match = re.search(
        r"(?:成文日期|发文日期|落款日期)(?:为|是)?[：:]?\s*"
        r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)",
        str(message),
    )
    return match.group(1).strip() if match else ""
