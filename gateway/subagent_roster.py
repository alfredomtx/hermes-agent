"""Pure state + rendering for the live delegate_task subagent roster bubble.

Gateway-free and import-cycle-free (only depends on the tiny shared
``gateway.duration_format`` util, never on ``gateway.run``). The gateway
consumer owns one instance per turn, feeds it lifecycle sentinels via
``apply_event``, and renders with ``fold`` + ``format_subagent_roster``.

Design contract (see plan ~/.hermes/plans/subagent-roster-bubble.md):
- SINGLE-WRITER per-turn state. Only the loop-bound gateway consumer mutates an
  instance. NOT thread-safe and deliberately lock-free: the worker-thread
  progress callback only ENQUEUES sentinels, it never touches this object.
- Membership + terminal status come from subagent.start/.complete EVENTS
  (the active-subagent registry deletes a child the instant it finishes, so a
  poll can never observe done/errored/timed-out). Live elapsed for RUNNING rows
  comes from a periodic poll of the registry, passed into ``fold`` as
  ``active_by_id``.
- Rows NEVER reorder: first-seen order is stable, a done row stays in place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from gateway.duration_format import format_duration

# Throttle for roster bubble edits, in seconds. Module constant so it is defined
# once and imported by the consumer (never re-declared locally).
ROSTER_EDIT_INTERVAL = 3.0

_LABEL_CAP = 32
_MAX_ROWS = 10

# delegate_tool subagent.complete status string -> (glyph, display bucket).
# Vocabulary verified in tools/delegate_tool.py: completed | failed |
# interrupted | timeout | error. Unknown -> fail-CLOSED to errored (never render
# an unrecognised terminal state as success).
STATUS_GLYPH: Dict[str, Tuple[str, str]] = {
    "completed": ("✓", "done"),
    "failed": ("✗", "errored"),
    "error": ("✗", "errored"),
    "timeout": ("⏱", "timed-out"),
    "interrupted": ("⏹", "interrupted"),
}
_UNKNOWN_GLYPH = ("?", "errored")


def roster_label(goal: Optional[str]) -> str:
    """Collapse whitespace/newlines and hard-cap a child goal for one row."""
    text = " ".join(str(goal or "").split())
    if not text:
        return "subagent"
    if len(text) > _LABEL_CAP:
        return text[: _LABEL_CAP - 1] + "…"
    return text


@dataclass
class SubagentRosterState:
    """Single-writer per-turn roster state. Lock-free by design."""

    # sid -> {"goal", "task_index", "started_at"}
    meta: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # sid -> {"status", "duration"}
    terminal: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # first-seen order; rows never reorder
    seen_order: List[str] = field(default_factory=list)

    def has_records(self) -> bool:
        return bool(self.seen_order)

    def start(
        self,
        sid: str,
        goal: Optional[str] = None,
        task_index: int = 0,
        started_at: float = 0.0,
    ) -> None:
        if not sid:
            return
        if sid not in self.meta:
            self.seen_order.append(sid)
        self.meta[sid] = {
            "goal": goal or "",
            "task_index": int(task_index or 0),
            "started_at": float(started_at or 0.0),
        }
        # A re-run/restart of the same sid clears any prior terminal state.
        self.terminal.pop(sid, None)

    def complete(
        self,
        sid: str,
        status: str = "completed",
        duration: float = 0.0,
        started_at: float = 0.0,
    ) -> None:
        if not sid:
            return
        if sid not in self.meta:
            # subagent.complete with no prior start (fast child): synthesize a
            # minimal meta so the row still renders.
            self.seen_order.append(sid)
            self.meta[sid] = {
                "goal": "",
                "task_index": 0,
                "started_at": float(started_at or 0.0),
            }
        self.terminal[sid] = {
            "status": str(status or "completed").lower(),
            "duration": float(duration or 0.0),
        }

    def apply_event(self, raw: Tuple[Any, ...]) -> None:
        """Mutate from a dequeued sentinel tuple (loop thread only).

        ``("__roster_start__", sid, goal, task_index, started_at)``
        ``("__roster_complete__", sid, status, duration)``
        """
        if not raw:
            return
        kind = raw[0]
        if kind == "__roster_start__":
            _, sid, goal, task_index, started_at = raw
            self.start(sid, goal, task_index, started_at)
        elif kind == "__roster_complete__":
            _, sid, status, duration = raw
            self.complete(sid, status, duration)

    def fold(self, active_by_id: Dict[str, Dict[str, Any]], now: float) -> List[Dict[str, Any]]:
        """Build ordered display rows from current state + a registry snapshot.

        ``active_by_id``: sid -> live registry record (started_at, tool_count),
        already filtered to this run's sids. Used only for RUNNING rows.
        """
        rows: List[Dict[str, Any]] = []
        for sid in self.seen_order:
            m = self.meta.get(sid) or {}
            label = roster_label(m.get("goal"))
            if sid in self.terminal:
                t = self.terminal[sid]
                glyph, _bucket = STATUS_GLYPH.get(
                    str(t.get("status") or "").lower(), _UNKNOWN_GLYPH
                )
                rows.append({
                    "glyph": glyph,
                    "label": label,
                    "elapsed": float(t.get("duration") or 0.0),
                    "running": False,
                    "tools": 0,
                })
            else:
                rec = active_by_id.get(sid) or {}
                started = rec.get("started_at") or m.get("started_at") or now
                rows.append({
                    "glyph": "▶",
                    "label": label,
                    "elapsed": max(0.0, now - float(started)),
                    "running": True,
                    "tools": int(rec.get("tool_count") or 0),
                })
        return rows


def _bucket_of(row: Dict[str, Any]) -> str:
    if row["running"]:
        return "running"
    glyph = row["glyph"]
    for _status, (g, bucket) in STATUS_GLYPH.items():
        if g == glyph:
            return bucket
    return "errored"


def format_subagent_roster(rows: List[Dict[str, Any]], *, collapsed: bool = False) -> Optional[str]:
    """Render roster rows into a bubble string. None when there are no rows."""
    if not rows:
        return None

    running = [r for r in rows if r["running"]]
    done = [r for r in rows if (not r["running"] and r["glyph"] == "✓")]
    errored = [r for r in rows if (not r["running"] and r["glyph"] != "✓")]

    if collapsed:
        # One-line summary so a wall of done rows never sits above the answer.
        # Elapsed proxy = the longest row (~ parallel wall span).
        span = max((r["elapsed"] for r in rows), default=0.0)
        parts = [f"🤖 {len(rows)} subagent" + ("s" if len(rows) != 1 else "")]
        if done:
            parts.append(f"{len(done)} ✓")
        if errored:
            parts.append(f"{len(errored)} ✗")
        parts.append(format_duration(span))
        return " · ".join(parts)

    head = f"🤖 Subagents — {len(running)} running"
    if done:
        head += f", {len(done)} done"
    if errored:
        head += f", {len(errored)} failed"

    lines = [head]
    shown = rows[:_MAX_ROWS]
    for r in shown:
        line = f"{r['glyph']} {r['label']} · {format_duration(r['elapsed'])}"
        if r["running"] and r["tools"] > 0:
            line += f" · {r['tools']} tool" + ("s" if r["tools"] != 1 else "")
        lines.append(line)
    extra = len(rows) - len(shown)
    if extra > 0:
        lines.append(f"… +{extra} more")
    return "\n".join(lines)
