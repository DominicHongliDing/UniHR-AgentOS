from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from .contracts import (
    FreshnessStatus,
    PublicEventStatus,
    SourceTier,
    WeatherSnapshot,
    WebPage,
    WebSearchBatch,
    WebSource,
)


class SearchAdapter(Protocol):
    provider_name: str
    def healthy(self) -> bool: ...
    def search(self, query: str, *, max_results: int, freshness_days: int | None = None) -> WebSearchBatch: ...


class OpenAdapter(Protocol):
    provider_name: str
    def healthy(self) -> bool: ...
    def open(self, url: str) -> WebPage: ...


class WeatherAdapter(Protocol):
    provider_name: str
    def healthy(self) -> bool: ...
    def fetch(self, location: str) -> WeatherSnapshot: ...


class WeatherGateway:
    """Official-first current weather with explicit, visible fallback."""

    capability = "weather.current"

    def __init__(self, adapters: list[WeatherAdapter]) -> None:
        self.adapters = adapters

    def healthy(self) -> bool:
        return any(adapter.healthy() for adapter in self.adapters)

    def fetch(self, location: str) -> WeatherSnapshot:
        tried: list[str] = []
        warnings: list[str] = []
        for adapter in self.adapters:
            if not adapter.healthy():
                continue
            tried.append(adapter.provider_name)
            try:
                snapshot = adapter.fetch(location)
                return replace(
                    snapshot,
                    providers_tried=tried,
                    warnings=[*warnings, *snapshot.warnings],
                )
            except Exception:
                warnings.append(f"weather_provider_failed:{adapter.provider_name}")
        raise RuntimeError("weather_gateway_exhausted:" + ",".join(warnings))


class WebSearchGateway:
    """Deep module: provider fallback, deduplication and ranking behind web.search."""

    capability = "web.search"

    def __init__(self, adapters: list[SearchAdapter], *, min_results: int = 4) -> None:
        self.adapters = adapters
        self.min_results = max(1, min_results)

    def healthy(self) -> bool:
        return any(adapter.healthy() for adapter in self.adapters)

    def search(self, query: str, *, max_results: int, freshness_days: int | None = None) -> WebSearchBatch:
        collected: list[WebSource] = []
        providers: list[str] = []
        warnings: list[str] = []
        latency_ms = 0
        provider_answer = ""
        for adapter in self.adapters:
            if not adapter.healthy():
                continue
            providers.append(adapter.provider_name)
            try:
                batch = adapter.search(
                    query, max_results=max_results, freshness_days=freshness_days,
                )
                latency_ms += batch.latency_ms
                provider_answer = provider_answer or batch.provider_answer
                collected.extend(batch.sources)
            except Exception:  # provider faults are isolated; gateway falls through
                warnings.append(f"provider_failed:{adapter.provider_name}")
            if len({_canonical(item.url) for item in collected}) >= self.min_results:
                break
        ranked = rank_sources(query, collected)[:max_results]
        return WebSearchBatch(
            query=query,
            sources=[replace(item, index=index) for index, item in enumerate(ranked, 1)],
            provider="web_retrieval_gateway",
            latency_ms=latency_ms,
            provider_answer=provider_answer,
            providers_tried=providers,
            warnings=warnings,
        )


class WebOpenGateway:
    """Deep module: cheap safe HTTP first, MCP extraction only when needed."""

    capability = "web.open"

    def __init__(self, adapters: list[OpenAdapter], *, min_content_chars: int = 240) -> None:
        self.adapters = adapters
        self.min_content_chars = max(40, min_content_chars)

    def healthy(self) -> bool:
        return any(adapter.healthy() for adapter in self.adapters)

    def open(self, url: str) -> WebPage:
        failures: list[str] = []
        for adapter in self.adapters:
            if not adapter.healthy():
                continue
            try:
                page = adapter.open(url)
                if _usable_page(page.text, self.min_content_chars):
                    return page if page.provider else replace(page, provider=adapter.provider_name)
                failures.append(f"thin:{adapter.provider_name}")
            except Exception:
                failures.append(f"failed:{adapter.provider_name}")
        raise RuntimeError("web_open_exhausted:" + ",".join(failures))


def rank_sources(query: str, sources: list[WebSource]) -> list[WebSource]:
    query_terms = _terms(query)
    best: dict[str, tuple[float, WebSource]] = {}
    for source in sources:
        key = _canonical(source.url)
        source = govern_source(source)
        text_terms = _terms(f"{source.title} {source.excerpt}")
        overlap = len(query_terms & text_terms) / max(1, len(query_terms))
        score = (
            min(max(source.relevance_score, 0.0), 1.0) * 0.35
            + overlap * 0.35
            + _authority(source.url) * 0.18
            + (0.08 if len(source.excerpt.strip()) >= 80 else 0.0)
            + _freshness(source.published_at) * 0.04
        )
        current = best.get(key)
        if current is None or score > current[0]:
            best[key] = (score, replace(source, relevance_score=round(score, 4)))
    return [item for _score, item in sorted(best.values(), key=lambda pair: pair[0], reverse=True)]


def govern_source(source: WebSource) -> WebSource:
    """Attach authority, freshness and event lifecycle metadata once."""
    text = f"{source.title} {source.excerpt}"
    return replace(
        source,
        source_tier=(
            source.source_tier
            if source.source_tier != SourceTier.UNKNOWN.value
            else classify_source_tier(source.url)
        ),
        freshness_status=(
            source.freshness_status
            if source.freshness_status != FreshnessStatus.UNKNOWN.value
            else assess_freshness(source.published_at)
        ),
        event_time=source.event_time or source.published_at,
        event_status=(
            source.event_status
            if source.event_status != PublicEventStatus.UNKNOWN.value
            else infer_event_status(text)
        ),
    )


def classify_source_tier(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    official_hosts = ("cma.gov.cn", "weather.cma.cn", "nmc.cn", "12379.cn")
    if host == "gov.cn" or host.endswith(".gov.cn") or any(
        host == item or host.endswith("." + item) for item in official_hosts
    ):
        return SourceTier.OFFICIAL.value
    authoritative_hosts = (
        "news.cn", "xinhuanet.com", "people.com.cn", "cctv.com",
        "cnr.cn", "chinanews.com.cn",
    )
    if any(host == item or host.endswith("." + item) for item in authoritative_hosts):
        return SourceTier.AUTHORITATIVE_MEDIA.value
    return SourceTier.GENERAL_WEB.value if host else SourceTier.UNKNOWN.value


def assess_freshness(value: str, *, now: datetime | None = None) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return FreshnessStatus.UNKNOWN.value
    current = now or datetime.now(timezone.utc)
    age_days = max(0, (current - parsed).total_seconds() / 86_400)
    if age_days <= 7:
        return FreshnessStatus.FRESH.value
    if age_days <= 30:
        return FreshnessStatus.AGING.value
    return FreshnessStatus.STALE.value


def infer_event_status(text: str) -> str:
    if any(term in text for term in ("解除", "终止", "取消", "结束预警", "撤销")):
        return PublicEventStatus.RELEASED.value
    if any(term in text for term in ("发布", "生效", "启动", "预警")):
        return PublicEventStatus.ACTIVE.value
    return PublicEventStatus.UNKNOWN.value


def _canonical(url: str) -> str:
    parts = urlsplit(url.strip())
    path = (parts.path or "/").rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, parts.query, ""))


def _terms(text: str) -> set[str]:
    lowered = text.casefold()
    words = set(re.findall(r"[a-z0-9]{2,}", lowered))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    words.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
    return words


def _authority(url: str) -> float:
    tier = classify_source_tier(url)
    if tier == SourceTier.OFFICIAL.value:
        return 1.0
    if tier == SourceTier.AUTHORITATIVE_MEDIA.value:
        return 0.82
    host = (urlsplit(url).hostname or "").casefold()
    if host.endswith((".edu.cn", ".ac.cn")):
        return 0.85
    if host.endswith((".org.cn", ".org")):
        return 0.65
    return 0.35


def _freshness(value: str) -> float:
    published = _parse_datetime(value)
    if published is None:
        return 0.0
    age = (datetime.now(timezone.utc) - published).days
    return 1.0 if age <= 31 else 0.5 if age <= 365 else 0.0


def _parse_datetime(value: str) -> datetime | None:
    compact = str(value or "").strip()
    if not compact:
        return None
    candidates = (compact, compact.replace("/", "-"))
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _usable_page(text: str, minimum: int) -> bool:
    compact = " ".join(text.split())
    if len(compact) < minimum:
        return False
    blocked_phrases = ("access denied", "enable javascript", "验证码", "访问受限")
    return not any(phrase in compact.casefold() for phrase in blocked_phrases)
