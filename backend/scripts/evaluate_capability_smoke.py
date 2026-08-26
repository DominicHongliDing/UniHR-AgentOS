from __future__ import annotations

import json
import os
from pathlib import Path
from time import perf_counter, sleep

import httpx

from app.domains.capability_acceptance import (
    CapabilityAcceptanceEvaluator,
    CapabilityObservation,
    default_capability_smoke_cases,
    observe_skill_record,
    observe_workbench_turn,
)
from app.domains.skills import SkillRecord
from app.domains.turns import TurnExecutionRecord, TurnExecutionStatus


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "evaluations" / "capability-smoke" / "latest.json"
API_BASE = os.getenv("UNIHR_SMOKE_API_BASE", "http://127.0.0.1:8088/api/v1")


def main() -> None:
    client = httpx.Client(base_url=API_BASE, timeout=240)
    login = client.post("/auth/login", json={
        "username": os.getenv("UNIHR_DEMO_USERNAME", "hr-user"),
        "password": os.getenv("UNIHR_DEMO_PASSWORD", "UniHR@2026"),
    })
    login.raise_for_status()
    selected_ids = {
        item.strip() for item in os.getenv("UNIHR_SMOKE_CASE_IDS", "").split(",") if item.strip()
    }
    previous = _previous_payload() if selected_ids else {}
    observations_by_id = {
        item.case_id: item
        for item in (
            CapabilityObservation.model_validate(raw)
            for raw in previous.get("observations", [])
        )
        if item.case_id not in selected_ids
    }
    conversations: dict[str, str] = dict(previous.get("conversations") or {})
    created_skills: dict[str, str] = dict(previous.get("created_skills") or {})

    for case in default_capability_smoke_cases():
        if selected_ids and case.id not in selected_ids:
            continue
        started = perf_counter()
        if case.capability == "skill":
            response = client.post("/skills/generate", json={
                "prompt": case.prompt,
                "preferred_name": "招聘进度月报",
            })
            response.raise_for_status()
            record = SkillRecord.model_validate(response.json())
            observations_by_id[case.id] = observe_skill_record(
                case.id, record, duration_ms=_duration_ms(started),
            )
            created_skills[case.id] = record.id
            continue

        files = _files(case.capability)
        response = client.post("/assistant/turns:start", data={
            "message": case.prompt,
            "task_mode": case.task_mode,
            "document_font": "仿宋_GB2312",
            "document_font_size": "16",
        }, files=files)
        response.raise_for_status()
        execution = TurnExecutionRecord.model_validate(response.json())
        execution = _wait(client, execution)
        if execution.status != TurnExecutionStatus.SUCCEEDED or execution.result is None:
            raise RuntimeError(f"{case.id}:{execution.status}:{execution.error_code}")
        observations_by_id[case.id] = observe_workbench_turn(
            case.id, execution.result, duration_ms=_duration_ms(started),
        )
        conversations[case.id] = execution.result.conversation.id

    observations = [
        observations_by_id[case.id]
        for case in default_capability_smoke_cases()
        if case.id in observations_by_id
    ]
    report = CapabilityAcceptanceEvaluator().evaluate(observations)
    payload = {
        "report": report.model_dump(mode="json"),
        "observations": [item.model_dump(mode="json") for item in observations],
        "conversations": conversations,
        "created_skills": created_skills,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _previous_payload() -> dict[str, object]:
    if not OUTPUT.exists():
        return {}
    try:
        value = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _wait(client: httpx.Client, record: TurnExecutionRecord) -> TurnExecutionRecord:
    deadline = perf_counter() + 240
    while record.status not in {
        TurnExecutionStatus.SUCCEEDED,
        TurnExecutionStatus.FAILED,
        TurnExecutionStatus.CANCELLED,
    }:
        if perf_counter() >= deadline:
            raise TimeoutError(record.id)
        sleep(0.5)
        response = client.get(f"/assistant/turns/{record.id}")
        response.raise_for_status()
        record = TurnExecutionRecord.model_validate(response.json())
    return record


def _files(capability: str) -> list[tuple[str, tuple[str, bytes, str]]]:
    if capability != "recruitment":
        return []
    return [
        ("files", ("候选人甲.txt", (
            "候选人甲\n参与产业合作项目，独立对接两家合作单位，完成合同资料跟进。"
        ).encode("utf-8"), "text/plain")),
        ("files", ("候选人乙.txt", (
            "候选人乙\n负责校园活动外联和赞助沟通，完成三场活动执行与数据复盘。"
        ).encode("utf-8"), "text/plain")),
    ]


def _duration_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


if __name__ == "__main__":
    main()
