from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from app.domains.context_lifecycle import PreparedContext
from app.domains.identity import Principal
from app.models.gateway import ModelGateway, ModelGatewayError

from .workspace import ResourceLocator, ResourceWorkspace


_RESOURCE_QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "resource_workspace_query",
        "description": "只读打开当前对话工作区中的一个文件局部。PDF按页、Word按章节、Excel按Sheet和范围、文本按行或关键词读取。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["resource_id"],
            "properties": {
                "resource_id": {"type": "string"},
                "page_start": {"type": "integer", "minimum": 1},
                "page_end": {"type": "integer", "minimum": 1},
                "section": {"type": "string"},
                "sheet": {"type": "string"},
                "cell_range": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
                "row_start": {"type": "integer", "minimum": 1},
                "row_end": {"type": "integer", "minimum": 1},
                "line_start": {"type": "integer", "minimum": 1},
                "line_end": {"type": "integer", "minimum": 1},
                "query": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 500, "maximum": 8000},
            },
        },
    },
}


@dataclass(frozen=True)
class ResourceQueryOutcome:
    context: PreparedContext
    trace: list[dict[str, Any]]
    call_count: int
    characters: int


class GovernedResourceQueryAgent:
    """Let a model request bounded resource slices without granting filesystem access."""

    def __init__(
        self,
        gateway: ModelGateway,
        workspace: ResourceWorkspace,
        model: str,
        *,
        max_calls: int = 4,
        max_total_chars: int = 24_000,
        max_chars_per_call: int = 8_000,
    ) -> None:
        self.gateway = gateway
        self.workspace = workspace
        self.model = model
        self.max_calls = max(1, min(max_calls, 8))
        self.max_total_chars = max(2_000, min(max_total_chars, 60_000))
        self.max_chars_per_call = max(500, min(max_chars_per_call, 12_000))

    def enrich(
        self,
        *,
        request: str,
        conversation_id: str,
        principal: Principal,
        context: PreparedContext,
        replace_source_text: bool = True,
    ) -> ResourceQueryOutcome:
        if not bool(getattr(self.gateway, "supports_tools", False)):
            return ResourceQueryOutcome(context, [], 0, 0)
        manifest = self.workspace.build(conversation_id, principal)
        if not manifest.items:
            return ResourceQueryOutcome(context, [], 0, 0)
        allowed_ids = {item.id for item in manifest.items}
        index = [{
            "resource_id": item.id,
            "name": item.display_name,
            "type": item.resource_type,
            "mime_type": item.mime_type,
            "locators": item.metadata.get("locators") or {},
        } for item in manifest.items]
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是UniHR的资源定位规划器。只在当前摘录不足以完成用户任务时调用工具，"
                    "选择最少的文件和最小的页码、章节、Sheet或单元格范围。"
                    "用户明确给出页码时必须填写page_start/page_end；明确给出Sheet或单元格范围时必须填写sheet/cell_range；"
                    "明确给出章节名称时必须填写section，不得用模糊query替代用户已经提供的精确定位符。"
                    "不得猜测resource_id，不得请求整个大型文件，不得读取与任务无关的个人信息。"
                    "获得足够证据后停止调用；不要回答业务问题。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前任务：{request[:4_000]}\n"
                    f"资源索引：{json.dumps(index, ensure_ascii=False)}\n"
                    f"当前已披露内容：{context.attachment_text[:6_000] or '无'}"
                ),
            },
        ]
        trace: list[dict[str, Any]] = []
        excerpts: list[tuple[str, str, str]] = []
        used_calls = 0
        used_chars = 0
        seen: set[str] = set()
        try:
            while used_calls < self.max_calls and used_chars < self.max_total_chars:
                reply = self.gateway.complete(
                    messages,
                    purpose="resource_query_planning",
                    model=self.model,
                    tools=[_RESOURCE_QUERY_TOOL],
                    tool_choice="auto",
                    max_tokens=1_200,
                )
                calls = [
                    call for call in reply.tool_calls
                    if str((call.get("function") or {}).get("name") or "") == "resource_workspace_query"
                ]
                if not calls:
                    break
                assistant_calls = []
                for call in calls:
                    function = dict(call.get("function") or {})
                    assistant_calls.append({
                        "id": str(call.get("id") or f"resource_call_{used_calls + 1}"),
                        "type": "function",
                        "function": {
                            "name": "resource_workspace_query",
                            "arguments": json.dumps(function.get("arguments") or {}, ensure_ascii=False),
                        },
                    })
                messages.append({"role": "assistant", "content": reply.content or "", "tool_calls": assistant_calls})

                for call, assistant_call in zip(calls, assistant_calls):
                    if used_calls >= self.max_calls or used_chars >= self.max_total_chars:
                        break
                    arguments = dict((call.get("function") or {}).get("arguments") or {})
                    resource_id = str(arguments.pop("resource_id", ""))
                    canonical = resource_id + "|" + json.dumps(arguments, sort_keys=True, ensure_ascii=False)
                    if canonical in seen:
                        tool_text = json.dumps({"status": "rejected", "reason": "duplicate_query"}, ensure_ascii=False)
                    elif resource_id not in allowed_ids:
                        tool_text = json.dumps({"status": "rejected", "reason": "resource_not_in_conversation"}, ensure_ascii=False)
                        trace.append(_trace("resource.query.rejected", "failed", "模型请求了当前工作区之外的资源", resource_id=resource_id))
                    else:
                        seen.add(canonical)
                        remaining = self.max_total_chars - used_chars
                        arguments["max_chars"] = min(
                            int(arguments.get("max_chars") or self.max_chars_per_call),
                            self.max_chars_per_call,
                            remaining,
                        )
                        try:
                            locator = ResourceLocator.model_validate(arguments)
                            excerpt = self.workspace.query(resource_id, locator, principal)
                            excerpts.append((resource_id, excerpt.display_name, excerpt.text))
                            used_calls += 1
                            used_chars += excerpt.characters
                            tool_text = json.dumps({
                                "status": "ok",
                                "resource_id": resource_id,
                                "display_name": excerpt.display_name,
                                "characters": excerpt.characters,
                                "truncated": excerpt.truncated,
                                "locator": locator.model_dump(mode="json", exclude_none=True),
                                "text": excerpt.text,
                            }, ensure_ascii=False)
                            trace.append(_trace(
                                "resource.query.executed", "completed",
                                f"按定位符读取 {excerpt.display_name}，获得 {excerpt.characters} 字符",
                                resource_id=resource_id,
                                locator=locator.model_dump(mode="json", exclude_none=True),
                                characters=excerpt.characters,
                                truncated=excerpt.truncated,
                                model=reply.model,
                            ))
                        except (KeyError, ValueError) as exc:
                            used_calls += 1
                            tool_text = json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False)
                            trace.append(_trace(
                                "resource.query.rejected", "failed", "资源定位请求未通过治理校验",
                                resource_id=resource_id, reason=str(exc),
                            ))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": assistant_call["id"],
                        "name": "resource_workspace_query",
                        "content": tool_text,
                    })
        except ModelGatewayError:
            trace.append(_trace(
                "resource.query.planner_unavailable", "fallback",
                "资源定位模型暂不可用，继续使用索引阶段已披露的附件内容",
            ))

        if not excerpts:
            return ResourceQueryOutcome(context, trace, used_calls, used_chars)
        by_resource: dict[str, list[str]] = {}
        for resource_id, _, text in excerpts:
            by_resource.setdefault(resource_id, []).append(text)
        attachments = context.attachments
        if replace_source_text:
            attachments = [
                item.model_copy(update={"extracted_text": "\n\n".join(by_resource[item.id])})
                if item.id in by_resource else item
                for item in context.attachments
            ]
        blocks = [
            f"\n\n[Function Call局部读取：{name} | resource_id={resource_id}]\n{text}"
            for resource_id, name, text in excerpts
        ]
        extra = "".join(blocks)
        enriched = replace(
            context,
            attachments=attachments,
            attachment_text=(context.attachment_text + extra)[-self.max_total_chars - 8_000:],
            usage=[
                *context.usage,
                *[
                    {"kind": "resource_query", "label": name, "purpose": "model_requested_evidence"}
                    for _, name, _ in excerpts
                ],
            ],
            injected_chars=context.injected_chars + len(extra),
            estimated_tokens=context.estimated_tokens + (len(extra) + 1) // 2,
        )
        trace.append(_trace(
            "resource.query.completed", "completed",
            f"完成 {used_calls} 次受治理局部读取，共补充 {used_chars} 字符",
            call_count=used_calls, characters=used_chars,
        ))
        return ResourceQueryOutcome(enriched, trace, used_calls, used_chars)


def _trace(event: str, status: str, summary: str, **details: Any) -> dict[str, Any]:
    return {"event": event, "status": status, "summary": summary, **details}
