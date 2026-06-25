"""Shared-topic context backfill from the local session DB.

When a Telegram message opens a NEW session in a SHARED topic/group, there is
no platform API we can call to fetch what already happened in that topic: the
Bot API cannot read arbitrary topic history, and bot-authored messages (the
dual-review bridge, cron posts, other Hermes sessions) never arrive as updates
at all. The ONLY local record of "what happened in this topic" is the
transcripts of OTHER Hermes sessions bound to the same
``platform + chat_id + thread_id``.

This module resolves those sibling sessions (the inverse of
``gateway.mirror._find_session_id``: ALL participants in the topic, excluding
the current session, with no user-match requirement), pulls their recent
text-only user/assistant messages out of ``state.db``, merges + sorts by
timestamp + caps + dedups + age-filters them, and renders one attributed
read-only block. The Telegram adapter assigns that block to
``event.channel_context`` (mirroring the Discord adapter), and the existing
``run.py`` fold injects it AFTER the sender prefix into both the in-context
message and the persisted user row.

Everything here is defensive: ``build_topic_backfill`` NEVER raises and
returns ``None`` on any error or when there is nothing to show, so a backfill
failure can never break message handling.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Roles whose text we surface in the backfill block. Tool calls / tool results
# and system rows are intentionally excluded — they are noise for "what was
# said in this topic".
_BACKFILL_ROLES = ("user", "assistant")


def _hermes_home():
    """Resolve the Hermes home at CALL time.

    Path constants like ``mirror._SESSIONS_INDEX`` and
    ``hermes_state.DEFAULT_DB_PATH`` are computed at import time, which breaks
    tests that point ``HERMES_HOME`` at a temp dir after import. Resolving
    here keeps the helper correct under a relocated home.
    """
    from hermes_cli.config import get_hermes_home

    return get_hermes_home()


def _sessions_index_path():
    return _hermes_home() / "sessions" / "sessions.json"


def _open_session_db():
    """Open the SessionDB pointed at the CURRENT Hermes home's state.db.

    Returns ``None`` if the DB cannot be opened.
    """
    try:
        from hermes_state import SessionDB

        return SessionDB(db_path=_hermes_home() / "state.db", read_only=True)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("topic_backfill: could not open SessionDB: %s", e)
        return None


def collect_sibling_session_ids(
    platform: str,
    chat_id: str,
    thread_id: Optional[str],
    exclude_session_id: Optional[str],
) -> List[str]:
    """Return session_ids of OTHER sessions bound to this exact topic.

    Inverse of ``mirror._find_session_id``: instead of picking the single best
    session for a sender, collect EVERY session whose ``origin`` matches the
    same ``platform + chat_id + thread_id`` — all participants, no user-match
    requirement — and drop ``exclude_session_id`` (the current/new session).

    thread_id matching is exact: ``None`` only matches entries with no thread,
    a value matches only the same value. This keeps one topic's backfill from
    bleeding into a sibling topic in the same chat.
    """
    index_path = _sessions_index_path()
    if not index_path.exists():
        return []

    try:
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    if not isinstance(data, dict):
        return []

    platform_lower = str(platform).lower()
    want_thread = "" if thread_id is None else str(thread_id)
    exclude = str(exclude_session_id) if exclude_session_id else None

    session_ids: List[str] = []
    seen: set = set()
    for _key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        origin = entry.get("origin") or {}
        entry_platform = (origin.get("platform") or entry.get("platform", "")).lower()
        if entry_platform != platform_lower:
            continue

        origin_chat_id = str(origin.get("chat_id", ""))
        if origin_chat_id != str(chat_id):
            continue

        origin_thread_id = str(origin.get("thread_id") or "")
        if origin_thread_id != want_thread:
            continue

        sid = entry.get("session_id")
        if not sid:
            continue
        sid = str(sid)
        if exclude is not None and sid == exclude:
            continue
        if sid in seen:
            continue
        seen.add(sid)
        session_ids.append(sid)

    return session_ids


def _source_label(entry_origin: Dict[str, Any]) -> str:
    """Best human-readable attribution label for a sibling message."""
    return (
        entry_origin.get("user_name")
        or entry_origin.get("user_id")
        or entry_origin.get("chat_name")
        or "someone"
    )


def _session_origin_map(session_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Map session_id -> its origin dict from sessions.json (for labels)."""
    index_path = _sessions_index_path()
    result: Dict[str, Dict[str, Any]] = {}
    if not index_path.exists():
        return result
    try:
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return result
    if not isinstance(data, dict):
        return result
    wanted = set(session_ids)
    for _key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("session_id") or "")
        if sid in wanted:
            result[sid] = entry.get("origin") or {}
    return result


def _coerce_timestamp(raw: Any) -> Optional[float]:
    """Best-effort coercion of a message timestamp to an epoch float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        # Numeric string (epoch).
        try:
            return float(s)
        except ValueError:
            pass
        # ISO-8601 string.
        try:
            from datetime import datetime

            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    # datetime-like
    try:
        return float(raw.timestamp())
    except Exception:
        return None


def get_recent_topic_messages(
    platform: str,
    chat_id: str,
    thread_id: Optional[str],
    exclude_session_id: Optional[str],
    max_messages: int = 15,
    max_age_hours: int = 24,
) -> List[Dict[str, Any]]:
    """Pull, merge, filter, sort, dedup and cap topic messages.

    Returns a list of dicts ``{"label", "role", "text", "timestamp"}`` in
    chronological order, at most ``max_messages`` (the most RECENT ones).

    Filters applied per message:
      - role in (user, assistant)
      - ``isinstance(content, str)`` (multimodal rows are decoded to lists and
        must be skipped, per the reviewer caveat)
      - non-empty after strip
      - within ``max_age_hours`` of now (when a timestamp is present)

    Cross-sibling ordering: ``SessionDB.get_messages`` orders by id, not
    timestamp, so we sort the MERGED set by timestamp ourselves.
    Dedup key is ``(role, normalized-content)`` so the same line mirrored into
    multiple sibling transcripts only shows once.
    """
    session_ids = collect_sibling_session_ids(
        platform, chat_id, thread_id, exclude_session_id
    )
    if not session_ids:
        return []

    db = _open_session_db()
    if db is None:
        return []

    origin_map = _session_origin_map(session_ids)

    now = time.time()
    max_age_seconds = max_age_hours * 3600 if max_age_hours and max_age_hours > 0 else None

    collected: List[Dict[str, Any]] = []
    for sid in session_ids:
        try:
            rows = db.get_messages(sid)
        except Exception as e:
            logger.debug("topic_backfill: get_messages failed for %s: %s", sid, e)
            continue

        label = _source_label(origin_map.get(sid, {}))
        for row in rows:
            role = row.get("role")
            if role not in _BACKFILL_ROLES:
                continue
            content = row.get("content")
            # Multimodal/decoded-list content (and anything non-str) is skipped.
            if not isinstance(content, str):
                continue
            text = content.strip()
            if not text:
                continue
            # Skip mirror/observed bookkeeping rows that are not real turns?
            # We keep observed rows: they ARE prior topic activity. Only the
            # role/text/age filters gate inclusion.
            ts = _coerce_timestamp(row.get("timestamp"))
            if max_age_seconds is not None and ts is not None:
                if (now - ts) > max_age_seconds:
                    continue
            collected.append(
                {
                    "label": label,
                    "role": role,
                    "text": text,
                    # Sort key: rows with no timestamp sink to the front so a
                    # present-timestamp recent row always wins the cap.
                    "timestamp": ts if ts is not None else 0.0,
                }
            )

    if not collected:
        return []

    # Sort the MERGED set chronologically (get_messages is id-ordered per
    # session, not across siblings).
    collected.sort(key=lambda m: m["timestamp"])

    # Dedup by (role, normalized text), keeping the FIRST (earliest)
    # chronological occurrence.
    deduped: List[Dict[str, Any]] = []
    seen: set = set()
    for msg in collected:
        key = (msg["role"], " ".join(msg["text"].split()).lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(msg)

    # Cap to the most recent ``max_messages`` (tail of the chronological list).
    if max_messages and max_messages > 0 and len(deduped) > max_messages:
        deduped = deduped[-max_messages:]

    return deduped


def render_backfill_block(messages: List[Dict[str, Any]]) -> Optional[str]:
    """Render an attributed, read-only context block. None when empty."""
    if not messages:
        return None

    header = (
        "[Earlier in this topic — from other sessions, READ-ONLY context]\n"
        "The lines below are prior activity in this shared topic captured from "
        "other Hermes sessions. They are background only: do not reply to them "
        "line by line, just use them as context for the new message."
    )

    lines = [header]
    for msg in messages:
        label = msg.get("label") or "someone"
        role = msg.get("role") or "user"
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        # Collapse internal newlines so each prior turn is one readable line.
        flat = " ".join(text.split())
        lines.append(f"- [{label} · {role}] {flat}")

    if len(lines) == 1:
        return None
    return "\n".join(lines)


def build_topic_backfill(
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
    exclude_session_id: Optional[str] = None,
    max_messages: int = 15,
    max_age_hours: int = 24,
) -> Optional[str]:
    """Top-level resolve + render. NEVER raises; None on error or empty.

    This is the single entry point the Telegram adapter calls. Any failure
    (missing index, DB error, malformed rows) degrades to ``None`` so message
    handling is never broken by a backfill problem.
    """
    try:
        messages = get_recent_topic_messages(
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
            exclude_session_id=exclude_session_id,
            max_messages=max_messages,
            max_age_hours=max_age_hours,
        )
        return render_backfill_block(messages)
    except Exception as e:  # pragma: no cover - defensive catch-all
        logger.debug("topic_backfill: build failed: %s", e)
        return None
