from __future__ import annotations

import re
from urllib.parse import urlsplit

from .contracts import FreshnessStatus, SourceTier, WebPage, WebSource


def inspect_public_answer(
    answer: str,
    sources: list[WebSource],
    pages: list[WebPage] | None = None,
    *,
    requested_open_count: int = 0,
    opened_page_count: int | None = None,
) -> dict[str, object]:
    opened_pages = pages or []
    opened_count = len(opened_pages) if opened_page_count is None else max(0, opened_page_count)
    substantive_snippets = sum(len(item.excerpt.strip()) >= 80 for item in sources)
    checks = {
        "answer_present": bool(answer.strip()),
        "sources_present": bool(sources),
        "source_urls_public_shape": all(
            urlsplit(item.url).scheme in {"http", "https"} and bool(urlsplit(item.url).hostname)
            for item in sources
        ),
        "numbered_citation_present": bool(re.search(r"\[\d+\]", answer)),
        "access_time_present": all(bool(item.accessed_at) for item in sources),
        "source_tier_present": all(
            item.source_tier != SourceTier.UNKNOWN.value for item in sources
        ),
        "freshness_status_present": all(
            item.freshness_status != FreshnessStatus.UNKNOWN.value
            or not item.published_at
            for item in sources
        ),
        "search_execution_claim_consistent": not any(phrase in answer for phrase in (
            "不能执行实时联网搜索", "无法执行实时联网搜索", "不能联网搜索",
            "无法联网搜索", "没有联网能力", "不能执行联网搜索",
        )),
        "evidence_body_available": opened_count > 0 or substantive_snippets >= min(2, len(sources)),
    }
    cited = {int(value) for value in re.findall(r"\[(\d+)\]", answer)}
    valid_indexes = {item.index for item in sources}
    checks["citations_resolve"] = bool(cited) and cited.issubset(valid_indexes)
    passed = all(checks.values())
    return {
        "passed": passed,
        "status": "passed" if passed else "failed",
        "checks": checks,
        "metrics": {
            "source_count": len(sources),
            "unique_domain_count": len({urlsplit(item.url).hostname for item in sources}),
            "citation_count": len(cited),
            "opened_page_count": opened_count,
            "requested_open_count": requested_open_count,
            "open_success_rate": round(opened_count / max(1, requested_open_count), 4),
            "substantive_snippet_count": substantive_snippets,
            "provider_count": len({item.provider for item in sources if item.provider}),
            "official_source_count": sum(
                item.source_tier == SourceTier.OFFICIAL.value for item in sources
            ),
            "authoritative_media_count": sum(
                item.source_tier == SourceTier.AUTHORITATIVE_MEDIA.value for item in sources
            ),
            "fresh_source_count": sum(
                item.freshness_status == FreshnessStatus.FRESH.value for item in sources
            ),
        },
        "issues": [name for name, ok in checks.items() if not ok],
    }
