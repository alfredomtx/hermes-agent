"""Bounded producer-owned activity state for tool execution."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Iterable

from agent.display import build_tool_label, build_tool_preview
from agent.redact import redact_sensitive_text

_MAX_PREVIEW_LENGTH = 200
_MAX_HISTORY_LENGTH = 3


def _redact_activity_text(value: Any) -> str:
    """Return text with credential-shaped values removed."""
    try:
        return redact_sensitive_text(str(value), force=True)
    except Exception:
        return "[redacted activity]"


def _safe_preview(tool_name: str, args: dict | None) -> str:
    if tool_name == "terminal":
        return "terminal command [redacted]"
    try:
        redacted = redact_sensitive_text(
            json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False),
            force=True,
        )
        safe_args = json.loads(redacted)
    except Exception:
        safe_args = {}
    try:
        preview = build_tool_label(tool_name, safe_args, max_len=_MAX_PREVIEW_LENGTH)
        if not preview:
            preview = build_tool_preview(tool_name, safe_args, max_len=_MAX_PREVIEW_LENGTH)
    except Exception:
        preview = None
    preview = _redact_activity_text(preview) if preview else ""
    if not preview:
        preview = f"{tool_name} (safe)"
    if len(preview) > _MAX_PREVIEW_LENGTH:
        preview = preview[: _MAX_PREVIEW_LENGTH - 3] + "..."
    return preview


def _safe_history_entry(entry: Any) -> dict | None:
    if not isinstance(entry, dict):
        return None
    name = entry.get("name") or entry.get("label") or "tool"
    label = entry.get("label") or name
    result = {
        "name": _redact_activity_text(name),
        "label": _redact_activity_text(label),
        "duration": 0.0,
        "state": entry.get("state") if entry.get("state") in {"running", "done"} else "done",
        "is_error": bool(entry.get("is_error", False)),
    }
    try:
        result["duration"] = max(0.0, float(entry.get("duration", 0.0) or 0.0))
    except (TypeError, ValueError):
        pass
    if isinstance(entry.get("completed_at"), (int, float)):
        result["completed_at"] = entry["completed_at"]
    return result


def _new_state(agent) -> dict:
    state = {
        "lock": threading.RLock(),
        "active": {},
        "history": [],
        "sequence": 0,
    }
    existing_history = getattr(agent, "_recent_tool_activity", None)
    if isinstance(existing_history, list):
        state["history"] = [
            item for item in (_safe_history_entry(entry) for entry in existing_history[-_MAX_HISTORY_LENGTH:])
            if item is not None
        ]
    return state


def _state_for(agent) -> dict:
    state = getattr(agent, "_activity_state", None)
    if isinstance(state, dict) and getattr(state.get("lock"), "acquire", None):
        return state
    state = _new_state(agent)
    agent_dict = getattr(agent, "__dict__", None)
    if isinstance(agent_dict, dict):
        state = agent_dict.setdefault("_activity_state", state)
    else:
        setattr(agent, "_activity_state", state)
    return state


def _existing_state(agent) -> dict | None:
    state = getattr(agent, "_activity_state", None)
    if isinstance(state, dict) and getattr(state.get("lock"), "acquire", None):
        return state
    return None


def _next_key(state: dict, call_id: Any = None) -> str:
    if call_id not in (None, ""):
        base = f"id:{call_id}"
        if base not in state["active"]:
            return base
    state["sequence"] += 1
    return f"activity:{state['sequence']}"


def _publish_current_locked(agent, state: dict) -> None:
    active = list(state["active"].values())
    agent._recent_tool_activity = list(state["history"][-_MAX_HISTORY_LENGTH:])
    if not active:
        agent._current_tool = None
        agent._current_tool_preview = None
        agent._current_tool_started_at = None
        return

    names = ", ".join(str(item["name"]) for item in active)
    previews = [str(item["preview"]) for item in active if item.get("preview")]
    if len(previews) == 1:
        preview = previews[0]
    else:
        preview = "running: " + " | ".join(previews)
    if len(preview) > _MAX_PREVIEW_LENGTH:
        preview = preview[: _MAX_PREVIEW_LENGTH - 3] + "..."
    agent._current_tool = names
    agent._current_tool_preview = preview
    agent._current_tool_started_at = min(item["started_at"] for item in active)


def mark_tool_started(agent, tool_name: str, args: dict | None, *, call_id: Any = None) -> str | None:
    """Record one real tool call at its execution boundary."""
    try:
        state = _state_for(agent)
        with state["lock"]:
            key = _next_key(state, call_id)
            started_at = time.monotonic()
            name = _redact_activity_text(tool_name) or "tool"
            entry = {
                "name": name,
                "label": _safe_preview(tool_name, args),
                "duration": 0.0,
                "state": "running",
                "is_error": False,
            }
            state["active"][key] = {
                "name": name,
                "preview": entry["label"],
                "started_at": started_at,
                "entry": entry,
            }
            state["history"].append(entry)
            del state["history"][:-_MAX_HISTORY_LENGTH]
            _publish_current_locked(agent, state)
            return key
    except Exception:
        return None


def mark_concurrent_tools_started(agent, calls: Iterable[Any]) -> list[str | None]:
    """Record one running entry for each concurrent worker."""
    keys = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        keys.append(
            mark_tool_started(
                agent,
                str(call.get("name") or "tool"),
                call.get("args"),
                call_id=call.get("call_id"),
            )
        )
    return keys


def _find_active_key(state: dict, tool_name: str, call_id: Any = None) -> str | None:
    if call_id not in (None, ""):
        key = f"id:{call_id}"
        if key in state["active"]:
            return key
    for key, item in state["active"].items():
        if item.get("name") == tool_name:
            return key
    return next(iter(state["active"]), None)


def mark_tool_completed(
    agent,
    tool_name: str,
    duration: float | None,
    *,
    is_error: bool = False,
    call_id: Any = None,
) -> None:
    """Update one running history entry and publish the remaining active calls."""
    try:
        state = _existing_state(agent)
        if state is None:
            return
        with state["lock"]:
            key = _find_active_key(state, _redact_activity_text(tool_name), call_id)
            if key is None:
                return
            item = state["active"].pop(key)
            elapsed = time.monotonic() - item["started_at"]
            try:
                reported = float(duration) if duration is not None else elapsed
            except (TypeError, ValueError):
                reported = elapsed
            elapsed = max(0.0, reported)
            entry = item["entry"]
            entry["duration"] = elapsed
            entry["state"] = "done"
            entry["is_error"] = bool(is_error)
            entry["completed_at"] = time.monotonic()
            agent._last_completed_tool = {
                "name": item["name"],
                "duration": elapsed,
                "is_error": bool(is_error),
                "completed_at": entry["completed_at"],
            }
            _publish_current_locked(agent, state)
    except Exception:
        return


def clear_current_tool_if_idle(agent) -> None:
    """Clear current-tool fields only when no producer call is active."""
    try:
        state = _existing_state(agent)
        if state is None:
            agent._current_tool = None
            agent._current_tool_preview = None
            agent._current_tool_started_at = None
            return
        with state["lock"]:
            if not state["active"]:
                _publish_current_locked(agent, state)
    except Exception:
        return


def reset_turn_activity(agent) -> None:
    """Reset producer-owned activity at an external turn boundary."""
    try:
        state = _state_for(agent)
        with state["lock"]:
            state["active"].clear()
            state["history"].clear()
            agent._recent_tool_activity = []
            agent._current_tool = None
            agent._current_tool_preview = None
            agent._current_tool_started_at = None
            agent._last_completed_tool = None
    except Exception:
        return


def current_tool_snapshot(agent) -> tuple[Any, Any, Any]:
    state = _existing_state(agent)
    if state is not None:
        with state["lock"]:
            if state["active"]:
                return (
                    getattr(agent, "_current_tool", None),
                    getattr(agent, "_current_tool_preview", None),
                    getattr(agent, "_current_tool_started_at", None),
                )
        return None, None, None
    return (
        getattr(agent, "_current_tool", None),
        getattr(agent, "_current_tool_preview", None),
        getattr(agent, "_current_tool_started_at", None),
    )


def current_tool_elapsed(agent, *, now: float | None = None) -> float | None:
    state = _existing_state(agent)
    if state is not None:
        with state["lock"]:
            if not state["active"]:
                return None
            started_at = min(item["started_at"] for item in state["active"].values())
        return max(0.0, time.monotonic() - started_at)

    started_at = getattr(agent, "_current_tool_started_at", None)
    if not isinstance(started_at, (int, float)):
        return None
    return max(0.0, (time.time() if now is None else now) - started_at)


def tool_activity_history(agent, *, now: float | None = None) -> list[dict]:
    state = _existing_state(agent)
    if state is not None:
        with state["lock"]:
            entries = [
                (item, dict(item))
                for item in state["history"][-_MAX_HISTORY_LENGTH:]
            ]
            active = {
                id(item["entry"]): item
                for item in state["active"].values()
            }
        current_now = time.monotonic()
        for original, entry in entries:
            active_item = active.get(id(original))
            if active_item is not None:
                entry["duration"] = max(0.0, current_now - active_item["started_at"])
        return [entry for _original, entry in entries]

    history = getattr(agent, "_recent_tool_activity", None)
    history = list(history[-_MAX_HISTORY_LENGTH:]) if isinstance(history, list) else []
    current_tool, current_preview, _ = current_tool_snapshot(agent)
    elapsed = current_tool_elapsed(agent, now=now)
    if current_tool and elapsed is not None:
        history = history + [{
            "name": str(current_tool),
            "label": str(current_preview or current_tool),
            "duration": elapsed,
            "state": "running",
            "is_error": False,
        }]
    return history[-_MAX_HISTORY_LENGTH:]


def completed_tool_snapshot(agent) -> dict | None:
    state = _existing_state(agent)
    if state is None:
        return getattr(agent, "_last_completed_tool", None)
    with state["lock"]:
        completed = getattr(agent, "_last_completed_tool", None)
        return dict(completed) if isinstance(completed, dict) else None


def todo_activity_snapshot(store) -> dict | None:
    if store is None:
        return None
    try:
        items = store.read_with_timing() if hasattr(store, "read_with_timing") else store.read()
        selected = next(
            (item for item in items if isinstance(item, dict) and item.get("status") == "in_progress"),
            None,
        )
        if selected is None:
            selected = next(
                (item for item in items if isinstance(item, dict) and item.get("status") == "pending"),
                None,
            )
        if selected is None:
            return None
        return {
            "content": selected.get("content") or selected.get("id") or "todo",
            "status": selected.get("status") or "pending",
            "elapsed_seconds": selected.get("elapsed_seconds"),
        }
    except Exception:
        return None
