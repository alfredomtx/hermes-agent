"""Human-readable todo rendering for gateway progress bubbles."""

from __future__ import annotations

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


def format_todo_progress(
    args: Optional[dict],
    *,
    max_items: int = 12,
    content_limit: int = 160,
) -> Optional[str]:
    """Render ``todo`` tool args as a compact plan card.

    Gateway tool-progress events are emitted at tool start, so this renders the
    input args, not the completed result.  Initial planning calls pass the full
    list. Merge calls often pass only changed items, so label them as updates.
    """
    if not isinstance(args, dict):
        return None

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
        shown += 1
        lines.append(f"{shown}. {icon} {label} - {content}")

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
