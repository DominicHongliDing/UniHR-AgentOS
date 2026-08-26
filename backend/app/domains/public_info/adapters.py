from __future__ import annotations

import ipaddress
import socket
from datetime import datetime, timezone
from html.parser import HTMLParser
from time import perf_counter
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.settings import Settings
from app.models.usage_store import ModelUsageStore

from .contracts import WebPage, WebSearchBatch, WebSource


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DashScopeWebSearchAdapter:
    """Native DashScope search adapter that preserves source metadata."""

    capability = "web.search"
    provider_name = "dashscope_native_search"
    endpoint = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.public_search_timeout_seconds)
        self.usage_store = ModelUsageStore(settings) if settings.database_path is not None else None

    def healthy(self) -> bool:
        return self.settings.public_search_enabled and bool(self.settings.model_api_key)

    def search(
        self,
        query: str,
        *,
        max_results: int,
        freshness_days: int | None = None,
    ) -> WebSearchBatch:
        if not self.healthy():
            raise RuntimeError("public_search_credentials_unavailable")
        # DashScope freshness is effective only with the turbo strategy.
        resolved_strategy = "turbo" if freshness_days is not None else self.settings.public_search_strategy
        search_options: dict[str, object] = {
            "search_strategy": resolved_strategy,
            "enable_source": True,
            "forced_search": True,
        }
        if freshness_days is not None:
            search_options["freshness"] = max(1, min(freshness_days, 3650))
        payload = {
            "model": self.settings.public_search_model,
            "input": {"messages": [{"role": "user", "content": query}]},
            "parameters": {
                "enable_search": True,
                "search_options": search_options,
                "result_format": "message",
            },
        }
        started = perf_counter()
        response = self.client.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.settings.model_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        if self.usage_store is not None:
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            self.usage_store.record(
                provider="bailian",
                model=self.settings.public_search_model,
                purpose="public_web_search",
                operation_kind="web_search",
                input_units=1,
                output_units=int(usage.get("total_tokens") or 0),
                unit_type="calls",
                latency_ms=max(0, int((perf_counter() - started) * 1000)),
                status="succeeded",
                metadata={"query_chars": len(query), "max_results": max_results},
            )
        output = body.get("output") if isinstance(body, dict) else {}
        output = output if isinstance(output, dict) else {}
        search_info = output.get("search_info")
        search_info = search_info if isinstance(search_info, dict) else {}
        raw_results = search_info.get("search_results") or []
        accessed_at = _utc_now()
        sources: list[WebSource] = []
        seen_urls: set[str] = set()
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            canonical = _canonical_url(url)
            if not url or canonical in seen_urls:
                continue
            seen_urls.add(canonical)
            sources.append(WebSource(
                index=len(sources) + 1,
                title=str(raw.get("title") or raw.get("name") or url).strip()[:300],
                url=url,
                site_name=str(raw.get("site_name") or raw.get("hostname") or "").strip()[:120],
                published_at=str(raw.get("publish_time") or raw.get("published_at") or "").strip()[:80],
                accessed_at=accessed_at,
                excerpt=str(raw.get("snippet") or raw.get("content") or "").strip()[:1_200],
                provider=self.provider_name,
            ))
        provider_answer = ""
        choices = output.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                provider_answer = str(message.get("content") or "")
        if any(term in query for term in ("官方", "官网", "政策", "通知", "公告", "规定")):
            sources.sort(key=lambda item: _authority_score(item.url), reverse=True)
        sources = [
            WebSource(
                index=index,
                title=item.title,
                url=item.url,
                site_name=item.site_name,
                published_at=item.published_at,
                accessed_at=item.accessed_at,
                excerpt=item.excerpt,
                provider=item.provider,
                relevance_score=item.relevance_score,
            )
            for index, item in enumerate(sources[:max_results], start=1)
        ]
        return WebSearchBatch(
            query=query,
            sources=sources,
            provider="dashscope_native_search",
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            provider_answer=provider_answer,
        )


def _authority_score(url: str) -> int:
    hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    if hostname == "gov.cn" or hostname.endswith(".gov.cn"):
        return 4
    if hostname.endswith(".edu.cn") or hostname.endswith(".ac.cn"):
        return 3
    if hostname.endswith((".org.cn", ".org")):
        return 2
    return 1


def _canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path or "/"
    if path.casefold().endswith("/index.html"):
        path = path[:-10] or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, parts.query, ""))


class _ReadableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored = 0
        self._title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        lowered = tag.casefold()
        if lowered in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
        elif lowered == "title":
            self._title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
        elif lowered == "title":
            self._title = False

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._title:
            self.title_parts.append(cleaned)
        self.text_parts.append(cleaned)


class SafeHttpWebOpenAdapter:
    capability = "web.open"
    provider_name = "safe_http"

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        *,
        resolver=socket.getaddrinfo,
    ) -> None:
        self.settings = settings
        self.client = client or httpx.Client(
            timeout=settings.public_open_timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": "UniHR-AgentOS/1.0 (public-read-only)"},
        )
        self.resolver = resolver

    def healthy(self) -> bool:
        return self.settings.public_search_enabled

    def open(self, url: str) -> WebPage:
        current = url
        for _ in range(4):
            self._assert_public_url(current)
            response = self.client.get(current)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("redirect_without_location")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            if content_type and not (content_type.startswith("text/") or content_type == "application/xhtml+xml"):
                raise ValueError("unsupported_web_content_type")
            raw = response.content
            if len(raw) > self.settings.public_open_max_bytes:
                raise ValueError("web_page_too_large")
            parser = _ReadableHtmlParser()
            parser.feed(response.text)
            title = " ".join(parser.title_parts).strip()[:300] or urlsplit(current).hostname or current
            text = "\n".join(parser.text_parts)
            return WebPage(
                url=current,
                title=title,
                text=text[: self.settings.public_open_max_chars],
                accessed_at=_utc_now(),
                content_type=content_type or "text/html",
                provider=self.provider_name,
            )
        raise ValueError("too_many_redirects")

    def _assert_public_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("unsafe_web_url")
        hostname = parts.hostname.casefold().rstrip(".")
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
            raise ValueError("private_web_host")
        try:
            addresses = [item[4][0] for item in self.resolver(hostname, parts.port or 443)]
        except OSError as exc:
            raise ValueError("web_host_resolution_failed") from exc
        if not addresses:
            raise ValueError("web_host_resolution_failed")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise ValueError("private_web_host")
