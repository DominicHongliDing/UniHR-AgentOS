from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


SERVER_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_ROOT.parent
DEFAULT_DATASET = SERVER_ROOT / "data" / "evaluations" / "harness-50-v1.jsonl"


def load_cases(path: Path) -> list[dict[str, str]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(cases) != 50:
        raise ValueError(f"Harness challenge set must contain exactly 50 cases, found {len(cases)}")
    ids = [case["case_id"] for case in cases]
    nodeids = [case["nodeid"] for case in cases]
    if len(set(ids)) != len(ids) or len(set(nodeids)) != len(nodeids):
        raise ValueError("Harness challenge case IDs and pytest node IDs must be unique")
    return cases


def junit_statuses(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    statuses: dict[str, str] = {}
    for testcase in root.iter("testcase"):
        classname = testcase.attrib.get("classname", "")
        name = testcase.attrib.get("name", "")
        file_path = classname.replace(".", "/") + ".py"
        nodeid = f"{file_path}::{name}"
        if testcase.find("failure") is not None or testcase.find("error") is not None:
            statuses[nodeid] = "failed"
        elif testcase.find("skipped") is not None:
            statuses[nodeid] = "skipped"
        else:
            statuses[nodeid] = "passed"
    return statuses


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the UniHR Harness 50-case challenge set")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    cases = load_cases(dataset)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = (args.output_dir or REPO_ROOT / "outputs" / f"unihr-harness-50-{stamp}").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    junit_path = output_dir / "pytest-junit.xml"
    raw_output_path = output_dir / "pytest-output.txt"

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--disable-warnings",
        f"--junitxml={junit_path}",
        *[case["nodeid"] for case in cases],
    ]
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    started_at = datetime.now(timezone.utc)
    completed = subprocess.run(
        command,
        cwd=SERVER_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    finished_at = datetime.now(timezone.utc)
    raw_output_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")

    statuses = junit_statuses(junit_path) if junit_path.exists() else {}
    results = []
    for case in cases:
        status = statuses.get(case["nodeid"], "not_collected")
        results.append({**case, "status": status})

    status_counts = Counter(result["status"] for result in results)
    domain_counts: dict[str, dict[str, int | float]] = {}
    for domain in sorted({case["domain"] for case in cases}):
        domain_results = [result for result in results if result["domain"] == domain]
        passed = sum(result["status"] == "passed" for result in domain_results)
        domain_counts[domain] = {
            "total": len(domain_results),
            "passed": passed,
            "pass_rate": round(passed / len(domain_results), 4),
        }

    behavior_counts: dict[str, dict[str, int | float]] = {}
    for behavior in sorted({case["behavior"] for case in cases}):
        behavior_results = [result for result in results if result["behavior"] == behavior]
        passed = sum(result["status"] == "passed" for result in behavior_results)
        behavior_counts[behavior] = {
            "total": len(behavior_results),
            "passed": passed,
            "pass_rate": round(passed / len(behavior_results), 4),
        }

    passed_count = status_counts.get("passed", 0)
    report = {
        "suite": "unihr-harness-50",
        "dataset_version": "harness-50-v1",
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "execution_mode": "current_code_deterministic_regression",
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "exit_code": completed.returncode,
        "total": len(results),
        "passed": passed_count,
        "failed": status_counts.get("failed", 0),
        "skipped": status_counts.get("skipped", 0),
        "not_collected": status_counts.get("not_collected", 0),
        "pass_rate": round(passed_count / len(results), 4),
        "by_domain": domain_counts,
        "by_expected_behavior": behavior_counts,
        "results": results,
        "evidence": {
            "dataset": str(dataset),
            "junit": str(junit_path),
            "pytest_output": str(raw_output_path),
        },
        "interpretation_boundary": (
            "A passing case proves that current code met the encoded deterministic contract for this fixture. "
            "It is not a live-model hallucination rate or production HR decision accuracy."
        ),
    }
    report_path = output_dir / "harness-50-result.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "output_dir": str(output_dir),
        "total": report["total"],
        "passed": report["passed"],
        "failed": report["failed"],
        "skipped": report["skipped"],
        "pass_rate": report["pass_rate"],
        "duration_seconds": report["duration_seconds"],
    }, ensure_ascii=False, indent=2))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
