"""Check whether a real Feishu message completed the live channel loop."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-text", required=True)
    parser.add_argument(
        "--database",
        default=str(Path(__file__).resolve().parents[1] / "data" / "unihr.db"),
    )
    args = parser.parse_args()

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT event_id, status, attempts, internal_run_id,
               external_result_message_id, error_code, message_json,
               created_at, updated_at
          FROM channel_events
         WHERE provider = 'feishu'
           AND event_id NOT LIKE 'evt-replay-%'
         ORDER BY created_at DESC
         LIMIT 100
        """
    ).fetchall()

    expected = args.expect_text.casefold()
    for row in rows:
        try:
            message = json.loads(row["message_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if expected not in str(message.get("text", "")).casefold():
            continue

        delivered = (
            row["status"] == "delivered"
            and bool(row["internal_run_id"])
            and bool(row["external_result_message_id"])
            and not row["error_code"]
        )
        verdict = "GREEN" if delivered else "RED"
        print(
            json.dumps(
                {
                    "verdict": verdict,
                    "stage": "complete" if delivered else "processing_or_delivery",
                    "event_id": row["event_id"],
                    "status": row["status"],
                    "attempts": row["attempts"],
                    "internal_run_id": row["internal_run_id"],
                    "external_result_message_id": row["external_result_message_id"],
                    "error_code": row["error_code"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                },
                ensure_ascii=False,
            )
        )
        return 0 if delivered else 1

    print(
        json.dumps(
            {
                "verdict": "RED",
                "stage": "ingress_missing",
                "expected_text": args.expect_text,
                "live_event_count": len(rows),
            },
            ensure_ascii=False,
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
