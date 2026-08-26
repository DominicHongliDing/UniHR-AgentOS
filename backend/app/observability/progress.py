from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Literal


ToolProgressCallback = Callable[[str, str, dict[str, Any]], None]
ToolProgressStatus = Literal["active", "completed", "failed", "waiting"]

_current_progress: ContextVar[ToolProgressCallback | None] = ContextVar(
    "unihr_tool_progress", default=None,
)


@contextmanager
def tool_progress_scope(callback: ToolProgressCallback | None) -> Iterator[None]:
    """Bind one progress sink to the current Run without sharing it across users."""

    token = _current_progress.set(callback)
    try:
        yield
    finally:
        _current_progress.reset(token)


def emit_tool_progress(
    event: str,
    summary: str,
    *,
    status: ToolProgressStatus = "active",
    **details: Any,
) -> None:
    """Emit a bounded execution fact; observability failures never fail the business Run."""

    callback = _current_progress.get()
    if callback is None:
        return
    safe_details = {
        key: value
        for key, value in details.items()
        if value is not None and key not in {"prompt", "messages", "content", "path", "state"}
    }
    try:
        callback(event, summary, {**safe_details, "event_status": status})
    except Exception:
        return
