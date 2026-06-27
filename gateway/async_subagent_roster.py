"""Rows for watcher-owned background delegate_task roster bubbles."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from gateway.subagent_roster import STATUS_GLYPH, roster_label

_PENDING_STATUSES = {"pending", "queued", "dispatched", "running"}


def _normalise_status(raw: Any) -> str:
    status = str(raw or "").strip().lower()
    if status == "success":
        return "completed"
    if status in STATUS_GLYPH:
        return status
    if status in _PENDING_STATUSES:
        return "pending"
    return "error"


def _children_from_record(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    children = record.get("children")
    if isinstance(children, list) and children:
        out = [dict(c) for c in children if isinstance(c, dict)]
    else:
        goals = record.get("goals") or []
        results = {
            int(r.get("task_index", -1)): r
            for r in (record.get("results") or [])
            if isinstance(r, dict)
        }
        out = []
        for i, goal in enumerate(goals):
            result = results.get(i, {})
            out.append(
                {
                    "task_index": i,
                    "subagent_id": str(result.get("subagent_id") or ""),
                    "goal": goal,
                    "model": record.get("model"),
                    "status": result.get("status") or "pending",
                    "duration_seconds": result.get("duration_seconds"),
                    "tool_count": result.get("tool_count"),
                    "api_calls": result.get("api_calls"),
                    "completed_at": record.get("completed_at"),
                }
            )

    out.sort(key=lambda c: int(c.get("task_index", 0) or 0))
    return out


def build_async_subagent_roster_rows(
    record: Dict[str, Any],
    active_subagents: List[Dict[str, Any]],
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Build formatter rows for one async delegation record.

    Async record children provide stable membership and terminal status.
    The active-subagents registry provides live running elapsed and tool counts.
    """
    now = time.time() if now is None else float(now)
    active_by_id = {
        str(item.get("subagent_id") or ""): item
        for item in active_subagents or []
        if item.get("subagent_id")
    }

    rows: List[Dict[str, Any]] = []
    for child in _children_from_record(record):
        sid = str(child.get("subagent_id") or "")
        active = active_by_id.get(sid)
        status = _normalise_status(child.get("status"))
        label = roster_label(child.get("goal"))
        model = child.get("model") or record.get("model") or ""
        profile = child.get("profile") or ""
        reasoning = child.get("reasoning")
        if reasoning is None:
            reasoning = record.get("reasoning")

        if active is not None and status == "pending":
            started = (
                active.get("started_at")
                or child.get("started_at")
                or record.get("dispatched_at")
                or now
            )
            try:
                elapsed = max(0.0, now - float(started))
            except Exception:
                elapsed = 0.0
            rows.append(
                {
                    "glyph": "▶",
                    "label": label,
                    "elapsed": elapsed,
                    "running": True,
                    "tools": int(active.get("tool_count") or 0),
                    "bucket": "running",
                    "model": model,
                    "profile": profile,
                    "reasoning": reasoning,
                }
            )
            continue

        if status == "pending":
            rows.append(
                {
                    "glyph": "◦",
                    "label": label,
                    "elapsed": 0.0,
                    "running": False,
                    "tools": 0,
                    "bucket": "pending",
                    "model": model,
                    "profile": profile,
                    "reasoning": reasoning,
                }
            )
            continue

        glyph, bucket = STATUS_GLYPH.get(status, ("?", "errored"))
        duration = child.get("duration_seconds")
        if duration is None:
            started = child.get("started_at") or record.get("dispatched_at")
            completed = child.get("completed_at") or record.get("completed_at")
            try:
                duration = max(0.0, float(completed) - float(started))
            except Exception:
                duration = 0.0

        # Final tool count: the child record carries tool_count (falling back to
        # api_calls). The live registry entry is gone once the child completes,
        # so the finished row keeps the count from the record.
        try:
            tools = int(child.get("tool_count") or child.get("api_calls") or 0)
        except (TypeError, ValueError):
            tools = 0

        # Final cost: threaded as a PUBLIC cost_usd onto the child record (see
        # delegate_tool._run_single_child + async_delegation._update_child_result_locked).
        # Completion-only; missing/non-numeric -> None so the row shows no cost cell.
        _row_cost = child.get("cost_usd")
        rows.append(
            {
                "glyph": glyph,
                "label": label,
                "elapsed": float(duration or 0.0),
                "running": False,
                "tools": tools,
                "bucket": bucket,
                "model": model,
                "profile": profile,
                "reasoning": reasoning,
                "cost_usd": float(_row_cost) if isinstance(_row_cost, (int, float)) else None,
            }
        )

    return rows


def build_async_dispatched_seed_rows(record: Dict[str, Any]) -> List[str]:
    """Card-style 'here is what I dispatched' rows for a BACKGROUND record.

    One row per child in the same shape as the foreground delegate_task params
    card (``[profile] · [role=… · toolsets=…] · `goal```). Per-child ``profile``
    is used when the child record carries it (threaded in by delegate_tool); it
    falls back to record-level ``role``/``toolsets`` when a child has none.
    Returns [] when the record has no children/goals.
    """
    # Lazy import: gateway.run owns the canonical cell renderers; a module-load
    # import would create a run.py <-> async_subagent_roster cycle.
    from gateway.run import _delegate_goal_cell, _delegate_param_cells

    children = _children_from_record(record)
    if not children:
        return []

    record_spec = {
        k: record.get(k)
        for k in ("role", "toolsets")
        if record.get(k) not in (None, "", [], {})
    }
    record_cells = _delegate_param_cells(record_spec)

    rows: List[str] = []
    for child in children:
        cells: List[str] = []
        profile = child.get("profile")
        if profile not in (None, "", [], {}):
            cells.append(" ".join(str(profile).replace("`", "").split()))
        else:
            cells.extend(record_cells)
        cells.append(_delegate_goal_cell(child.get("goal")))
        rows.append(" · ".join(cells))
    return rows
