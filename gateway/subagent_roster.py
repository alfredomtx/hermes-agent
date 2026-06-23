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
# once and imported by the consumer (never re-declared locally). Set to 10s:
# Telegram enforces a per-chat edit flood ceiling, and a busy chat with other
# bubbles can trip "Flood control exceeded" — which froze the live timer. A
# 10s cadence stays well under the ceiling; the final collapse (force) bypasses
# this throttle so the terminal state always lands. This is the DEFAULT; the
# effective value is per-platform configurable via
# display.platforms.<platform>.subagent_roster_interval (see resolve_roster_interval).
ROSTER_EDIT_INTERVAL = 10.0

# Hard floor so a misconfigured tiny interval can never flood the platform's
# edit rate limiter. Mirrors the clamp in gateway.display_config._normalise.
ROSTER_EDIT_INTERVAL_FLOOR = 1.0


def resolve_roster_interval(user_config: Any, platform_key: str) -> float:
    """Resolve the effective roster edit interval (seconds) for a platform.

    Reads ``display.platforms.<platform>.subagent_roster_interval`` (falling
    back through the global setting and the built-in default) and clamps to a
    1.0s floor. Best-effort: any error returns the default ``ROSTER_EDIT_INTERVAL``.
    """
    try:
        from gateway.display_config import resolve_display_setting

        raw = resolve_display_setting(
            user_config,
            platform_key,
            "subagent_roster_interval",
            ROSTER_EDIT_INTERVAL,
        )
        return max(ROSTER_EDIT_INTERVAL_FLOOR, float(raw))
    except Exception:
        return ROSTER_EDIT_INTERVAL

_LABEL_CAP = 100
_MAX_ROWS = 10


def shorten_model(model: Optional[str]) -> str:
    """Compact a model id for a roster row.

    Strips a leading region/provider dotted prefix so a row stays readable:
    ``us.anthropic.claude-opus-4-8`` -> ``opus-4-8``,
    ``claude-sonnet-4-6`` -> ``sonnet-4-6``. Model ids whose only dots are a
    version number (``gpt-5.5``) are left intact. Best-effort: unknown shapes
    return unchanged.
    """
    text = str(model or "").strip()
    if not text:
        return ""
    # Only treat dots as a provider/region prefix separator when EVERY segment
    # before the last is a bare alpha token (us, anthropic, openai, ...). This
    # avoids mangling a version dot like "gpt-5.5" (segment "5" is not alpha).
    if "." in text:
        segs = text.split(".")
        if all(s.isalpha() for s in segs[:-1]):
            text = segs[-1]
    # Strip a redundant vendor token so "claude-opus-4-8" -> "opus-4-8".
    for vendor in ("claude-", "anthropic-"):
        if text.startswith(vendor):
            text = text[len(vendor):]
            break
    return text


def reasoning_tag(reasoning: Any) -> str:
    """Render a reasoning_config dict/string into a short effort tag.

    ``{"enabled": True, "effort": "high"}`` -> ``high``;
    ``{"enabled": False}`` -> ``""`` (no reasoning, nothing to show);
    a bare string ``"max"`` -> ``max``. Anything else -> ``""``.
    """
    if isinstance(reasoning, dict):
        if not reasoning.get("enabled", True):
            return ""
        return str(reasoning.get("effort") or "").strip().lower()
    if isinstance(reasoning, str):
        return reasoning.strip().lower()
    return ""


def _model_suffix(row: Dict[str, Any]) -> str:
    """`· <model> <reasoning>` suffix for a row, or '' when no model known."""
    model = shorten_model(row.get("model"))
    if not model:
        return ""
    tag = reasoning_tag(row.get("reasoning"))
    return f" · {model} {tag}".rstrip() if tag else f" · {model}"

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
        model: Optional[str] = None,
        reasoning: Any = None,
    ) -> None:
        if not sid:
            return
        if sid not in self.meta:
            self.seen_order.append(sid)
        self.meta[sid] = {
            "goal": goal or "",
            "task_index": int(task_index or 0),
            "started_at": float(started_at or 0.0),
            "model": model or "",
            "reasoning": reasoning,
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
                "model": "",
                "reasoning": None,
            }
        self.terminal[sid] = {
            "status": str(status or "completed").lower(),
            "duration": float(duration or 0.0),
        }

    def apply_event(self, raw: Tuple[Any, ...]) -> None:
        """Mutate from a dequeued sentinel tuple (loop thread only).

        ``("__roster_start__", sid, goal, task_index, started_at[, model, reasoning])``
        ``("__roster_complete__", sid, status, duration)``

        The start tuple's model/reasoning tail is optional so older producers
        (and replayed queues) without them still apply cleanly.
        """
        if not raw:
            return
        kind = raw[0]
        if kind == "__roster_start__":
            sid = raw[1] if len(raw) > 1 else ""
            goal = raw[2] if len(raw) > 2 else ""
            task_index = raw[3] if len(raw) > 3 else 0
            started_at = raw[4] if len(raw) > 4 else 0.0
            model = raw[5] if len(raw) > 5 else None
            reasoning = raw[6] if len(raw) > 6 else None
            self.start(sid, goal, task_index, started_at, model, reasoning)
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
            model = m.get("model") or ""
            reasoning = m.get("reasoning")
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
                    "model": model,
                    "reasoning": reasoning,
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
                    "model": model,
                    "reasoning": reasoning,
                })
        return rows


def _bucket_of(row: Dict[str, Any]) -> str:
    explicit = str(row.get("bucket") or "").strip().lower()
    if explicit:
        return explicit
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

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(_bucket_of(row), []).append(row)

    running = buckets.get("running", [])
    pending = buckets.get("pending", [])
    done = buckets.get("done", [])
    errored = buckets.get("errored", [])
    timed_out = buckets.get("timed-out", [])
    interrupted = buckets.get("interrupted", [])
    failed_total = errored + timed_out + interrupted

    if collapsed:
        # Final render. Keep the per-child breakdown (each child marked with its
        # terminal glyph) instead of collapsing to a bare one-liner — the user
        # wants to see WHICH children did what, not just a count. The summary
        # line becomes a header above the rows.
        span = max((r["elapsed"] for r in rows), default=0.0)
        head_parts = [f"🤖 {len(rows)} subagent" + ("s" if len(rows) != 1 else "")]
        if pending:
            head_parts.append(f"{len(pending)} pending")
        if done:
            head_parts.append(f"{len(done)} ✓")
        if failed_total:
            head_parts.append(f"{len(failed_total)} ✗")
        head_parts.append(format_duration(span))
        head = " · ".join(head_parts)

        lines = [head]
        shown = rows[:_MAX_ROWS]
        for r in shown:
            # On the final render a running row (shouldn't normally happen) is
            # shown with its live glyph; terminal rows keep ✓/✗/⏱/⏹.
            line = f"{r['glyph']} {r['label']}{_model_suffix(r)} · {format_duration(r['elapsed'])}"
            lines.append(line)
        extra = len(rows) - len(shown)
        if extra > 0:
            lines.append(f"… +{extra} more")
        return "\n".join(lines)

    head = f"🤖 Subagents — {len(running)} running"
    if pending:
        head += f", {len(pending)} pending"
    if done:
        head += f", {len(done)} done"
    if failed_total:
        head += f", {len(failed_total)} failed"

    lines = [head]
    shown = rows[:_MAX_ROWS]
    for r in shown:
        line = f"{r['glyph']} {r['label']}{_model_suffix(r)} · {format_duration(r['elapsed'])}"
        if r["running"] and r["tools"] > 0:
            line += f" · {r['tools']} tool" + ("s" if r["tools"] != 1 else "")
        lines.append(line)
    extra = len(rows) - len(shown)
    if extra > 0:
        lines.append(f"… +{extra} more")
    return "\n".join(lines)
