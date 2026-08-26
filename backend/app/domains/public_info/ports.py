from __future__ import annotations

from typing import Protocol

from .contracts import WeatherSnapshot, WebPage, WebSearchBatch


class WebSearchPort(Protocol):
    capability: str

    def healthy(self) -> bool: ...

    def search(
        self,
        query: str,
        *,
        max_results: int,
        freshness_days: int | None = None,
    ) -> WebSearchBatch: ...


class WebOpenPort(Protocol):
    capability: str

    def healthy(self) -> bool: ...

    def open(self, url: str) -> WebPage: ...


class WeatherPort(Protocol):
    capability: str

    def healthy(self) -> bool: ...

    def fetch(self, location: str) -> WeatherSnapshot: ...
