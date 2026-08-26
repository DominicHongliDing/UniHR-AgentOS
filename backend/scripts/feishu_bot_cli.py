from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.domains.workplace_connectors.feishu_bot import (
    FeishuBotClient,
    FeishuBotConfig,
    FeishuBotError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="UniHR Feishu bot CLI. External writes require --commit."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Validate local bot configuration")
    status.add_argument("--live", action="store_true", help="Request a tenant token")

    text = subparsers.add_parser("send-text", help="Send a text message")
    text.add_argument("--chat-id", required=True)
    text.add_argument("--text", required=True)
    text.add_argument("--commit", action="store_true")

    file_parser = subparsers.add_parser("send-file", help="Send a local file")
    file_parser.add_argument("--chat-id", required=True)
    file_parser.add_argument("--file", type=Path, required=True)
    file_parser.add_argument("--caption")
    file_parser.add_argument("--commit", action="store_true")

    import_parser = subparsers.add_parser(
        "import-docx", help="Import a local DOCX as an editable Feishu document"
    )
    import_parser.add_argument("--file", type=Path, required=True)
    import_parser.add_argument(
        "--folder-token",
        default=os.getenv("UNIHR_FEISHU_FOLDER_TOKEN", ""),
        help="Writable Feishu Drive folder token, or UNIHR_FEISHU_FOLDER_TOKEN",
    )
    import_parser.add_argument("--title")
    import_parser.add_argument("--share-chat-id")
    import_parser.add_argument(
        "--share-perm", choices=("view", "edit", "full_access"), default="view"
    )
    import_parser.add_argument("--announce-chat-id")
    import_parser.add_argument("--poll-interval", type=float, default=2.0)
    import_parser.add_argument("--max-polls", type=int, default=30)
    import_parser.add_argument("--commit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "send-text" and not args.commit:
            print(
                json.dumps(
                    {
                        "status": "planned",
                        "action": "send_text",
                        "chat_id": args.chat_id,
                        "text_chars": len(args.text),
                        "commit_required": True,
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        if args.command == "send-file":
            resolved = args.file.expanduser().resolve()
            FeishuBotClient.validate_file(resolved)
            if not args.commit:
                print(
                    json.dumps(
                        {
                            "status": "planned",
                            "action": "send_file",
                            "chat_id": args.chat_id,
                            "file": str(resolved),
                            "bytes": resolved.stat().st_size,
                            "caption": bool(args.caption),
                            "commit_required": True,
                        },
                        ensure_ascii=False,
                    )
                )
                return 0

        if args.command == "import-docx":
            resolved = args.file.expanduser().resolve()
            FeishuBotClient.validate_import_file(resolved)
            if not args.folder_token.strip():
                raise FeishuBotError("folder_token_missing")
            if not args.commit:
                print(
                    json.dumps(
                        {
                            "status": "planned",
                            "action": "import_docx",
                            "file": str(resolved),
                            "bytes": resolved.stat().st_size,
                            "folder_token_present": True,
                            "title": args.title or resolved.stem,
                            "share_chat_id": args.share_chat_id,
                            "share_perm": args.share_perm if args.share_chat_id else None,
                            "announce_chat_id": args.announce_chat_id,
                            "max_polls": args.max_polls,
                            "commit_required": True,
                        },
                        ensure_ascii=False,
                    )
                )
                return 0

        config = FeishuBotConfig.from_env()
        client = FeishuBotClient(config)
        try:
            if args.command == "status":
                output = {"status": "configured", "live_checked": False}
                if args.live:
                    client._tenant_access_token()
                    output = {"status": "connected", "live_checked": True}
                print(json.dumps(output, ensure_ascii=False))
                return 0

            if args.command == "send-text":
                receipt = client.send_text(receive_id=args.chat_id, text=args.text)
                print(json.dumps(receipt.safe_dict(), ensure_ascii=False))
                return 0

            if args.command == "send-file":
                receipt = client.send_file(
                    receive_id=args.chat_id,
                    file_path=args.file,
                    caption=args.caption,
                )
            else:
                receipt = client.import_docx(
                    file_path=args.file,
                    folder_token=args.folder_token,
                    title=args.title,
                    share_chat_id=args.share_chat_id,
                    share_perm=args.share_perm,
                    announce_chat_id=args.announce_chat_id,
                    poll_interval_seconds=args.poll_interval,
                    max_polls=args.max_polls,
                )
            print(json.dumps(receipt.safe_dict(), ensure_ascii=False))
            return 0
        finally:
            client.close()
    except FeishuBotError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
