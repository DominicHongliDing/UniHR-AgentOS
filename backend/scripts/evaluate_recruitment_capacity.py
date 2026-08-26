from __future__ import annotations

import argparse
from pathlib import Path

from app.domains.recruitment import RecruitmentCapacityEvaluator


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the fixed 30-resume recruitment Graph capacity baseline."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/evaluations/recruitment-capacity"),
    )
    args = parser.parse_args()
    report = RecruitmentCapacityEvaluator(args.output_dir).evaluate()
    print(report.model_dump_json(indent=2))
    return 0 if report.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
