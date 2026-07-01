"""Formatter for periodic long-running gateway heartbeat bubbles."""

from __future__ import annotations

import time
from typing import Any, Optional

_MAX_LINE = 180
_MAX_LINES = 6
_MAX_TOTAL = 900
_STATUS_LABELS = {
    "in_progress": "now",
    "pending": "next",
    "completed": "done",
    "cancelled": "cancelled",
}


def _oneline(value: Any) -> str:
    return " ".join(str(value or "").split())


def _truncate(value: Any, max_len: int = _MAX_LINE) -> str:
    text = _oneline(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _format_elapsed(seconds: Any) -> str:
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _elapsed_code(seconds: Any) -> str:
    return f"`{_format_elapsed(seconds)}`"


def _duration_suffix(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        return ""
    try:
        if float(seconds) < 0:
            return ""
    except Exception:
        return ""
    return f" · {_elapsed_code(float(seconds))}"


def _age_suffix(completed_at: Any, *, now: Optional[float] = None) -> str:
    if not isinstance(completed_at, (int, float)):
        return ""
    end = time.time() if now is None else now
    age = max(0.0, end - float(completed_at))
    if age < 2:
        return " just now"
    return f" {_elapsed_code(age)} ago"


def format_long_running_heartbeat(
    elapsed_seconds: float,
    activity: Optional[dict[str, Any]] = None,
    *,
    want_iteration_detail: bool = False,
    now: Optional[float] = None,
) -> str:
    """Build the edited-in-place "Working" heartbeat text.

    Keeps the first line backward-compatible and appends bounded detail lines
    when the agent exposes them.
    """
    activity = activity if isinstance(activity, dict) else {}

    lines = [f"⏳ Working — {_elapsed_code(elapsed_seconds)}"]

    if want_iteration_detail:
        api = activity.get("api_call_count")
        max_iter = activity.get("max_iterations")
        if api is not None and max_iter is not None:
            lines.append(_truncate(f"• `iteration:` {api}/{max_iter}"))

    todo = activity.get("current_todo")
    if isinstance(todo, dict) and todo.get("content"):
        status = _STATUS_LABELS.get(str(todo.get("status") or ""), str(todo.get("status") or "todo"))
        lines.append(_truncate(
            f"• todo {status}: {todo.get('content')}{_duration_suffix(todo.get('elapsed_seconds'))}"
        ))

    current_tool = _oneline(activity.get("current_tool"))
    current_preview = _oneline(activity.get("current_tool_preview"))
    current_elapsed = activity.get("current_tool_elapsed")
    if current_tool:
        lines.append(_truncate(f"• `tool:` {current_tool}{_duration_suffix(current_elapsed)}"))
        if current_preview and current_preview != current_tool:
            lines.append(_truncate(f"• `doing:` {current_preview}"))
    else:
        desc = _oneline(activity.get("last_activity_desc"))
        if desc:
            lines.append(_truncate(f"• status: {desc}"))

    last = activity.get("last_completed_tool")
    if isinstance(last, dict) and last.get("name"):
        state = "failed" if last.get("is_error") else "done"
        lines.append(_truncate(
            f"• `last:` {last.get('name')} {state} in {_elapsed_code(last.get('duration') or 0)}"
            f"{_age_suffix(last.get('completed_at'), now=now)}"
        ))

    # Bound vertical and total size; this bubble edits every minute.
    lines = lines[:_MAX_LINES]
    text = "\n".join(lines)
    if len(text) > _MAX_TOTAL:
        text = text[: _MAX_TOTAL - 1].rstrip() + "…"
    return text
