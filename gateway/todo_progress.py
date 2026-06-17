"""Human-readable todo rendering for gateway progress bubbles."""

from __future__ import annotations

import json
from typing import Any, Optional


_STATUS_ICON = {
    "completed": "✅",
    "in_progress": "🔄",
    "pending": "⏳",
    "cancelled": "✗",
}

_STATUS_LABEL = {
    "completed": "completed",
    "in_progress": "in progress",
    "pending": "pending",
    "cancelled": "cancelled",
}


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _format_elapsed(seconds: Any) -> Optional[str]:
    """Compact human duration for a per-item span. None when unmeasured.

    Mirrors the gateway's tool-progress duration buckets (ms / s / m / h) so a
    todo item's time reads the same as any other tool's completion time.
    """
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return None
    value = float(seconds)
    if value < 0:
        value = 0.0
    if value < 0.1:
        return f"{int(round(value * 1000))}ms"
    if value < 10:
        return f"{value:.1f}s"
    total = int(round(value))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _coerce_todo_items(payload: Any) -> Optional[list]:
    """Extract the todo item list from a tool result (JSON string or dict)."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return None
    if isinstance(payload, dict):
        items = payload.get("todos")
        return items if isinstance(items, list) else None
    return None


def format_todo_progress(
    args: Optional[dict],
    *,
    result: Any = None,
    max_items: int = 12,
    content_limit: int = 160,
) -> Optional[str]:
    """Render ``todo`` tool args as a compact plan card.

    Gateway tool-progress events are emitted at tool start, so by default this
    renders the input args, not the completed result.  Initial planning calls
    pass the full list. Merge calls often pass only changed items, so label them
    as updates.

    When ``result`` is provided (the tool's completion payload), per-item
    wall-clock durations are read from it and shown as ``(2m 14s)`` suffixes.
    The model's args never carry timing, so durations only appear on the
    completion re-render. If ``result`` is provided but carries no usable item
    list, returns None (the caller should keep the existing start card) rather
    than falling back to args or the "Reading task list" sentinel.
    """
    if not isinstance(args, dict):
        return None

    if result is not None:
        # Completion re-render path: the result is authoritative. No usable
        # items → no card (caller leaves the start card untouched).
        result_items = _coerce_todo_items(result)
        if result_items is None:
            return None
        todos = result_items
    else:
        # Tool-start path: render from args (no timing available yet).
        todos = args.get("todos")
        if todos is None:
            return "📋 Todo\nReading task list"
    if not isinstance(todos, list):
        return None

    merge = bool(args.get("merge", False))
    title = "📋 Plan update" if merge else "📋 Plan"
    count = len(todos)
    noun = "task" if count == 1 else "tasks"
    lines = [f"{title} ({count} {noun})"]

    if not todos:
        lines.append("No tasks")
        return "\n".join(lines)

    shown = 0
    for item in todos:
        if shown >= max_items:
            break
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "pending")
        label = _STATUS_LABEL.get(status, status.replace("_", " "))
        icon = _STATUS_ICON.get(status, "•")
        content = _one_line(item.get("content") or item.get("id") or "<untitled task>")
        content = _truncate(content, content_limit)
        elapsed = _format_elapsed(item.get("elapsed_seconds"))
        duration = f" ({elapsed})" if elapsed else ""
        shown += 1
        lines.append(f"{shown}. {icon} {label}{duration} - {content}")

    remaining = count - shown
    if remaining > 0:
        lines.append(f"... {remaining} more")

    text = "\n".join(lines)
    try:
        from agent.redact import redact_sensitive_text

        text = redact_sensitive_text(text)
    except Exception:
        pass
    return text
