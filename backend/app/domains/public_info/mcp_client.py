from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from itertools import count
from typing import Any

import httpx


@dataclass(frozen=True)
class McpToolResult:
    text: str = ""
    structured: dict[str, Any] = field(default_factory=dict)


class StreamableHttpMcpClient:
    """Small synchronous MCP client for governed, read-only remote tools."""

    def __init__(
        self,
        endpoint: str,
        *,
        token: str = "",
        timeout_seconds: float = 25.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self._ids = count(1)
        self._initialized = False
        self._session_id = ""
        self._lock = threading.Lock()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        self._ensure_initialized()
        response = self._request(
            {"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call", "params": {
                "name": name, "arguments": arguments,
            }}
        )
        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            raise RuntimeError("mcp_tool_result_missing")
        structured = result.get("structuredContent")
        structured = structured if isinstance(structured, dict) else {}
        texts = [
            str(item.get("text") or "")
            for item in result.get("content") or []
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if result.get("isError"):
            code = str(structured.get("code") or "mcp_tool_error")
            raise RuntimeError(code)
        return McpToolResult(text="\n".join(filter(None, texts)), structured=structured)

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._request({
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "unihr-agentos", "version": "0.1.0"},
                },
            })
            # Streamable HTTP servers may be stateless. The notification is still
            # sent for protocol-compliant stateful servers.
            self._request({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_body=False)
            self._initialized = True

    def _request(self, payload: dict[str, Any], *, expect_body: bool = True) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response = self.client.post(self.endpoint, headers=headers, json=payload)
        response.raise_for_status()
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        if not expect_body or not response.text.strip():
            return {}
        body = self._decode_response(response)
        if isinstance(body.get("error"), dict):
            raise RuntimeError(str(body["error"].get("message") or "mcp_protocol_error"))
        return body

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "").casefold()
        if "text/event-stream" not in content_type:
            body = response.json()
            if not isinstance(body, dict):
                raise RuntimeError("mcp_response_not_object")
            return body
        data_lines: list[str] = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            raise RuntimeError("mcp_sse_data_missing")
        body = json.loads("\n".join(data_lines))
        if not isinstance(body, dict):
            raise RuntimeError("mcp_response_not_object")
        return body
