from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from .contracts import PublicAlert, PublicEventStatus, SourceTier, WeatherSnapshot


class CmaWeatherAdapter:
    """Read current weather and active alerts from China Meteorological Administration.

    The public CMA JSON endpoints are fixed by the adapter; no user-provided URL
    is ever requested. Location autocomplete is used only to resolve a station.
    """

    capability = "weather.current"
    provider_name = "china_meteorological_administration"
    autocomplete_url = "https://weather.cma.cn/api/autocomplete"
    weather_url_template = "https://weather.cma.cn/api/weather/view?stationid={station_id}"

    def __init__(
        self,
        *,
        enabled: bool = True,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.enabled = enabled
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "UniHR-AgentOS/1.0 (official-public-read-only)"},
        )

    def healthy(self) -> bool:
        return self.enabled

    def fetch(self, location: str) -> WeatherSnapshot:
        station_id, station_name = self._resolve_station(location)
        source_url = self.weather_url_template.format(station_id=quote(station_id, safe=""))
        response = self.client.get(source_url)
        response.raise_for_status()
        body = response.json()
        if int(body.get("code", -1)) != 0 or not isinstance(body.get("data"), dict):
            raise ValueError("cma_weather_invalid_response")
        data = body["data"]
        now = data.get("now") if isinstance(data.get("now"), dict) else {}
        resolved_location = data.get("location") if isinstance(data.get("location"), dict) else {}
        daily = data.get("daily") if isinstance(data.get("daily"), list) else []
        current_day = daily[0] if daily and isinstance(daily[0], dict) else {}
        alerts = [
            self._alert(raw)
            for raw in (data.get("alarm") or [])
            if isinstance(raw, dict)
        ]
        observed_at = _normalize_cma_time(str(data.get("lastUpdate") or ""))
        return WeatherSnapshot(
            location=str(resolved_location.get("name") or station_name or location),
            observed_at=observed_at or datetime.now(timezone.utc).isoformat(),
            temperature_c=_number(now.get("temperature")),
            apparent_temperature_c=_number(now.get("feelst")),
            wind_speed_kmh=_mps_to_kmh(now.get("windSpeed")),
            wind_direction=str(now.get("windDirection") or ""),
            wind_scale=str(now.get("windScale") or ""),
            humidity_percent=_number(now.get("humidity")),
            precipitation_mm=_number(now.get("precipitation")),
            weather_text=str(current_day.get("dayText") or current_day.get("nightText") or ""),
            alerts=alerts,
            provider=self.provider_name,
            source_url=source_url,
            source_tier=SourceTier.OFFICIAL.value,
            providers_tried=[self.provider_name],
        )

    def _resolve_station(self, location: str) -> tuple[str, str]:
        response = self.client.get(self.autocomplete_url, params={"q": location})
        response.raise_for_status()
        body = response.json()
        candidates = body.get("data") if isinstance(body, dict) else []
        parsed: list[tuple[int, str, str]] = []
        for raw in candidates or []:
            parts = str(raw).split("|")
            if len(parts) < 4 or parts[3].strip() != "中国":
                continue
            station_id, name = parts[0].strip(), parts[1].strip()
            if not station_id or not name:
                continue
            score = _location_match_score(location, name)
            if score:
                parsed.append((score, station_id, name))
        if not parsed:
            raise ValueError("cma_location_not_found")
        parsed.sort(key=lambda item: (-item[0], len(item[2]), item[1]))
        _score, station_id, name = parsed[0]
        return station_id, name

    @staticmethod
    def _alert(raw: dict[str, Any]) -> PublicAlert:
        alert_id = str(raw.get("id") or "")
        title = str(raw.get("title") or "")
        return PublicAlert(
            id=alert_id,
            title=title,
            event_type=str(raw.get("signaltype") or ""),
            level=str(raw.get("signallevel") or ""),
            severity=str(raw.get("severity") or ""),
            effective_at=_normalize_cma_time(str(raw.get("effective") or "")),
            status=PublicEventStatus.ACTIVE.value,
            region_scope=_alert_region(title),
            description=str(raw.get("description") or ""),
            source_url=(
                f"https://weather.cma.cn/web/alarm/{quote(alert_id, safe='')}.html"
                if alert_id else ""
            ),
        )


def _location_match_score(requested: str, candidate: str) -> int:
    wanted = requested.strip().replace(" ", "")
    name = candidate.strip().replace(" ", "")
    if not wanted or not name:
        return 0
    suffixes = "省市区县旗"
    normalized_wanted = wanted.rstrip(suffixes)
    normalized_name = name.rstrip(suffixes)
    if wanted == name:
        return 100
    if normalized_wanted == normalized_name:
        return 95
    if wanted.endswith(name) or name.endswith(wanted):
        return 85
    if normalized_name and normalized_name in normalized_wanted:
        return 70
    return 0


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value))
    except ValueError:
        return None


def _mps_to_kmh(value: Any) -> float | int | None:
    number = _number(value)
    return round(float(number) * 3.6, 1) if number is not None else None


def _normalize_cma_time(value: str) -> str:
    compact = value.strip()
    if not compact:
        return ""
    for pattern in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(compact, pattern).replace(
                tzinfo=timezone(timedelta(hours=8)),
            )
            return parsed.isoformat()
        except ValueError:
            continue
    return compact


def _alert_region(title: str) -> str:
    marker = title.find("发布")
    return title[:marker].strip() if marker > 0 else ""
