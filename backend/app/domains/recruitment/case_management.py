from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.domains.identity import Principal, RUN_APPROVE

from .contracts import (
    CandidateReviewRecord,
    InterviewFeedback,
    InterviewNote,
    InterviewPanelMember,
    InterviewSession,
    RecruitmentCaseAction,
    RecruitmentCaseCreate,
    RecruitmentCaseRecord,
    RecruitmentFolderRecord,
    RecruitmentInterviewerRecord,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecruitmentCaseModule:
    """Persist and govern the human recruitment review lifecycle behind one seam."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
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
                CREATE TABLE IF NOT EXISTS recruitment_cases (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE CASCADE,
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
                    job_title TEXT NOT NULL,
                    rubric_hash TEXT NOT NULL,
                    selection_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'reviewing',
                    candidates_json TEXT NOT NULL DEFAULT '[]',
                    final_candidate_ids_json TEXT NOT NULL DEFAULT '[]',
                    final_rationale TEXT NOT NULL DEFAULT '',
                    finalized_by TEXT NOT NULL DEFAULT '',
                    finalized_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_recruitment_cases_owner_updated
                    ON recruitment_cases(tenant_id, owner_user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_recruitment_cases_conversation
                    ON recruitment_cases(conversation_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS recruitment_case_folders (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_recruitment_case_folders_owner
                    ON recruitment_case_folders(tenant_id, owner_user_id, updated_at DESC);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(recruitment_cases)").fetchall()}
            if "case_name" not in columns:
                db.execute("ALTER TABLE recruitment_cases ADD COLUMN case_name TEXT NOT NULL DEFAULT ''")
            if "folder_id" not in columns:
                db.execute("ALTER TABLE recruitment_cases ADD COLUMN folder_id TEXT NOT NULL DEFAULT ''")
            if "archived_at" not in columns:
                db.execute("ALTER TABLE recruitment_cases ADD COLUMN archived_at TEXT NOT NULL DEFAULT ''")
            if "collaborator_user_ids_json" not in columns:
                db.execute("ALTER TABLE recruitment_cases ADD COLUMN collaborator_user_ids_json TEXT NOT NULL DEFAULT '[]'")
            if "interview_sessions_json" not in columns:
                db.execute("ALTER TABLE recruitment_cases ADD COLUMN interview_sessions_json TEXT NOT NULL DEFAULT '[]'")

    def open(self, command: RecruitmentCaseCreate) -> RecruitmentCaseRecord:
        now = _now()
        with self._connect() as db:
            existing = db.execute(
                "SELECT * FROM recruitment_cases WHERE run_id=?", (command.run_id,),
            ).fetchone()
            if existing is not None:
                return self._record(existing)
            db.execute(
                """INSERT INTO recruitment_cases(
                    id,tenant_id,owner_user_id,conversation_id,run_id,artifact_id,job_title,
                    rubric_hash,selection_count,candidates_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    command.case_id, command.tenant_id, command.owner_user_id,
                    command.conversation_id, command.run_id, command.artifact_id,
                    command.job_title, command.rubric_hash, command.selection_count,
                    json.dumps([item.model_dump(mode="json") for item in command.candidates], ensure_ascii=False),
                    now, now,
                ),
            )
            row = db.execute("SELECT * FROM recruitment_cases WHERE id=?", (command.case_id,)).fetchone()
        if row is None:  # pragma: no cover - defensive database guard
            raise KeyError("recruitment_case_not_found")
        return self._record(row)

    def get(self, case_id: str, principal: Principal) -> RecruitmentCaseRecord:
        with self._connect() as db:
            row = db.execute("SELECT * FROM recruitment_cases WHERE id=?", (case_id,)).fetchone()
        if row is None or not self._can_access(row, principal):
            raise KeyError("recruitment_case_not_found")
        return self._record(row)

    def list(
        self,
        principal: Principal,
        *,
        status: str = "",
        folder_id: str = "",
        query: str = "",
        archived: bool = False,
    ) -> list[RecruitmentCaseRecord]:
        conditions = ["tenant_id=?"]
        values: list[str] = [principal.tenant_id]
        if not principal.can(RUN_APPROVE):
            conditions.append("(owner_user_id=? OR EXISTS (SELECT 1 FROM json_each(recruitment_cases.collaborator_user_ids_json) WHERE value=?))")
            values.extend([principal.user_id, principal.user_id])
        if status:
            if status not in {"reviewing", "interviewing", "finalized"}:
                raise ValueError("recruitment_case_status_invalid")
            conditions.append("status=?")
            values.append(status)
        conditions.append("archived_at<>''" if archived else "archived_at=''" )
        if folder_id == "unfiled":
            conditions.append("folder_id=''")
        elif folder_id:
            conditions.append("folder_id=?")
            values.append(folder_id)
        if query.strip():
            conditions.append("(case_name LIKE ? OR job_title LIKE ?)")
            search = f"%{query.strip()}%"
            values.extend([search, search])
        query = (
            "SELECT * FROM recruitment_cases WHERE " + " AND ".join(conditions)
            + " ORDER BY updated_at DESC"
        )
        with self._connect() as db:
            rows = db.execute(query, values).fetchall()
        return [self._record(row) for row in rows]

    def list_folders(self, principal: Principal) -> list[RecruitmentFolderRecord]:
        conditions = ["tenant_id=?"]
        values = [principal.tenant_id]
        if not principal.can(RUN_APPROVE):
            conditions.append("owner_user_id=?")
            values.append(principal.user_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM recruitment_case_folders WHERE " + " AND ".join(conditions)
                + " ORDER BY name COLLATE NOCASE",
                values,
            ).fetchall()
        return [self._folder_record(row) for row in rows]

    def list_interviewers(self, principal: Principal) -> list[RecruitmentInterviewerRecord]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT u.id,u.display_name,u.role FROM users u
                JOIN tenant_memberships m ON m.user_id=u.id
                WHERE m.tenant_id=? AND u.active=1
                ORDER BY u.display_name COLLATE NOCASE""",
                (principal.tenant_id,),
            ).fetchall()
        return [RecruitmentInterviewerRecord(
            user_id=str(row["id"]), display_name=str(row["display_name"]), role=str(row["role"]),
        ) for row in rows]

    def create_folder(self, name: str, principal: Principal) -> RecruitmentFolderRecord:
        clean = name.strip()
        if not clean:
            raise ValueError("recruitment_folder_name_required")
        now = _now()
        with self._connect() as db:
            duplicate = db.execute(
                "SELECT 1 FROM recruitment_case_folders WHERE tenant_id=? AND owner_user_id=? AND lower(name)=lower(?)",
                (principal.tenant_id, principal.user_id, clean),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("recruitment_folder_name_exists")
            folder_id = f"recruitment_folder_{uuid4().hex}"
            db.execute(
                "INSERT INTO recruitment_case_folders(id,tenant_id,owner_user_id,name,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (folder_id, principal.tenant_id, principal.user_id, clean, now, now),
            )
            row = db.execute("SELECT * FROM recruitment_case_folders WHERE id=?", (folder_id,)).fetchone()
        return self._folder_record(row)

    def rename_folder(self, folder_id: str, name: str, principal: Principal) -> RecruitmentFolderRecord:
        row = self._get_folder_row(folder_id, principal)
        clean = name.strip()
        if not clean:
            raise ValueError("recruitment_folder_name_required")
        with self._connect() as db:
            duplicate = db.execute(
                "SELECT 1 FROM recruitment_case_folders WHERE tenant_id=? AND owner_user_id=? AND id<>? AND lower(name)=lower(?)",
                (row["tenant_id"], row["owner_user_id"], folder_id, clean),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("recruitment_folder_name_exists")
            db.execute(
                "UPDATE recruitment_case_folders SET name=?,updated_at=? WHERE id=?",
                (clean, _now(), row["id"]),
            )
            updated = db.execute("SELECT * FROM recruitment_case_folders WHERE id=?", (folder_id,)).fetchone()
        return self._folder_record(updated)

    def delete_folder(self, folder_id: str, principal: Principal) -> None:
        row = self._get_folder_row(folder_id, principal)
        with self._connect() as db:
            db.execute(
                "UPDATE recruitment_cases SET folder_id='',updated_at=? WHERE tenant_id=? AND owner_user_id=? AND folder_id=?",
                (_now(), row["tenant_id"], row["owner_user_id"], folder_id),
            )
            db.execute("DELETE FROM recruitment_case_folders WHERE id=?", (folder_id,))

    def apply(
        self,
        case_id: str,
        action: RecruitmentCaseAction,
        principal: Principal,
    ) -> RecruitmentCaseRecord:
        current = self.get(case_id, principal)
        management_actions = {"rename_case", "move_case", "archive_case", "restore_case"}
        owner_actions = {
            *management_actions, "review_candidate", "record_interview", "finalize", "reopen",
            "schedule_interview", "cancel_interview",
        }
        can_manage = current.owner_user_id == principal.user_id or principal.can(RUN_APPROVE)
        if action.action in owner_actions and not can_manage:
            raise ValueError("recruitment_case_owner_required")
        if current.status == "finalized" and action.action not in {"reopen", *management_actions}:
            raise ValueError("recruitment_case_finalized")
        candidates = [item.model_copy(deep=True) for item in current.candidates]
        interview_sessions = [item.model_copy(deep=True) for item in current.interview_sessions]
        status = current.status
        final_ids = list(current.final_candidate_ids)
        final_rationale = current.final_rationale
        finalized_by = current.finalized_by
        finalized_at = current.finalized_at
        now = _now()

        if action.action in management_actions:
            updates: dict[str, str] = {"updated_at": now}
            if action.action == "rename_case":
                if not action.case_name.strip():
                    raise ValueError("recruitment_case_name_required")
                updates["case_name"] = action.case_name.strip()
            elif action.action == "move_case":
                if action.folder_id:
                    folder = self._get_folder_row(action.folder_id, principal)
                    if folder["owner_user_id"] != current.owner_user_id:
                        raise ValueError("recruitment_folder_owner_mismatch")
                updates["folder_id"] = action.folder_id
            elif action.action == "archive_case":
                updates["archived_at"] = now
            elif action.action == "restore_case":
                updates["archived_at"] = ""
            assignments = ",".join(f"{key}=?" for key in updates)
            with self._connect() as db:
                db.execute(
                    f"UPDATE recruitment_cases SET {assignments} WHERE id=?",
                    (*updates.values(), case_id),
                )
            return self.get(case_id, principal)

        if action.action == "review_candidate":
            candidate = self._candidate(candidates, action.candidate_id)
            if action.review_status is None:
                raise ValueError("review_status_required")
            candidate.review_status = action.review_status
            candidate.reviewer_note = action.reviewer_note.strip()
            candidate.reviewed_by = principal.display_name
            candidate.reviewed_at = now
            if action.review_status == "shortlisted":
                status = "interviewing"
        elif action.action == "record_interview":
            candidate = self._candidate(candidates, action.candidate_id)
            if action.interview_outcome is None or not action.interview_note.strip():
                raise ValueError("interview_outcome_and_note_required")
            candidate.interview_outcome = action.interview_outcome
            candidate.interview_notes.append(InterviewNote(
                id=f"interview_note_{uuid4().hex}",
                note=action.interview_note.strip(),
                outcome=action.interview_outcome,
                recorded_by=principal.display_name,
                recorded_at=now,
            ))
            status = "interviewing"
        elif action.action == "schedule_interview":
            candidate = self._candidate(candidates, action.candidate_id)
            if candidate.review_status != "shortlisted":
                raise ValueError("candidate_must_be_shortlisted")
            start_at = self._parse_datetime(action.interview_start_at, "interview_start_required")
            end_at = self._parse_datetime(action.interview_end_at, "interview_end_required")
            if end_at <= start_at or (end_at - start_at).total_seconds() > 8 * 60 * 60:
                raise ValueError("interview_time_range_invalid")
            if action.interview_mode is None:
                raise ValueError("interview_mode_required")
            if action.interview_mode == "online" and not action.interview_meeting_url.strip():
                raise ValueError("interview_meeting_url_required")
            if action.interview_mode == "onsite" and not action.interview_location.strip():
                raise ValueError("interview_location_required")
            members = self._interviewers(action.interviewer_ids, current.tenant_id)
            dimensions = list(dict.fromkeys(item.strip() for item in action.focus_dimensions if item.strip()))
            if not dimensions:
                raise ValueError("interview_focus_dimensions_required")
            existing = self._session(interview_sessions, action.interview_session_id, required=False)
            if existing is not None and existing.candidate_id != candidate.candidate_id:
                raise ValueError("interview_candidate_mismatch")
            session = InterviewSession(
                id=existing.id if existing else f"interview_session_{uuid4().hex}",
                candidate_id=candidate.candidate_id,
                title=action.interview_title.strip() or f"{candidate.candidate_name} · 结构化面试",
                start_at=start_at.isoformat(), end_at=end_at.isoformat(),
                timezone=action.interview_timezone.strip() or "Asia/Shanghai",
                mode=action.interview_mode,
                location=action.interview_location.strip(),
                meeting_url=action.interview_meeting_url.strip(),
                interviewers=members, focus_dimensions=dimensions,
                status="scheduled",
                feedback=[
                    item for item in (existing.feedback if existing else [])
                    if item.interviewer_user_id in {member.user_id for member in members}
                ],
                created_by=existing.created_by if existing else principal.display_name,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            if existing is None:
                interview_sessions.append(session)
            else:
                interview_sessions[interview_sessions.index(existing)] = session
            candidate.interview_outcome = "scheduled"
            status = "interviewing"
        elif action.action == "cancel_interview":
            session = self._session(interview_sessions, action.interview_session_id)
            assert session is not None
            session.status = "cancelled"
            session.cancelled_by = principal.display_name
            session.cancelled_at = now
            session.updated_at = now
            candidate = self._candidate(candidates, session.candidate_id)
            candidate.interview_outcome = self._candidate_interview_outcome(candidate.candidate_id, interview_sessions)
            status = "interviewing"
        elif action.action == "submit_interview_feedback":
            session = self._session(interview_sessions, action.interview_session_id)
            assert session is not None
            if session.status == "cancelled":
                raise ValueError("interview_session_cancelled")
            if principal.user_id not in {item.user_id for item in session.interviewers}:
                raise ValueError("interview_feedback_assignment_required")
            if action.recommendation is None or not action.evidence_note.strip():
                raise ValueError("interview_feedback_required")
            scores = {key.strip(): int(value) for key, value in action.dimension_scores.items() if key.strip()}
            expected = set(session.focus_dimensions)
            if set(scores) != expected or any(value < 1 or value > 5 for value in scores.values()):
                raise ValueError("interview_dimension_scores_invalid")
            existing_feedback = next(
                (item for item in session.feedback if item.interviewer_user_id == principal.user_id), None,
            )
            feedback = InterviewFeedback(
                id=existing_feedback.id if existing_feedback else f"interview_feedback_{uuid4().hex}",
                session_id=session.id, candidate_id=session.candidate_id,
                interviewer_user_id=principal.user_id, interviewer_name=principal.display_name,
                recommendation=action.recommendation, dimension_scores=scores,
                evidence_note=action.evidence_note.strip(), risk_note=action.risk_note.strip(),
                submitted_at=existing_feedback.submitted_at if existing_feedback else now,
                updated_at=now,
            )
            if existing_feedback is None:
                session.feedback.append(feedback)
            else:
                session.feedback[session.feedback.index(existing_feedback)] = feedback
            session.status = "completed" if {item.interviewer_user_id for item in session.feedback} >= {item.user_id for item in session.interviewers} else "scheduled"
            session.updated_at = now
            candidate = self._candidate(candidates, session.candidate_id)
            candidate.interview_outcome = self._candidate_interview_outcome(candidate.candidate_id, interview_sessions)
            status = "interviewing"
        elif action.action == "finalize":
            selected = list(dict.fromkeys(action.final_candidate_ids))
            known = {item.candidate_id for item in candidates}
            if not selected:
                raise ValueError("final_candidates_required")
            if len(selected) > current.selection_count:
                raise ValueError("final_candidates_exceed_selection_count")
            if any(candidate_id not in known for candidate_id in selected):
                raise ValueError("candidate_not_found")
            if not action.final_rationale.strip():
                raise ValueError("final_rationale_required")
            final_ids = selected
            final_rationale = action.final_rationale.strip()
            finalized_by = principal.display_name
            finalized_at = now
            status = "finalized"
        elif action.action == "reopen":
            status = "interviewing" if any(item.review_status == "shortlisted" for item in candidates) else "reviewing"
            final_ids = []
            final_rationale = ""
            finalized_by = ""
            finalized_at = ""

        with self._connect() as db:
            collaborator_ids = sorted({
                member.user_id
                for session in interview_sessions if session.status != "cancelled"
                for member in session.interviewers
                if member.user_id != current.owner_user_id
            })
            db.execute(
                """UPDATE recruitment_cases SET status=?,candidates_json=?,final_candidate_ids_json=?,
                final_rationale=?,finalized_by=?,finalized_at=?,collaborator_user_ids_json=?,
                interview_sessions_json=?,updated_at=? WHERE id=?""",
                (
                    status,
                    json.dumps([item.model_dump(mode="json") for item in candidates], ensure_ascii=False),
                    json.dumps(final_ids, ensure_ascii=False), final_rationale, finalized_by,
                    finalized_at, json.dumps(collaborator_ids, ensure_ascii=False),
                    json.dumps([item.model_dump(mode="json") for item in interview_sessions], ensure_ascii=False),
                    now, case_id,
                ),
            )
        return self.get(case_id, principal)

    @staticmethod
    def _candidate(candidates: list[CandidateReviewRecord], candidate_id: str) -> CandidateReviewRecord:
        for candidate in candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise ValueError("candidate_not_found")

    @staticmethod
    def _session(
        sessions: list[InterviewSession], session_id: str, *, required: bool = True,
    ) -> InterviewSession | None:
        if session_id:
            for session in sessions:
                if session.id == session_id:
                    return session
        if required:
            raise ValueError("interview_session_not_found")
        return None

    @staticmethod
    def _parse_datetime(value: str, error: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise ValueError(error) from None
        return parsed

    def _interviewers(self, user_ids: list[str], tenant_id: str) -> list[InterviewPanelMember]:
        ordered = list(dict.fromkeys(item.strip() for item in user_ids if item.strip()))
        if not ordered:
            raise ValueError("interviewers_required")
        placeholders = ",".join("?" for _ in ordered)
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT u.id,u.display_name FROM users u
                JOIN tenant_memberships m ON m.user_id=u.id
                WHERE m.tenant_id=? AND u.active=1 AND u.id IN ({placeholders})""",
                (tenant_id, *ordered),
            ).fetchall()
        found = {str(row["id"]): str(row["display_name"]) for row in rows}
        if set(found) != set(ordered):
            raise ValueError("interviewers_invalid")
        return [InterviewPanelMember(user_id=user_id, display_name=found[user_id]) for user_id in ordered]

    @staticmethod
    def _candidate_interview_outcome(candidate_id: str, sessions: list[InterviewSession]) -> str:
        active = [item for item in sessions if item.candidate_id == candidate_id and item.status != "cancelled"]
        if not active:
            return "not_scheduled"
        if any(item.status != "completed" for item in active):
            return "scheduled"
        weights = {"strong_yes": 2, "yes": 1, "hold": 0, "no": -1, "strong_no": -2}
        recommendations = [weights[item.recommendation] for session in active for item in session.feedback]
        if not recommendations:
            return "scheduled"
        average = sum(recommendations) / len(recommendations)
        if average >= .5:
            return "passed"
        if average <= -.5:
            return "not_advanced"
        return "hold"

    def calendar_event(self, case_id: str, session_id: str, principal: Principal) -> tuple[str, str]:
        case = self.get(case_id, principal)
        session = self._session(case.interview_sessions, session_id)
        assert session is not None
        candidate = self._candidate(case.candidates, session.candidate_id)
        try:
            zone = ZoneInfo(session.timezone)
        except Exception:
            zone = ZoneInfo("Asia/Shanghai")
        start = self._parse_datetime(session.start_at, "interview_start_required")
        end = self._parse_datetime(session.end_at, "interview_end_required")
        if start.tzinfo is None:
            start = start.replace(tzinfo=zone)
        if end.tzinfo is None:
            end = end.replace(tzinfo=zone)

        def utc_stamp(value: datetime) -> str:
            return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        def escape(value: str) -> str:
            return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

        location = session.meeting_url if session.mode == "online" else session.location
        description = "核实重点：" + "、".join(session.focus_dimensions)
        content = "\r\n".join([
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//UniHR AgentOS//Recruitment Interview//CN",
            "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "BEGIN:VEVENT",
            f"UID:{escape(session.id)}@unihr-agentos", f"DTSTAMP:{utc_stamp(datetime.now(timezone.utc))}",
            f"DTSTART:{utc_stamp(start)}", f"DTEND:{utc_stamp(end)}",
            f"SUMMARY:{escape(session.title)}", f"DESCRIPTION:{escape(description)}",
            f"LOCATION:{escape(location)}", f"URL:{escape(session.meeting_url)}" if session.meeting_url else "URL:",
            f"CATEGORIES:{escape(case.case_name)}", "END:VEVENT", "END:VCALENDAR", "",
        ])
        safe_name = "".join(char for char in candidate.candidate_name if char not in '\\/:*?"<>|') or "候选人"
        return f"{safe_name}-面试安排.ics", content

    @staticmethod
    def _can_access(row: sqlite3.Row, principal: Principal) -> bool:
        collaborators = set(json.loads(row["collaborator_user_ids_json"] or "[]")) if "collaborator_user_ids_json" in row.keys() else set()
        return row["tenant_id"] == principal.tenant_id and (
            row["owner_user_id"] == principal.user_id
            or principal.user_id in collaborators
            or principal.can(RUN_APPROVE)
        )

    def _get_folder_row(self, folder_id: str, principal: Principal) -> sqlite3.Row:
        with self._connect() as db:
            row = db.execute("SELECT * FROM recruitment_case_folders WHERE id=?", (folder_id,)).fetchone()
        if row is None or not self._can_access(row, principal):
            raise KeyError("recruitment_folder_not_found")
        return row

    @staticmethod
    def _folder_record(row: sqlite3.Row) -> RecruitmentFolderRecord:
        return RecruitmentFolderRecord(
            id=row["id"], tenant_id=row["tenant_id"], owner_user_id=row["owner_user_id"],
            name=row["name"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> RecruitmentCaseRecord:
        return RecruitmentCaseRecord(
            id=row["id"], tenant_id=row["tenant_id"], owner_user_id=row["owner_user_id"],
            conversation_id=row["conversation_id"], run_id=row["run_id"],
            artifact_id=row["artifact_id"], job_title=row["job_title"],
            case_name=row["case_name"] or row["job_title"], folder_id=row["folder_id"],
            archived_at=row["archived_at"],
            rubric_hash=row["rubric_hash"], selection_count=int(row["selection_count"]),
            status=row["status"],
            candidates=[CandidateReviewRecord.model_validate(item) for item in json.loads(row["candidates_json"])],
            collaborator_user_ids=list(json.loads(row["collaborator_user_ids_json"] or "[]")),
            interview_sessions=[InterviewSession.model_validate(item) for item in json.loads(row["interview_sessions_json"] or "[]")],
            final_candidate_ids=list(json.loads(row["final_candidate_ids_json"])),
            final_rationale=row["final_rationale"], finalized_by=row["finalized_by"],
            finalized_at=row["finalized_at"], created_at=row["created_at"], updated_at=row["updated_at"],
        )
