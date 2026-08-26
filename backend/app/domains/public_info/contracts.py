from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SourceTier(StrEnum):
    OFFICIAL = "official"
    AUTHORITATIVE_MEDIA = "authoritative_media"
    GENERAL_WEB = "general_web"
    UNKNOWN = "unknown"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    UNKNOWN = "unknown"


class PublicEventStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WebSource:
    index: int
    title: str
    url: str
    site_name: str = ""
    published_at: str = ""
    accessed_at: str = ""
    excerpt: str = ""
    provider: str = ""
    relevance_score: float = 0.0
    source_tier: str = SourceTier.UNKNOWN.value
    freshness_status: str = FreshnessStatus.UNKNOWN.value
    event_time: str = ""
    expires_at: str = ""
    event_status: str = PublicEventStatus.UNKNOWN.value
    region_scope: str = ""

    def citation(self) -> dict[str, str]:
        return {
            "index": str(self.index),
            "title": self.title,
            "url": self.url,
            "site_name": self.site_name,
            "published_at": self.published_at,
            "observed_at": self.accessed_at,
            "excerpt": self.excerpt,
            "source_tier": self.source_tier,
            "freshness_status": self.freshness_status,
            "event_time": self.event_time,
            "expires_at": self.expires_at,
            "event_status": self.event_status,
            "region_scope": self.region_scope,
        }


@dataclass(frozen=True)
class PublicAlert:
    id: str
    title: str
    event_type: str
    level: str
    severity: str
    effective_at: str
    status: str
    region_scope: str
    description: str = ""
    expires_at: str = ""
    source_url: str = ""


@dataclass(frozen=True)
class WeatherSnapshot:
    location: str
    observed_at: str
    temperature_c: float | int | None
    apparent_temperature_c: float | int | None
    wind_speed_kmh: float | int | None
    wind_direction: str = ""
    wind_scale: str = ""
    humidity_percent: float | int | None = None
    precipitation_mm: float | int | None = None
    weather_text: str = ""
    alerts: list[PublicAlert] = field(default_factory=list)
    provider: str = ""
    source_url: str = ""
    source_tier: str = SourceTier.UNKNOWN.value
    providers_tried: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WebSearchBatch:
    query: str
    sources: list[WebSource]
    provider: str
    latency_ms: int = 0
    provider_answer: str = ""
    providers_tried: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    text: str
    accessed_at: str
    content_type: str = "text/html"
    provider: str = ""


@dataclass(frozen=True)
class PublicInfoResult:
    answer: str
    citations: list[dict[str, str]]
    capability_status: str
    query: str = ""
    trace: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    harness: dict[str, object] = field(default_factory=dict)
