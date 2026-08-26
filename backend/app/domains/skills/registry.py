from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from app.domains.identity import Principal
from app.domains.identity.repository import ADMIN_USER_ID

from .contracts import SkillDraft, SkillMatch, SkillRecord, SkillVersionRecord
from .harness import SkillHarness, merge_harness_metadata
from .matching import score_skill_match
from .orchestration import ensure_orchestration_metadata
from .skill_builder import refresh_package_metadata


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_BUILTIN_SKILLS = [
    SkillDraft(
        name="build-reusable-skill",
        display_name="沉淀为可复用 Skill",
        description="把一句话需求或一段已完成的人事工作对话整理为可校验、可预览、可版本化发布的 Agent Skill 草稿。",
        instructions=(
            "确认要沉淀的目标、典型输入和成功输出；只提取可复用步骤，不复制一次性业务事实或敏感信息；"
            "规划 scripts、references、assets；生成标准 SKILL.md 与 agents/openai.yaml；执行结构、触发、"
            "隐私、权限和输出校验；校验通过后仍保存为草稿，由用户复核并启用。"
        ),
        trigger_phrases=["总结为 Skill", "沉淀为 Skill", "制作 Skill", "固定成流程"],
        capabilities=["skill.generate", "skill.validate", "skill.publish"],
        source_type="system", enabled=True, status="active",
    ),
    SkillDraft(
        name="screen-resumes-with-evidence",
        display_name="批量简历证据化筛选",
        description="按岗位要求批量比较简历，输出可追溯评分、人工复核排名和 Excel 报告。",
        instructions=(
            "先确认岗位必备条件、加分项和权重；逐份读取候选人材料；每项评分必须给出简历事实证据；"
            "不得使用与岗位无关的受保护属性；只给人工复核建议，不作自动淘汰决定；输出排名及评分明细。"
        ),
        trigger_phrases=["批量简历", "简历筛选", "候选人排名", "岗位匹配"],
        capabilities=["file.extract", "recruitment.evaluate", "artifact.create"],
        source_type="system", enabled=True, status="active",
    ),
    SkillDraft(
        name="build-reviewable-spreadsheet",
        display_name="通用表格处理",
        description="根据纯文字数据从零创建 Excel，或按用户明确规则转换现有工作簿，并保留公式、校验和差异证据。",
        instructions=(
            "先识别用户给出的字段、记录、计算关系和汇总要求；把明确数据编译为白名单表格操作；"
            "公式由受限执行器写入，不把公式伪装成普通单元格文本；生成后重开文件，核验字段、公式、"
            "样式和汇总结果；不覆盖源文件，不猜测用户未提供的业务数字。"
        ),
        trigger_phrases=["生成 Excel", "创建表格", "处理工作簿", "计算完成率"],
        capabilities=["spreadsheet.inspect", "spreadsheet.transform", "artifact.create"],
        metadata={"orchestration": {"intents": ["spreadsheet_processing"]}},
        source_type="system", enabled=True, status="active",
    ),
    SkillDraft(
        name="answer-policy-with-evidence",
        display_name="政策依据问答",
        description="从受控政策索引检索条款，并以文件、章节和页码为依据回答。",
        instructions="只引用检索到的有效依据；说明版本、适用范围和证据缺口；不得把候选人或薪酬材料写入组织通用知识库。",
        trigger_phrases=["政策依据", "政策怎么规定", "适用范围", "政策问答"],
        capabilities=["knowledge.retrieve", "evidence.answer"],
        source_type="system", enabled=True, status="active",
    ),
    SkillDraft(
        name="draft-hr-documents",
        display_name="公文与会议材料",
        description="根据历史材料和用户事实生成可复核的 Word 公文、通知与会议资料。",
        instructions="只使用用户和附件提供的事实；未知信息留空；生成物标记为 AI 草稿，并在正式发送前要求人工审批。",
        trigger_phrases=["公文", "会议通知", "会议材料", "Word草稿"],
        capabilities=["document.compose", "artifact.create"],
        source_type="system", enabled=True, status="active",
    ),
]


def _package_builtin_skill(draft: SkillDraft) -> SkillDraft:
    steps = [item.strip(" 。") for item in re.split(r"[；。]", draft.instructions) if item.strip()]
    if len(steps) < 2:
        steps.append("完成后输出可追溯结果，并进入人工复核")
    workflow = "\n".join(f"{index}. {step}。" for index, step in enumerate(steps, 1))
    trigger = draft.trigger_phrases[0] if draft.trigger_phrases else draft.display_name
    instructions = (
        f"## 适用场景\n\n{draft.description}\n\n"
        "## 输入\n\n- 用户本轮目标与明确约束。\n- 当前用户有权访问的附件、知识和上下文。\n\n"
        f"## 工作流程\n\n{workflow}\n\n"
        "## 校验\n\n- 核对输入完整性、证据来源、输出格式和异常项。\n"
        "- 任何关键事实不足时明确标注待确认，不得猜测补齐。\n\n"
        "## 安全边界\n\n- 只在当前用户、租户、数据和工具权限交集内执行。\n"
        "- 不绕过隐私、公平性、审批和人工复核规则。\n\n"
        "## 输出\n\n- 返回可复核的处理结果、证据或产物。\n- 同时列出缺口、异常和下一步。\n\n"
        f"## 示例\n\n- 用户请求“{trigger}”时，按上述流程执行并给出可复核结果。\n\n"
        "## 不应触发的示例\n\n- 与本 Skill 目的无关的闲聊、其他专业任务或无授权的数据请求不应触发。"
    )
    positive = [f"请执行{item}" for item in draft.trigger_phrases[:2]]
    negative = ["查询今天的天气", "写一首与人事工作无关的短诗"]
    packaged = draft.model_copy(update={"instructions": instructions, "metadata": {
        **draft.metadata,
        "package_format": "agent-skills-v1",
        "governance_version": 2,
        "system_managed": True,
        "risk_level": "medium",
        "approval_required": True,
        "resource_plan": [],
        "examples": [
            {"user_request": item, "expected_behavior": "应选择当前系统 Skill"}
            for item in positive
        ],
        "non_examples": [
            {"user_request": item, "expected_behavior": "不应选择当前系统 Skill"}
            for item in negative
        ],
        "eval_suite": {
            "status": "generated_not_run",
            "positive_triggers": positive,
            "negative_triggers": negative,
        },
    }})
    packaged = packaged.model_copy(update={"metadata": ensure_orchestration_metadata(
        packaged.metadata,
        capabilities=packaged.capabilities,
    )})
    metadata = refresh_package_metadata(packaged, packaged.metadata)
    metadata = merge_harness_metadata(metadata, SkillHarness().evaluate(packaged, metadata))
    return packaged.model_copy(update={"metadata": metadata})


class SkillRegistry:
    """Own Skill validation, versions, activation and selection behind one interface."""

    def __init__(self, database_path: str | Path, default_tenant_id: str = "tenant_unihr_demo") -> None:
        self.database_path = Path(database_path)
        self.default_tenant_id = default_tenant_id
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS skills (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'tenant_unihr_demo',
                    owner_user_id TEXT NOT NULL DEFAULT 'usr_unihr_admin',
                    name TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    trigger_phrases_json TEXT NOT NULL DEFAULT '[]',
                    capabilities_json TEXT NOT NULL DEFAULT '[]',
                    source_type TEXT NOT NULL,
                    source_conversation_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'draft',
                    version INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    visibility TEXT NOT NULL DEFAULT 'personal',
                    approval_status TEXT NOT NULL DEFAULT 'not_required',
                    approval_requested_by TEXT NOT NULL DEFAULT '',
                    approved_by TEXT NOT NULL DEFAULT '',
                    approved_at TEXT NOT NULL DEFAULT '',
                    approval_comment TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skill_versions (
                    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    change_type TEXT NOT NULL DEFAULT 'created',
                    content_sha256 TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(skill_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_skills_enabled ON skills(enabled, status, updated_at);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(skills)").fetchall()}
            if "tenant_id" not in columns:
                db.execute("ALTER TABLE skills ADD COLUMN tenant_id TEXT")
            if "owner_user_id" not in columns:
                db.execute("ALTER TABLE skills ADD COLUMN owner_user_id TEXT")
            skill_columns = {
                "visibility": "TEXT NOT NULL DEFAULT 'personal'",
                "approval_status": "TEXT NOT NULL DEFAULT 'not_required'",
                "approval_requested_by": "TEXT NOT NULL DEFAULT ''",
                "approved_by": "TEXT NOT NULL DEFAULT ''",
                "approved_at": "TEXT NOT NULL DEFAULT ''",
                "approval_comment": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in skill_columns.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE skills ADD COLUMN {name} {declaration}")
            version_columns = {row["name"] for row in db.execute("PRAGMA table_info(skill_versions)").fetchall()}
            for name, declaration in {
                "change_type": "TEXT NOT NULL DEFAULT 'created'",
                "content_sha256": "TEXT NOT NULL DEFAULT ''",
                "created_by": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in version_columns:
                    db.execute(f"ALTER TABLE skill_versions ADD COLUMN {name} {declaration}")
            db.execute(
                "UPDATE skills SET tenant_id=? WHERE tenant_id IS NULL OR tenant_id=''",
                (self.default_tenant_id,),
            )
            db.execute("UPDATE skills SET owner_user_id='*' WHERE source_type='system'")
            db.execute("UPDATE skills SET visibility='system',approval_status='approved' WHERE source_type='system'")
            # Product capability retired on 2026-08-12. Keep immutable versions
            # for audit, but never expose or route to the old built-in Skill.
            db.execute(
                "UPDATE skills SET enabled=0,status='retired',updated_at=? WHERE name='prepare-annual-adjustment'",
                (_now(),),
            )
            db.execute(
                "UPDATE skills SET owner_user_id=? WHERE source_type<>'system' AND (owner_user_id IS NULL OR owner_user_id='' OR owner_user_id='*')",
                (ADMIN_USER_ID,),
            )
            for raw_draft in _BUILTIN_SKILLS:
                draft = _package_builtin_skill(raw_draft)
                existing = db.execute("SELECT * FROM skills WHERE name=?", (draft.name,)).fetchone()
                if existing is None:
                    self._insert(db, draft, self.default_tenant_id, "*")
                    continue
                current_metadata = json.loads(existing["metadata_json"] or "{}")
                if (
                    current_metadata.get("content_sha256") == draft.metadata.get("content_sha256")
                    and int(current_metadata.get("governance_version") or 0) >= 2
                ):
                    continue
                version = int(existing["version"]) + 1
                updated_at = _now()
                db.execute(
                    """UPDATE skills SET display_name=?,description=?,instructions=?,trigger_phrases_json=?,
                    capabilities_json=?,enabled=1,status='active',version=?,metadata_json=?,updated_at=? WHERE id=?""",
                    (
                        draft.display_name, draft.description, draft.instructions,
                        json.dumps(draft.trigger_phrases, ensure_ascii=False),
                        json.dumps(draft.capabilities, ensure_ascii=False), version,
                        json.dumps(draft.metadata, ensure_ascii=False), updated_at, existing["id"],
                    ),
                )
                self._save_version(db, existing["id"], version, draft, updated_at)

    def _scope(self, principal: Principal | None) -> tuple[str, str]:
        if principal is None:
            return self.default_tenant_id, ADMIN_USER_ID
        return principal.tenant_id, principal.user_id

    def list(self, principal: Principal | None = None) -> list[SkillRecord]:
        tenant_id, owner_user_id = self._scope(principal)
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM skills WHERE tenant_id=? AND status<>'retired' AND (owner_user_id='*' OR owner_user_id=?)
                ORDER BY source_type='system' DESC, enabled DESC, updated_at DESC""",
                (tenant_id, owner_user_id),
            ).fetchall()
        return [self._record(row) for row in rows]

    def get(self, skill_id: str, principal: Principal | None = None) -> SkillRecord:
        tenant_id, owner_user_id = self._scope(principal)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM skills WHERE id=? AND tenant_id=? AND (owner_user_id='*' OR owner_user_id=?)",
                (skill_id, tenant_id, owner_user_id),
            ).fetchone()
        if row is None:
            raise KeyError(skill_id)
        return self._record(row)

    def create(self, draft: SkillDraft, principal: Principal | None = None) -> SkillRecord:
        normalized = self._normalize_draft(draft)
        tenant_id, owner_user_id = self._scope(principal)
        with self._connect() as db:
            if db.execute("SELECT 1 FROM skills WHERE name=?", (normalized.name,)).fetchone():
                authoring = dict(normalized.metadata.get("authoring") or {})
                normalized = normalized.model_copy(update={"name": f"{normalized.name[:54]}-{uuid4().hex[:8]}"})
                if normalized.metadata.get("package_format") == "agent-skills-v1":
                    from .skill_builder import refresh_package_metadata
                    metadata = refresh_package_metadata(normalized, normalized.metadata)
                    rebound = normalized.model_copy(update={"metadata": metadata})
                    metadata = merge_harness_metadata(
                        metadata,
                        SkillHarness().evaluate(rebound, metadata),
                    )
                    if authoring:
                        metadata["authoring"] = {
                            **authoring,
                            "content_sha256": metadata.get("content_sha256"),
                        }
                    normalized = normalized.model_copy(update={"metadata": metadata})
            skill_id = self._insert(db, normalized, tenant_id, owner_user_id)
        return self.get(skill_id, principal)

    def replace_draft(
        self,
        skill_id: str,
        draft: SkillDraft,
        *,
        change_type: str = "authoring_completed",
        principal: Principal | None = None,
    ) -> SkillRecord:
        """Replace a user Skill as one immutable version after a governed authoring run."""
        current = self.get(skill_id, principal)
        self._assert_writable(current, principal)
        normalized = self._normalize_draft(draft.model_copy(update={
            "name": current.name,
            "source_type": current.source_type,
            "source_conversation_id": current.source_conversation_id,
            "enabled": False,
            "status": "pending_approval" if current.visibility == "organization" else "draft",
        }))
        version = current.version + 1
        updated_at = _now()
        approval_status = "pending" if current.visibility == "organization" else "not_required"
        with self._connect() as db:
            db.execute(
                """UPDATE skills SET display_name=?,description=?,instructions=?,trigger_phrases_json=?,
                capabilities_json=?,enabled=0,status=?,version=?,metadata_json=?,approval_status=?,
                approval_requested_by=?,approved_by='',approved_at='',approval_comment='',updated_at=? WHERE id=?""",
                (
                    normalized.display_name,
                    normalized.description,
                    normalized.instructions,
                    json.dumps(normalized.trigger_phrases, ensure_ascii=False),
                    json.dumps(normalized.capabilities, ensure_ascii=False),
                    normalized.status,
                    version,
                    json.dumps(normalized.metadata, ensure_ascii=False),
                    approval_status,
                    self._actor_id(principal) if approval_status == "pending" else "",
                    updated_at,
                    skill_id,
                ),
            )
            self._save_version(
                db,
                skill_id,
                version,
                normalized,
                updated_at,
                change_type=change_type,
                created_by=self._actor_id(principal),
            )
        return self.get(skill_id, principal)

    def update(
        self,
        skill_id: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
        trigger_phrases: list[str] | None = None,
        capabilities: list[str] | None = None,
        enabled: bool | None = None,
        principal: Principal | None = None,
    ) -> SkillRecord:
        current = self.get(skill_id, principal)
        self._assert_writable(current, principal)
        content_changed = any(value is not None for value in (
            display_name, description, instructions, trigger_phrases, capabilities,
        ))
        next_enabled = False if content_changed else current.enabled if enabled is None else enabled
        next_status = (
            "pending_approval" if content_changed and current.visibility == "organization"
            else "draft" if content_changed
            else "active" if next_enabled
            else "draft" if current.status == "draft"
            else "disabled"
        )
        draft = SkillDraft(
            name=current.name,
            display_name=display_name or current.display_name,
            description=description or current.description,
            instructions=instructions or current.instructions,
            trigger_phrases=trigger_phrases if trigger_phrases is not None else current.trigger_phrases,
            capabilities=capabilities if capabilities is not None else current.capabilities,
            source_type=current.source_type,
            source_conversation_id=current.source_conversation_id,
            enabled=next_enabled,
            status=next_status,  # type: ignore[arg-type]
            metadata=current.metadata,
        )
        normalized = self._normalize_draft(draft)
        metadata = current.metadata
        if content_changed:
            from .skill_builder import refresh_package_metadata
            metadata = refresh_package_metadata(normalized, metadata)
            normalized = normalized.model_copy(update={"metadata": metadata})
        if next_enabled and metadata.get("package_format") == "agent-skills-v1":
            validation = metadata.get("validation")
            if not isinstance(validation, dict) or not validation.get("publishable", validation.get("passed")):
                raise ValueError("skill_validation_failed")
        version = current.version + 1 if content_changed else current.version
        updated_at = _now()
        approval_status = (
            "pending" if content_changed and current.visibility == "organization"
            else "not_required" if content_changed
            else current.approval_status
        )
        requested_by = (
            self._actor_id(principal) if approval_status == "pending"
            else "" if content_changed
            else current.approval_requested_by
        )
        with self._connect() as db:
            db.execute(
                """UPDATE skills SET display_name=?,description=?,instructions=?,trigger_phrases_json=?,
                capabilities_json=?,enabled=?,status=?,version=?,metadata_json=?,approval_status=?,
                approval_requested_by=?,approved_by=?,approved_at=?,approval_comment=?,updated_at=? WHERE id=?""",
                (
                    normalized.display_name, normalized.description, normalized.instructions,
                    json.dumps(normalized.trigger_phrases, ensure_ascii=False),
                    json.dumps(normalized.capabilities, ensure_ascii=False), int(normalized.enabled),
                    normalized.status, version, json.dumps(metadata, ensure_ascii=False), approval_status,
                    requested_by,
                    "" if content_changed else current.approved_by,
                    "" if content_changed else current.approved_at,
                    "" if content_changed else current.approval_comment,
                    updated_at, skill_id,
                ),
            )
            if content_changed:
                self._save_version(
                    db, skill_id, version, normalized, updated_at,
                    change_type="content_update", created_by=self._actor_id(principal),
                )
        return self.get(skill_id, principal)

    def replace_metadata(
        self,
        skill_id: str,
        metadata: dict[str, object],
        principal: Principal | None = None,
        change_type: str = "metadata_validation",
        invalidate_activation: bool = False,
    ) -> None:
        current = self.get(skill_id, principal)
        self._assert_writable(current, principal)
        version = current.version + 1
        updated_at = _now()
        draft = SkillDraft(
            name=current.name,
            display_name=current.display_name,
            description=current.description,
            instructions=current.instructions,
            trigger_phrases=current.trigger_phrases,
            capabilities=current.capabilities,
            source_type=current.source_type,
            source_conversation_id=current.source_conversation_id,
            enabled=current.enabled,
            status=current.status,
            metadata=metadata,
        )
        with self._connect() as db:
            if invalidate_activation:
                db.execute(
                    """UPDATE skills SET metadata_json=?,version=?,updated_at=?,enabled=0,status='draft',
                    approval_status=CASE WHEN visibility='personal' THEN 'not_required' ELSE approval_status END
                    WHERE id=?""",
                    (json.dumps(metadata, ensure_ascii=False), version, updated_at, skill_id),
                )
            else:
                db.execute(
                    "UPDATE skills SET metadata_json=?,version=?,updated_at=? WHERE id=?",
                    (json.dumps(metadata, ensure_ascii=False), version, updated_at, skill_id),
                )
            self._save_version(
                db, skill_id, version, draft, updated_at,
                change_type=change_type, created_by=self._actor_id(principal),
            )

    def delete(self, skill_id: str, principal: Principal | None = None) -> None:
        current = self.get(skill_id, principal)
        if current.source_type == "system":
            raise ValueError("system_skill_cannot_be_deleted")
        self._assert_writable(current, principal)
        with self._connect() as db:
            db.execute("DELETE FROM skills WHERE id=?", (skill_id,))

    def list_versions(
        self, skill_id: str, principal: Principal | None = None,
    ) -> list[SkillVersionRecord]:
        self.get(skill_id, principal)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM skill_versions WHERE skill_id=? ORDER BY version DESC",
                (skill_id,),
            ).fetchall()
        return [SkillVersionRecord(
            skill_id=row["skill_id"],
            version=int(row["version"]),
            snapshot=SkillDraft.model_validate_json(row["snapshot_json"]),
            change_type=row["change_type"],
            content_sha256=row["content_sha256"],
            created_by=row["created_by"],
            created_at=row["created_at"],
        ) for row in rows]

    def rollback(
        self,
        skill_id: str,
        target_version: int,
        principal: Principal | None = None,
    ) -> SkillRecord:
        current = self.get(skill_id, principal)
        self._assert_writable(current, principal)
        with self._connect() as db:
            row = db.execute(
                "SELECT snapshot_json FROM skill_versions WHERE skill_id=? AND version=?",
                (skill_id, target_version),
            ).fetchone()
            if row is None:
                raise KeyError(f"{skill_id}:v{target_version}")
            target = SkillDraft.model_validate_json(row["snapshot_json"])
            metadata = dict(target.metadata)
            metadata["content_sha256"] = current.metadata.get("content_sha256", "")
            metadata["rollback_from_version"] = target_version
            metadata["rollback_created_from_version"] = current.version
            restored = target.model_copy(update={
                "enabled": False,
                "status": "pending_approval" if current.visibility == "organization" else "draft",
                "metadata": metadata,
            })
            restored = restored.model_copy(update={
                "metadata": refresh_package_metadata(restored, restored.metadata),
            })
            version = current.version + 1
            updated_at = _now()
            approval_status = "pending" if current.visibility == "organization" else "not_required"
            db.execute(
                """UPDATE skills SET display_name=?,description=?,instructions=?,trigger_phrases_json=?,
                capabilities_json=?,enabled=0,status=?,version=?,metadata_json=?,approval_status=?,
                approval_requested_by=?,approved_by='',approved_at='',approval_comment='',updated_at=? WHERE id=?""",
                (
                    restored.display_name, restored.description, restored.instructions,
                    json.dumps(restored.trigger_phrases, ensure_ascii=False),
                    json.dumps(restored.capabilities, ensure_ascii=False), restored.status, version,
                    json.dumps(restored.metadata, ensure_ascii=False), approval_status,
                    self._actor_id(principal) if approval_status == "pending" else "",
                    updated_at, skill_id,
                ),
            )
            self._save_version(
                db, skill_id, version, restored, updated_at,
                change_type=f"rollback_from_v{target_version}", created_by=self._actor_id(principal),
            )
        return self.get(skill_id, principal)

    def request_organization_approval(
        self, skill_id: str, principal: Principal | None = None,
    ) -> SkillRecord:
        current = self.get(skill_id, principal)
        if current.source_type == "system" or current.owner_user_id == "*":
            raise ValueError("organization_skill_request_not_allowed")
        validation = current.metadata.get("validation")
        if not isinstance(validation, dict) or not validation.get("publishable"):
            raise ValueError("skill_validation_failed")
        with self._connect() as db:
            db.execute(
                """UPDATE skills SET enabled=0,status='pending_approval',approval_status='pending',
                approval_requested_by=?,approved_by='',approved_at='',approval_comment='',updated_at=? WHERE id=?""",
                (self._actor_id(principal), _now(), skill_id),
            )
        return self.get(skill_id, principal)

    def list_organization_approvals(self, principal: Principal) -> list[SkillRecord]:
        if principal.role != "admin":
            raise PermissionError("skill_organization_approval_required")
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM skills WHERE tenant_id=? AND approval_status='pending'
                ORDER BY updated_at""",
                (principal.tenant_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def decide_organization_approval(
        self,
        skill_id: str,
        approved: bool,
        principal: Principal,
        comment: str = "",
    ) -> SkillRecord:
        if principal.role != "admin":
            raise PermissionError("skill_organization_approval_required")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM skills WHERE id=? AND tenant_id=? AND approval_status='pending'",
                (skill_id, principal.tenant_id),
            ).fetchone()
            if row is None:
                raise KeyError(skill_id)
            db.execute(
                """UPDATE skills SET owner_user_id=?,visibility=?,approval_status=?,enabled=?,status=?,
                approved_by=?,approved_at=?,approval_comment=?,updated_at=? WHERE id=?""",
                (
                    "*" if approved else row["owner_user_id"],
                    "organization" if approved else "personal",
                    "approved" if approved else "rejected",
                    int(approved), "active" if approved else "draft",
                    principal.user_id, _now(), comment.strip()[:1_000], _now(), skill_id,
                ),
            )
            decided = db.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
        assert decided is not None
        return self._record(decided)

    @staticmethod
    def _actor_id(principal: Principal | None) -> str:
        return principal.user_id if principal is not None else ADMIN_USER_ID

    @staticmethod
    def _assert_writable(current: SkillRecord, principal: Principal | None) -> None:
        if current.source_type == "system":
            raise ValueError("system_skill_is_read_only")
        if current.owner_user_id == "*" and (principal is None or principal.role != "admin"):
            raise PermissionError("organization_skill_admin_only")

    def select(self, query: str, max_results: int = 3, principal: Principal | None = None) -> list[SkillMatch]:
        matches: list[SkillMatch] = []
        for skill in self.list(principal):
            if not skill.enabled or skill.status != "active":
                continue
            best_score, best_trigger = score_skill_match(skill, query)
            if best_score > 0:
                matches.append(SkillMatch(skill=skill, score=best_score, matched_trigger=best_trigger))
        matches.sort(key=lambda item: (-item.score, item.skill.display_name.casefold()))
        return matches[:max_results]

    def select_by_ids(
        self,
        skill_ids: list[str],
        principal: Principal | None = None,
        max_results: int = 5,
    ) -> list[SkillMatch]:
        selected: list[SkillMatch] = []
        seen: set[str] = set()
        for skill_id in skill_ids[:max_results]:
            if skill_id in seen:
                continue
            seen.add(skill_id)
            skill = self.get(skill_id, principal)
            if not skill.enabled or skill.status != "active":
                raise ValueError("skill_not_active")
            selected.append(SkillMatch(skill=skill, score=100.0, matched_trigger="explicit"))
        return selected

    @staticmethod
    def render_prompt(matches: list[SkillMatch]) -> str:
        return "\n\n".join(
            f"### {item.skill.display_name} v{item.skill.version}\n{item.skill.instructions}"
            for item in matches
        )

    def export_markdown(self, skill_id: str, principal: Principal | None = None) -> str:
        item = self.get(skill_id, principal)
        packaged = item.metadata.get("skill_md")
        if isinstance(packaged, str) and packaged.strip():
            return packaged
        return (
            "---\n"
            f"name: {item.name}\n"
            f"description: {item.description.replace(chr(10), ' ')}\n"
            "---\n\n"
            f"# {item.display_name}\n\n{item.instructions.strip()}\n"
        )

    def _insert(
        self, db: sqlite3.Connection, draft: SkillDraft, tenant_id: str, owner_user_id: str,
    ) -> str:
        normalized = self._normalize_draft(draft)
        skill_id = f"skill_{uuid4().hex}"
        now = _now()
        db.execute(
            """INSERT INTO skills(id,tenant_id,owner_user_id,name,display_name,description,instructions,trigger_phrases_json,
            capabilities_json,source_type,source_conversation_id,enabled,status,version,metadata_json,
            visibility,approval_status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                skill_id, tenant_id, owner_user_id, normalized.name, normalized.display_name, normalized.description,
                normalized.instructions, json.dumps(normalized.trigger_phrases, ensure_ascii=False),
                json.dumps(normalized.capabilities, ensure_ascii=False), normalized.source_type,
                normalized.source_conversation_id, int(normalized.enabled), normalized.status, 1,
                json.dumps(normalized.metadata, ensure_ascii=False),
                "system" if normalized.source_type == "system" else "organization" if owner_user_id == "*" else "personal",
                "approved" if normalized.source_type == "system" or owner_user_id == "*" else "not_required",
                now, now,
            ),
        )
        self._save_version(
            db, skill_id, 1, normalized, now,
            change_type="created", created_by=owner_user_id,
        )
        return skill_id

    @staticmethod
    def _save_version(
        db: sqlite3.Connection,
        skill_id: str,
        version: int,
        draft: SkillDraft,
        created_at: str,
        *,
        change_type: str = "created",
        created_by: str = "",
    ) -> None:
        db.execute(
            """INSERT INTO skill_versions(
            skill_id,version,snapshot_json,change_type,content_sha256,created_by,created_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (
                skill_id, version, draft.model_dump_json(), change_type,
                str(draft.metadata.get("content_sha256") or ""), created_by, created_at,
            ),
        )

    @staticmethod
    def _normalize_draft(draft: SkillDraft) -> SkillDraft:
        name = re.sub(r"[^a-z0-9-]+", "-", draft.name.casefold()).strip("-")
        name = re.sub(r"-{2,}", "-", name)[:64] or f"user-skill-{uuid4().hex[:8]}"
        triggers = list(dict.fromkeys(" ".join(item.split())[:80] for item in draft.trigger_phrases if item.strip()))[:16]
        capabilities = list(dict.fromkeys(item.strip()[:80] for item in draft.capabilities if item.strip()))[:16]
        return draft.model_copy(update={
            "name": name,
            "display_name": " ".join(draft.display_name.split())[:80],
            "description": " ".join(draft.description.split())[:600],
            "instructions": draft.instructions.strip()[:12_000],
            "trigger_phrases": triggers,
            "capabilities": capabilities,
        })

    @staticmethod
    def _record(row: sqlite3.Row) -> SkillRecord:
        return SkillRecord(
            id=row["id"], tenant_id=row["tenant_id"], owner_user_id=row["owner_user_id"],
            name=row["name"], display_name=row["display_name"],
            description=row["description"], instructions=row["instructions"],
            trigger_phrases=json.loads(row["trigger_phrases_json"]),
            capabilities=json.loads(row["capabilities_json"]), source_type=row["source_type"],
            source_conversation_id=row["source_conversation_id"], enabled=bool(row["enabled"]),
            status=row["status"], version=row["version"], metadata=json.loads(row["metadata_json"]),
            visibility=row["visibility"], approval_status=row["approval_status"],
            approval_requested_by=row["approval_requested_by"], approved_by=row["approved_by"],
            approved_at=row["approved_at"], approval_comment=row["approval_comment"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )
