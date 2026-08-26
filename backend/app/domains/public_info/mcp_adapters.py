from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from .contracts import WebPage, WebSearchBatch, WebSource
from .mcp_client import McpToolResult, StreamableHttpMcpClient


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FirecrawlMcpSearchAdapter:
    capability = "web.search"
    provider_name = "firecrawl_mcp"

    def __init__(self, client: StreamableHttpMcpClient, *, enabled: bool) -> None:
        self.client = client
        self.enabled = enabled

    def healthy(self) -> bool:
        return self.enabled

    def search(self, query: str, *, max_results: int, freshness_days: int | None = None) -> WebSearchBatch:
        started = perf_counter()
        arguments: dict[str, Any] = {
            "query": query,
            "limit": max_results,
            "sources": [{"type": "web"}, {"type": "news"}],
        }
        if freshness_days:
            arguments["tbs"] = f"qdr:d{freshness_days}"
        result = self.client.call_tool("firecrawl_search", arguments)
        payload = _json_payload(result)
        raw_items = _firecrawl_items(payload)
        sources = _normalize_items(raw_items, provider=self.provider_name, max_results=max_results)
        return WebSearchBatch(
            query=query,
            sources=sources,
            provider=self.provider_name,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            providers_tried=[self.provider_name],
        )


class FirecrawlMcpOpenAdapter:
    capability = "web.open"
    provider_name = "firecrawl_mcp"

    def __init__(self, client: StreamableHttpMcpClient, *, enabled: bool, max_chars: int) -> None:
        self.client = client
        self.enabled = enabled
        self.max_chars = max_chars

    def healthy(self) -> bool:
        return self.enabled

    def open(self, url: str) -> WebPage:
        result = self.client.call_tool("firecrawl_scrape", {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "redactPII": True,
            "removeBase64Images": True,
        })
        payload = _json_payload(result)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        text = str(data.get("markdown") or data.get("content") or result.text).strip()
        if not text:
            raise ValueError("mcp_page_content_missing")
        return WebPage(
            url=str(metadata.get("sourceURL") or metadata.get("url") or url),
            title=str(metadata.get("title") or urlsplit(url).hostname or url)[:300],
            text=text[: self.max_chars],
            accessed_at=_utc_now(),
            content_type="text/markdown",
            provider=self.provider_name,
        )


class TavilyMcpSearchAdapter:
    capability = "web.search"
    provider_name = "tavily_mcp"

    def __init__(self, client: StreamableHttpMcpClient, *, enabled: bool) -> None:
        self.client = client
        self.enabled = enabled

    def healthy(self) -> bool:
        return self.enabled

    def search(self, query: str, *, max_results: int, freshness_days: int | None = None) -> WebSearchBatch:
        started = perf_counter()
        arguments: dict[str, Any] = {
            "query": query,
            "search_depth": "advanced",
            "max_results": max(5, max_results),
            "include_raw_content": False,
            "country": "China",
        }
        if freshness_days:
            arguments["time_range"] = _tavily_time_range(freshness_days)
        result = self.client.call_tool("tavily_search", arguments)
        sources = _parse_tavily_search(result.text, max_results=max_results)
        return WebSearchBatch(
            query=query,
            sources=sources,
            provider=self.provider_name,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            providers_tried=[self.provider_name],
        )


class TavilyMcpOpenAdapter:
    capability = "web.open"
    provider_name = "tavily_mcp"

    def __init__(self, client: StreamableHttpMcpClient, *, enabled: bool, max_chars: int) -> None:
        self.client = client
        self.enabled = enabled
        self.max_chars = max_chars

    def healthy(self) -> bool:
        return self.enabled

    def open(self, url: str) -> WebPage:
        result = self.client.call_tool("tavily_extract", {
            "urls": [url], "extract_depth": "advanced", "format": "markdown",
        })
        text = _parse_tavily_extract(result.text, url)
        if not text:
            raise ValueError("mcp_page_content_missing")
        return WebPage(
            url=url,
            title=urlsplit(url).hostname or url,
            text=text[: self.max_chars],
            accessed_at=_utc_now(),
            content_type="text/markdown",
            provider=self.provider_name,
        )


class ExaMcpSearchAdapter:
    capability = "web.search"
    provider_name = "exa_mcp"

    def __init__(self, client: StreamableHttpMcpClient, *, enabled: bool) -> None:
        self.client = client
        self.enabled = enabled

    def healthy(self) -> bool:
        return self.enabled

    def search(self, query: str, *, max_results: int, freshness_days: int | None = None) -> WebSearchBatch:
        started = perf_counter()
        # Exa's hosted MCP accepts natural-language ideal-page descriptions.
        # Freshness remains in the user/query-rewrite text because the keyless
        # search tool deliberately exposes only query and result count.
        result = self.client.call_tool("web_search_exa", {
            "query": query,
            "numResults": max_results,
        })
        sources = _parse_exa_search(result.text, max_results=max_results)
        return WebSearchBatch(
            query=query,
            sources=sources,
            provider=self.provider_name,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            providers_tried=[self.provider_name],
        )


class ExaMcpOpenAdapter:
    capability = "web.open"
    provider_name = "exa_mcp"

    def __init__(self, client: StreamableHttpMcpClient, *, enabled: bool, max_chars: int) -> None:
        self.client = client
        self.enabled = enabled
        self.max_chars = max_chars

    def healthy(self) -> bool:
        return self.enabled

    def open(self, url: str) -> WebPage:
        result = self.client.call_tool("web_fetch_exa", {
            "urls": [url], "maxCharacters": self.max_chars,
        })
        text = result.text.strip()
        if not text:
            raise ValueError("mcp_page_content_missing")
        title_match = re.search(r"(?m)^#\s+(.+)$", text)
        return WebPage(
            url=url,
            title=(title_match.group(1).strip() if title_match else urlsplit(url).hostname or url)[:300],
            text=text[: self.max_chars],
            accessed_at=_utc_now(),
            content_type="text/markdown",
            provider=self.provider_name,
        )


def _json_payload(result: McpToolResult) -> dict[str, Any]:
    if result.structured:
        return result.structured
    try:
        parsed = json.loads(result.text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _firecrawl_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    items: list[dict[str, Any]] = []
    for key in ("web", "news", "results"):
        value = data.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def _normalize_items(items: list[dict[str, Any]], *, provider: str, max_results: int) -> list[WebSource]:
    accessed_at = _utc_now()
    sources: list[WebSource] = []
    seen: set[str] = set()
    for item in items:
        url = str(item.get("url") or item.get("link") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append(WebSource(
            index=len(sources) + 1,
            title=str(item.get("title") or url)[:300],
            url=url,
            site_name=str(item.get("siteName") or item.get("site_name") or urlsplit(url).hostname or "")[:120],
            published_at=str(item.get("date") or item.get("publishedDate") or item.get("published_at") or "")[:80],
            accessed_at=accessed_at,
            excerpt=str(item.get("description") or item.get("content") or item.get("markdown") or "")[:1_200],
            provider=provider,
            relevance_score=float(item.get("score") or 0.0),
        ))
        if len(sources) >= max_results:
            break
    return sources


def _parse_tavily_search(text: str, *, max_results: int) -> list[WebSource]:
    chunks = re.split(r"(?m)^Title:\s*", text)
    items: list[dict[str, Any]] = []
    for chunk in chunks[1:]:
        title, _, rest = chunk.partition("\n")
        url_match = re.search(r"(?m)^URL:\s*(\S+)", rest)
        content_match = re.search(r"(?ms)^Content:\s*(.*?)(?=\n(?:Raw Content|Favicon):|\Z)", rest)
        if url_match:
            items.append({
                "title": title.strip(), "url": url_match.group(1).strip(),
                "content": content_match.group(1).strip() if content_match else "",
            })
    return _normalize_items(items, provider="tavily_mcp", max_results=max_results)


def _parse_exa_search(text: str, *, max_results: int) -> list[WebSource]:
    chunks = re.split(r"(?m)^Title:\s*", text)
    items: list[dict[str, Any]] = []
    for chunk in chunks[1:]:
        title, _, rest = chunk.partition("\n")
        url_match = re.search(r"(?m)^URL:\s*(\S+)", rest)
        published_match = re.search(r"(?m)^Published:\s*(.+)$", rest)
        highlights_match = re.search(r"(?ms)^Highlights:\s*(.*)$", rest)
        if url_match:
            items.append({
                "title": title.strip(),
                "url": url_match.group(1).strip(),
                "published_at": published_match.group(1).strip() if published_match else "",
                "content": highlights_match.group(1).strip() if highlights_match else rest.strip(),
            })
    return _normalize_items(items, provider="exa_mcp", max_results=max_results)


def _parse_tavily_extract(text: str, url: str) -> str:
    marker = re.search(r"(?ms)(?:Raw Content|Content):\s*(.*)", text)
    if marker:
        return marker.group(1).strip()
    cleaned = text.replace(url, "").strip()
    return cleaned


def _tavily_time_range(days: int) -> str:
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    return "year"
