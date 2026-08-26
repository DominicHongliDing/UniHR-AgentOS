from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.settings import Settings


HOST = "127.0.0.1"
PORT = int(os.getenv("UNIHR_FEISHU_EXTERNAL_PROXY_PORT", "8090"))
BACKEND_URL = "http://127.0.0.1:8088/api/v1/channels/feishu/external/events"
MAX_BODY_BYTES = 64_000


def public_path(secret: str) -> str:
    token = hmac.new(
        secret.encode("utf-8"), b"unihr-external-public-path", hashlib.sha256,
    ).hexdigest()[:40]
    return f"/relay/{token}"


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "UniHRExternalRelay/1.0"

    def do_GET(self) -> None:
        if self.path != "/health":
            self._json(404, {"detail": "not_found"})
            return
        self._json(200, {"status": "ok", "service": "unihr-external-relay"})

    def do_POST(self) -> None:
        secret = str(getattr(self.server, "relay_secret"))
        if not hmac.compare_digest(self.path, public_path(secret)):
            self._json(404, {"detail": "not_found"})
            return
        content_type = self.headers.get("Content-Type", "").casefold()
        if "application/json" not in content_type:
            self._json(415, {"detail": "json_required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"detail": "invalid_content_length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json(413, {"detail": "invalid_body_size"})
            return
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(422, {"detail": "invalid_json"})
            return
        required = {"group_id", "message_id", "sender_id", "text"}
        if not isinstance(payload, dict) or not required.issubset(payload):
            self._json(422, {"detail": "invalid_external_event"})
            return
        canonical_body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        timestamp = str(int(time.time()))
        signature = hmac.new(
            secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + canonical_body,
            hashlib.sha256,
        ).hexdigest()
        request = Request(
            BACKEND_URL,
            data=canonical_body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-UniHR-Timestamp": timestamp,
                "X-UniHR-Signature": f"sha256={signature}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=8) as response:
                response_body = response.read(MAX_BODY_BYTES)
                status = response.status
        except HTTPError as exc:
            response_body = exc.read(MAX_BODY_BYTES)
            status = exc.code
        except URLError:
            self._json(502, {"detail": "unihr_backend_unavailable"})
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format: str, *args: object) -> None:
        # Never log the secret-bearing request path.
        safe_path = "/health" if self.path == "/health" else "/relay/[redacted]"
        print(f"{self.address_string()} {self.command} {safe_path}", flush=True)

    def _json(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    settings = Settings.from_env()
    if not settings.feishu_external_relay_enabled:
        raise RuntimeError("feishu_external_relay_disabled")
    if not settings.feishu_external_relay_secret:
        raise RuntimeError("feishu_external_relay_secret_missing")
    server = ThreadingHTTPServer((HOST, PORT), RelayHandler)
    server.relay_secret = settings.feishu_external_relay_secret  # type: ignore[attr-defined]
    print(f"UniHR external relay listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
