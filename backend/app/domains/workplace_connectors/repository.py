from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Iterator

from app.domains.identity import Principal

from .contracts import (
    ActionReceipt,
    ProposalStatus,
    ProposedAction,
    ReceiptStatus,
    utc_now_iso,
)


class WorkplaceConnectorRepository:
    """Durable owner-scoped proposals and receipts; never stores provider tokens."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = Lock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=30)
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
                CREATE TABLE IF NOT EXISTS external_action_proposals (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    normalized_arguments_json TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    parameter_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    case_id TEXT,
                    run_id TEXT,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_external_proposals_owner
                    ON external_action_proposals(tenant_id, owner_user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS external_action_receipts (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    external_ids_json TEXT NOT NULL DEFAULT '[]',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def create_proposal(self, proposal: ProposedAction) -> ProposedAction:
        with self._connect() as db:
            db.execute(
                """INSERT INTO external_action_proposals(
                id,tenant_id,owner_user_id,provider,capability,summary,
                normalized_arguments_json,risk_level,parameter_hash,status,
                case_id,run_id,expires_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    proposal.id,
                    proposal.tenant_id,
                    proposal.owner_user_id,
                    proposal.provider.value,
                    proposal.capability,
                    proposal.summary,
                    json.dumps(proposal.normalized_arguments, ensure_ascii=False, sort_keys=True),
                    proposal.risk_level,
                    proposal.parameter_hash,
                    proposal.status.value,
                    proposal.case_id,
                    proposal.run_id,
                    proposal.expires_at,
                    proposal.created_at,
                    proposal.updated_at,
                ),
            )
        return proposal

    def get_proposal(self, proposal_id: str, principal: Principal) -> ProposedAction:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM external_action_proposals
                WHERE id=? AND tenant_id=? AND owner_user_id=?""",
                (proposal_id, principal.tenant_id, principal.user_id),
            ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return self._proposal(row)

    def set_proposal_status(
        self, proposal_id: str, principal: Principal, status: ProposalStatus
    ) -> ProposedAction:
        self.get_proposal(proposal_id, principal)
        with self._connect() as db:
            db.execute(
                """UPDATE external_action_proposals SET status=?,updated_at=?
                WHERE id=? AND tenant_id=? AND owner_user_id=?""",
                (
                    status.value,
                    utc_now_iso(),
                    proposal_id,
                    principal.tenant_id,
                    principal.user_id,
                ),
            )
        return self.get_proposal(proposal_id, principal)

    def get_receipt_for_proposal(
        self, proposal_id: str, principal: Principal
    ) -> ActionReceipt | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM external_action_receipts
                WHERE proposal_id=? AND tenant_id=? AND owner_user_id=?""",
                (proposal_id, principal.tenant_id, principal.user_id),
            ).fetchone()
        return self._receipt(row) if row is not None else None

    def save_receipt(self, receipt: ActionReceipt) -> ActionReceipt:
        with self._write_lock, self._connect() as db:
            db.execute(
                """INSERT INTO external_action_receipts(
                id,proposal_id,tenant_id,owner_user_id,provider,capability,status,
                idempotency_key,external_ids_json,details_json,error_code,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    status=excluded.status,
                    external_ids_json=excluded.external_ids_json,
                    details_json=excluded.details_json,
                    error_code=excluded.error_code,
                    updated_at=excluded.updated_at""",
                (
                    receipt.id,
                    receipt.proposal_id,
                    receipt.tenant_id,
                    receipt.owner_user_id,
                    receipt.provider.value,
                    receipt.capability,
                    receipt.status.value,
                    receipt.idempotency_key,
                    json.dumps(receipt.external_ids, ensure_ascii=False),
                    json.dumps(receipt.details, ensure_ascii=False, sort_keys=True),
                    receipt.error_code,
                    receipt.created_at,
                    receipt.updated_at,
                ),
            )
        return receipt

    @staticmethod
    def _proposal(row: sqlite3.Row) -> ProposedAction:
        return ProposedAction(
            id=row["id"],
            tenant_id=row["tenant_id"],
            owner_user_id=row["owner_user_id"],
            provider=row["provider"],
            capability=row["capability"],
            summary=row["summary"],
            normalized_arguments=json.loads(row["normalized_arguments_json"]),
            risk_level=row["risk_level"],
            parameter_hash=row["parameter_hash"],
            status=row["status"],
            case_id=row["case_id"],
            run_id=row["run_id"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _receipt(row: sqlite3.Row) -> ActionReceipt:
        return ActionReceipt(
            id=row["id"],
            proposal_id=row["proposal_id"],
            tenant_id=row["tenant_id"],
            owner_user_id=row["owner_user_id"],
            provider=row["provider"],
            capability=row["capability"],
            status=ReceiptStatus(row["status"]),
            idempotency_key=row["idempotency_key"],
            external_ids=json.loads(row["external_ids_json"]),
            details=json.loads(row["details_json"]),
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

