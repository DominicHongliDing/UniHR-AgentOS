from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.settings import Settings


@dataclass(frozen=True)
class ModelUsageScope:
    tenant_id: str
    user_id: str = ""
    run_id: str = ""


_current_scope: ContextVar[ModelUsageScope | None] = ContextVar(
    "unihr_model_usage_scope", default=None
)


@contextmanager
def model_usage_scope(tenant_id: str, user_id: str = "", run_id: str = ""):
    token = _current_scope.set(ModelUsageScope(tenant_id, user_id, run_id))
    try:
        yield
    finally:
        _current_scope.reset(token)


class ModelUsageStore:
    """Append-only provider usage ledger with configured, never guessed pricing."""

    def __init__(self, settings: Settings) -> None:
        if settings.database_path is None:
            raise ValueError("database_path_required")
        self.database_path = Path(settings.database_path)
        self.default_tenant_id = settings.default_tenant_id
        try:
            pricing = json.loads(settings.model_pricing_json or "{}")
            self.pricing = pricing if isinstance(pricing, dict) else {}
        except json.JSONDecodeError:
            self.pricing = {}
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_usage_events (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    operation_kind TEXT NOT NULL,
                    input_units INTEGER NOT NULL DEFAULT 0,
                    output_units INTEGER NOT NULL DEFAULT 0,
                    unit_type TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    estimated_cost_cny REAL NOT NULL DEFAULT 0,
                    pricing_configured INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_model_usage_tenant_created
                    ON model_usage_events(tenant_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_model_usage_purpose
                    ON model_usage_events(tenant_id, operation_kind, purpose, created_at DESC);
                CREATE TABLE IF NOT EXISTS model_budget_policies (
                    tenant_id TEXT PRIMARY KEY,
                    monthly_budget_cny REAL NOT NULL DEFAULT 0,
                    per_run_budget_cny REAL NOT NULL DEFAULT 0,
                    daily_call_limit INTEGER NOT NULL DEFAULT 0,
                    warning_ratio REAL NOT NULL DEFAULT 0.8,
                    updated_by TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(model_usage_events)").fetchall()
            }
            for name in ("execution_id", "workflow_run_id", "conversation_id"):
                if name not in columns:
                    db.execute(
                        f"ALTER TABLE model_usage_events ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                    )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_usage_execution ON model_usage_events(tenant_id, execution_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_usage_conversation ON model_usage_events(tenant_id, conversation_id, created_at DESC)"
            )

    def record(
        self,
        *,
        provider: str,
        model: str,
        purpose: str,
        operation_kind: str,
        input_units: int = 0,
        output_units: int = 0,
        unit_type: str = "tokens",
        latency_ms: int = 0,
        status: str = "succeeded",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        scope = _current_scope.get() or ModelUsageScope(self.default_tenant_id)
        estimated_cost, configured = self._estimate(
            model, operation_kind, input_units, output_units
        )
        with self._connect() as db:
            db.execute(
                """INSERT INTO model_usage_events(
                id,tenant_id,user_id,run_id,provider,model,purpose,operation_kind,
                input_units,output_units,unit_type,latency_ms,status,estimated_cost_cny,
                pricing_configured,metadata_json,created_at,execution_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"usage_{uuid4().hex}", scope.tenant_id, scope.user_id, scope.run_id,
                    provider, model, purpose, operation_kind, max(0, int(input_units)),
                    max(0, int(output_units)), unit_type, max(0, int(latency_ms)), status,
                    estimated_cost, int(configured),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                    scope.run_id,
                ),
            )

    def bind_execution(
        self,
        tenant_id: str,
        execution_id: str,
        *,
        conversation_id: str,
        workflow_run_id: str,
    ) -> int:
        """Bind provider calls made during an async turn to durable business objects."""
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE model_usage_events
                SET execution_id=?,conversation_id=?,workflow_run_id=?
                WHERE tenant_id=? AND (execution_id=? OR run_id=?)""",
                (
                    execution_id, conversation_id, workflow_run_id,
                    tenant_id, execution_id, execution_id,
                ),
            )
            return int(cursor.rowcount or 0)

    def scope_summary(self, tenant_id: str, scope_id: str) -> dict[str, Any]:
        """Return auditable usage for one evaluation/task scope without guessing prices."""
        with self._connect() as db:
            row = db.execute(
                """SELECT COUNT(*) calls,SUM(estimated_cost_cny) estimated_cost_cny,
                SUM(CASE WHEN pricing_configured=1 THEN 1 ELSE 0 END) priced_calls,
                GROUP_CONCAT(DISTINCT model) models
                FROM model_usage_events WHERE tenant_id=?
                AND (run_id=? OR execution_id=?)""",
                (tenant_id, scope_id, scope_id),
            ).fetchone()
        calls = int(row["calls"] or 0) if row else 0
        priced_calls = int(row["priced_calls"] or 0) if row else 0
        return {
            "calls": calls,
            "estimated_cost_cny": round(float(row["estimated_cost_cny"] or 0), 8) if row else 0,
            "priced_calls": priced_calls,
            "pricing_coverage": priced_calls / calls if calls else 1.0,
            "models": str(row["models"] or "") if row else "",
        }

    def get_budget_policy(self, tenant_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM model_budget_policies WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
        if row is None:
            return {
                "tenant_id": tenant_id,
                "monthly_budget_cny": 0.0,
                "per_run_budget_cny": 0.0,
                "daily_call_limit": 0,
                "warning_ratio": 0.8,
                "updated_by": "",
                "updated_at": "",
            }
        return dict(row)

    def update_budget_policy(
        self,
        tenant_id: str,
        *,
        monthly_budget_cny: float,
        per_run_budget_cny: float,
        daily_call_limit: int,
        warning_ratio: float,
        updated_by: str,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                """INSERT INTO model_budget_policies(
                tenant_id,monthly_budget_cny,per_run_budget_cny,daily_call_limit,
                warning_ratio,updated_by,updated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                monthly_budget_cny=excluded.monthly_budget_cny,
                per_run_budget_cny=excluded.per_run_budget_cny,
                daily_call_limit=excluded.daily_call_limit,
                warning_ratio=excluded.warning_ratio,
                updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                (
                    tenant_id, max(0.0, float(monthly_budget_cny)),
                    max(0.0, float(per_run_budget_cny)), max(0, int(daily_call_limit)),
                    min(1.0, max(0.1, float(warning_ratio))), updated_by, now,
                ),
            )
        return self.get_budget_policy(tenant_id)

    def cost_control(self, tenant_id: str) -> dict[str, Any]:
        policy = self.get_budget_policy(tenant_id)
        with self._connect() as db:
            month = db.execute(
                """SELECT COUNT(*) calls,SUM(estimated_cost_cny) cost,
                SUM(CASE WHEN pricing_configured=1 THEN 1 ELSE 0 END) priced_calls
                FROM model_usage_events WHERE tenant_id=?
                AND substr(created_at,1,7)=substr(datetime('now'),1,7)""",
                (tenant_id,),
            ).fetchone()
            today = db.execute(
                """SELECT COUNT(*) calls FROM model_usage_events WHERE tenant_id=?
                AND substr(created_at,1,10)=substr(datetime('now'),1,10)""",
                (tenant_id,),
            ).fetchone()
            run = db.execute(
                """SELECT workflow_run_id,conversation_id,COUNT(*) calls,
                SUM(estimated_cost_cny) cost FROM model_usage_events
                WHERE tenant_id=? AND workflow_run_id<>''
                GROUP BY workflow_run_id,conversation_id ORDER BY cost DESC LIMIT 1""",
                (tenant_id,),
            ).fetchone()
        monthly_calls = int(month["calls"] or 0)
        priced_calls = int(month["priced_calls"] or 0)
        monthly_cost = round(float(month["cost"] or 0), 8)
        daily_calls = int(today["calls"] or 0)
        max_run_cost = round(float(run["cost"] or 0), 8) if run else 0.0
        alerts: list[dict[str, Any]] = []
        if monthly_calls and priced_calls < monthly_calls:
            alerts.append({
                "code": "pricing_incomplete", "severity": "warning",
                "message": "存在未配置单价的模型调用，金额预算只能作为部分成本参考。",
            })
        monthly_budget = float(policy["monthly_budget_cny"] or 0)
        warning_ratio = float(policy["warning_ratio"] or 0.8)
        if monthly_budget > 0 and monthly_cost >= monthly_budget * warning_ratio:
            alerts.append({
                "code": "monthly_budget_exceeded" if monthly_cost >= monthly_budget else "monthly_budget_warning",
                "severity": "critical" if monthly_cost >= monthly_budget else "warning",
                "message": "本月已达到预算上限。" if monthly_cost >= monthly_budget else "本月成本已达到预算预警线。",
            })
        daily_limit = int(policy["daily_call_limit"] or 0)
        if daily_limit > 0 and daily_calls >= daily_limit:
            alerts.append({
                "code": "daily_call_limit", "severity": "critical",
                "message": "今日模型调用量已达到组织阈值。",
            })
        per_run_budget = float(policy["per_run_budget_cny"] or 0)
        if per_run_budget > 0 and max_run_cost >= per_run_budget:
            alerts.append({
                "code": "per_run_budget", "severity": "critical",
                "message": "存在单任务成本达到预算上限的工作流。",
            })
        return {
            "policy": policy,
            "monthly_calls": monthly_calls,
            "priced_calls": priced_calls,
            "unpriced_calls": max(0, monthly_calls - priced_calls),
            "pricing_coverage": priced_calls / monthly_calls if monthly_calls else 1.0,
            "monthly_cost_cny": monthly_cost,
            "daily_calls": daily_calls,
            "max_run_cost_cny": max_run_cost,
            "max_run_id": str(run["workflow_run_id"] or "") if run else "",
            "alerts": alerts,
        }

    def _estimate(
        self, model: str, operation_kind: str, input_units: int, output_units: int
    ) -> tuple[float, bool]:
        config = self.pricing.get(model) or self.pricing.get(operation_kind) or {}
        if not isinstance(config, dict):
            return 0.0, False
        if operation_kind in {"chat", "embedding"}:
            input_rate = float(config.get("input_per_million") or 0)
            output_rate = float(config.get("output_per_million") or 0)
            configured = "input_per_million" in config or "output_per_million" in config
            return round((input_units * input_rate + output_units * output_rate) / 1_000_000, 8), configured
        if operation_kind == "rerank":
            rate = float(config.get("per_1000_documents") or 0)
            return round(input_units * rate / 1000, 8), "per_1000_documents" in config
        if operation_kind == "web_search":
            rate = float(config.get("per_call") or 0)
            return round(input_units * rate, 8), "per_call" in config
        return 0.0, False

    def tenant_summary(self, tenant_id: str, *, days: int = 30) -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT operation_kind,purpose,model,status,COUNT(*) calls,
                SUM(input_units) input_units,SUM(output_units) output_units,
                SUM(estimated_cost_cny) estimated_cost_cny,
                MAX(pricing_configured) pricing_configured
                FROM model_usage_events
                WHERE tenant_id=? AND created_at>=datetime('now', ?)
                GROUP BY operation_kind,purpose,model,status
                ORDER BY calls DESC""",
                (tenant_id, f"-{max(1, days)} days"),
            ).fetchall()
            daily = db.execute(
                """SELECT substr(created_at,1,10) day,COUNT(*) calls,
                SUM(input_units) input_units,SUM(output_units) output_units,
                SUM(estimated_cost_cny) estimated_cost_cny
                FROM model_usage_events
                WHERE tenant_id=? AND created_at>=datetime('now', ?)
                GROUP BY substr(created_at,1,10) ORDER BY day""",
                (tenant_id, f"-{max(1, days)} days"),
            ).fetchall()
            tables = {
                str(row["name"]) for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if {"conversations", "recruitment_cases"}.issubset(tables):
                attribution = db.execute(
                    """SELECT e.conversation_id,COALESCE(c.title,'未归属会话') conversation_title,
                    COALESCE(rc.id,'') case_id,
                    CASE WHEN COALESCE(rc.case_name,'')<>'' THEN rc.case_name ELSE COALESCE(rc.job_title,'') END case_name,
                    COUNT(*) calls,SUM(e.estimated_cost_cny) estimated_cost_cny
                    FROM model_usage_events e
                    LEFT JOIN conversations c ON c.id=e.conversation_id
                    LEFT JOIN recruitment_cases rc ON rc.conversation_id=e.conversation_id
                        AND rc.tenant_id=e.tenant_id
                    WHERE e.tenant_id=? AND e.created_at>=datetime('now', ?)
                    GROUP BY e.conversation_id,c.title,rc.id,rc.case_name,rc.job_title
                    ORDER BY calls DESC LIMIT 30""",
                    (tenant_id, f"-{max(1, days)} days"),
                ).fetchall()
            else:
                attribution = db.execute(
                    """SELECT conversation_id,'未归属会话' conversation_title,
                    '' case_id,'' case_name,COUNT(*) calls,
                    SUM(estimated_cost_cny) estimated_cost_cny
                    FROM model_usage_events WHERE tenant_id=?
                    AND created_at>=datetime('now', ?)
                    GROUP BY conversation_id ORDER BY calls DESC LIMIT 30""",
                    (tenant_id, f"-{max(1, days)} days"),
                ).fetchall()
        total_calls = sum(int(row["calls"] or 0) for row in rows)
        priced_calls = sum(
            int(row["calls"] or 0) for row in rows if bool(row["pricing_configured"])
        )
        return {
            "days": days,
            "calls": total_calls,
            "input_units": sum(int(row["input_units"] or 0) for row in rows),
            "output_units": sum(int(row["output_units"] or 0) for row in rows),
            "estimated_cost_cny": round(sum(float(row["estimated_cost_cny"] or 0) for row in rows), 8),
            "pricing_configured": any(bool(row["pricing_configured"]) for row in rows),
            "priced_calls": priced_calls,
            "unpriced_calls": max(0, total_calls - priced_calls),
            "pricing_coverage": priced_calls / total_calls if total_calls else 1.0,
            "breakdown": [dict(row) for row in rows],
            "daily": [dict(row) for row in daily],
            "attribution": [dict(row) for row in attribution],
        }
