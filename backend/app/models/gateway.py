from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol, Sequence

import httpx

from app.settings import Settings
from .usage_store import ModelUsageStore


Message = dict[str, Any]


@dataclass(frozen=True)
class ModelReply:
    content: str
    model: str
    latency_ms: int
    usage: dict[str, Any]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ModelGatewayError(RuntimeError):
    pass


class ModelGateway(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        *,
        purpose: str,
        model: str | None = None,
        structured: bool = False,
        max_tokens: int = 2_000,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ModelReply: ...


class BailianModelGateway:
    """One OpenAI-compatible seam for all UniHR model calls."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.request_timeout_seconds)
        self.usage_store = ModelUsageStore(settings) if settings.database_path is not None else None
        self._protocol_blocked_models: set[str] = set()

    @property
    def available(self) -> bool:
        return bool(self.settings.model_api_key)

    @property
    def supports_tools(self) -> bool:
        return True

    def complete(
        self,
        messages: Sequence[Message],
        *,
        purpose: str,
        model: str | None = None,
        structured: bool = False,
        max_tokens: int = 2_000,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ModelReply:
        if not self.settings.model_api_key:
            raise ModelGatewayError("model_credentials_unavailable")
        resolved_model = model or (
            self.settings.router_model if purpose == "turn_interpretation" else self.settings.primary_model
        )
        model_candidates = [resolved_model]
        if (
            purpose == "turn_interpretation"
            and resolved_model == self.settings.router_model
            and self.settings.router_fallback_model
            and self.settings.router_fallback_model != resolved_model
        ):
            model_candidates.append(self.settings.router_fallback_model)
        if (
            resolved_model == self.settings.primary_model
            and self.settings.primary_fallback_model
            and self.settings.primary_fallback_model != resolved_model
            and purpose not in {"vision_understanding", "image_evidence_extraction", "ocr_extraction"}
        ):
            model_candidates.append(self.settings.primary_fallback_model)
        if (
            resolved_model == self.settings.primary_model
            and self.settings.primary_last_resort_model
            and self.settings.primary_last_resort_model not in model_candidates
            and purpose not in {"vision_understanding", "image_evidence_extraction", "ocr_extraction"}
        ):
            model_candidates.append(self.settings.primary_last_resort_model)
        available_candidates = [
            item for item in model_candidates if item not in self._protocol_blocked_models
        ]
        if available_candidates:
            model_candidates = available_candidates
        last_error: Exception | None = None
        for attempt, candidate_model in enumerate(model_candidates):
            use_responses = candidate_model.startswith("qwen3.8")
            payload = (
                self._responses_payload(
                    candidate_model, messages, max_tokens=max_tokens,
                    tools=tools, tool_choice=tool_choice,
                )
                if use_responses
                else self._chat_payload(
                    candidate_model, messages, structured=structured,
                    max_tokens=max_tokens, tools=tools, tool_choice=tool_choice,
                )
            )
            started = perf_counter()
            try:
                base_url = (
                    self.settings.responses_base_url.strip()
                    if use_responses and self.settings.responses_base_url.strip()
                    else self.settings.model_base_url
                )
                response = self.client.post(
                    f"{base_url.rstrip('/')}/{'responses' if use_responses else 'chat/completions'}",
                    headers={
                        "Authorization": f"Bearer {self.settings.model_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
                content, tool_calls = (
                    self._parse_responses_body(body) if use_responses
                    else self._parse_chat_body(body)
                )
                usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
                reply = ModelReply(
                    content=content,
                    model=candidate_model,
                    latency_ms=max(0, int((perf_counter() - started) * 1000)),
                    usage=usage,
                    tool_calls=tool_calls,
                )
                self._record_usage(
                    model=candidate_model, purpose=purpose, usage=usage,
                    latency_ms=reply.latency_ms, status="succeeded",
                    metadata=(
                        {"fallback_from": resolved_model, "fallback_attempt": attempt}
                        if attempt else None
                    ),
                )
                return reply
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
                if (
                    use_responses
                    and isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in {400, 403, 404, 422}
                ):
                    # A Token Plan/workspace protocol mismatch will not heal during this
                    # process. Skip repeated failing probes until the backend restarts.
                    self._protocol_blocked_models.add(candidate_model)
                self._record_usage(
                    model=candidate_model, purpose=purpose, usage={},
                    latency_ms=max(0, int((perf_counter() - started) * 1000)),
                    status="failed", metadata={
                        "error_code": type(exc).__name__,
                        "fallback_available": attempt + 1 < len(model_candidates),
                    },
                )
        raise ModelGatewayError(type(last_error).__name__ if last_error else "model_call_failed") from last_error

    def _chat_payload(
        self, model: str, messages: Sequence[Message], *, structured: bool,
        max_tokens: int, tools: Sequence[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "enable_thinking": False,
            "max_tokens": max_tokens,
            "temperature": 0.2 if structured else 0.4,
        }
        if structured:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = tool_choice or "auto"
        return payload

    def _responses_payload(
        self, model: str, messages: Sequence[Message], *, max_tokens: int,
        tools: Sequence[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "input": list(messages),
            "max_output_tokens": max(max_tokens, 8_192),
            "reasoning": {"effort": self.settings.primary_reasoning_effort},
        }
        if tools:
            translated: list[dict[str, Any]] = []
            for raw in tools:
                function = dict(raw.get("function") or {})
                translated.append({
                    "type": "function",
                    "name": str(function.get("name") or raw.get("name") or ""),
                    "description": str(function.get("description") or raw.get("description") or ""),
                    "parameters": function.get("parameters") or raw.get("parameters") or {"type": "object"},
                })
            payload["tools"] = translated
            if isinstance(tool_choice, str) or tool_choice is None:
                payload["tool_choice"] = tool_choice or "auto"
            elif isinstance(tool_choice, dict):
                function = dict(tool_choice.get("function") or {})
                payload["tool_choice"] = {
                    "type": "function", "name": str(function.get("name") or ""),
                }
        return payload

    @staticmethod
    def _parse_chat_body(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        message = body["choices"][0]["message"]
        content = message.get("content") or ""
        if not isinstance(content, str):
            raise TypeError("provider returned invalid content")
        tool_calls: list[dict[str, Any]] = []
        for raw in message.get("tool_calls") or []:
            function = dict(raw.get("function") or {})
            arguments = function.get("arguments") or "{}"
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise TypeError("provider returned invalid tool arguments")
            tool_calls.append({
                "id": str(raw.get("id") or ""), "type": "function",
                "function": {"name": str(function.get("name") or ""), "arguments": arguments},
            })
        return content, tool_calls

    @staticmethod
    def _parse_responses_body(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for item in body.get("output") or []:
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        content_parts.append(part["text"])
            elif item.get("type") == "function_call":
                arguments = item.get("arguments") or "{}"
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                if not isinstance(arguments, dict):
                    raise TypeError("provider returned invalid tool arguments")
                tool_calls.append({
                    "id": str(item.get("call_id") or item.get("id") or ""),
                    "type": "function",
                    "function": {"name": str(item.get("name") or ""), "arguments": arguments},
                })
        content = "\n".join(content_parts).strip()
        if not isinstance(content, str):
            raise TypeError("provider returned invalid content")
        return content, tool_calls

    def _record_usage(
        self,
        *,
        model: str,
        purpose: str,
        usage: dict[str, Any],
        latency_ms: int,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.usage_store is None:
            return
        try:
            self.usage_store.record(
                provider="bailian",
                model=model,
                purpose=purpose,
                operation_kind="chat",
                input_units=int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                output_units=int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                unit_type="tokens",
                latency_ms=latency_ms,
                status=status,
                metadata=metadata,
            )
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return


def parse_json_object(content: str) -> dict[str, Any]:
    candidate = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.S | re.I)
    if fenced:
        candidate = fenced.group(1)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        # Some compatible models occasionally wrap an otherwise valid object
        # in a short explanation or a non-exact markdown fence. Accept the
        # outermost complete object while preserving strict JSON validation.
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(candidate[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("structured model result must be an object")
    return value
