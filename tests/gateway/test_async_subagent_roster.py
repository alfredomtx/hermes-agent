from gateway.async_subagent_roster import build_async_subagent_roster_rows
from gateway.subagent_roster import format_subagent_roster


def test_async_rows_use_active_registry_for_running_elapsed():
    record = {
        "delegation_id": "deleg_1",
        "dispatched_at": 100.0,
        "children": [
            {"task_index": 0, "subagent_id": "sa-0", "goal": "sleep 6", "status": "pending"},
            {"task_index": 1, "subagent_id": "sa-1", "goal": "sleep 10", "status": "pending"},
        ],
    }
    active = [
        {"subagent_id": "sa-0", "started_at": 101.0, "tool_count": 2},
        {"subagent_id": "sa-1", "started_at": 102.0, "tool_count": 0},
    ]

    rows = build_async_subagent_roster_rows(record, active, now=107.0)

    assert rows[0]["glyph"] == "▶"
    assert rows[0]["elapsed"] == 6.0
    assert rows[0]["tools"] == 2
    assert rows[1]["glyph"] == "▶"
    assert rows[1]["elapsed"] == 5.0


def test_async_rows_use_record_status_for_live_done_counts():
    record = {
        "delegation_id": "deleg_1",
        "children": [
            {
                "task_index": 0,
                "subagent_id": "sa-0",
                "goal": "sleep 6",
                "status": "completed",
                "duration_seconds": 6.0,
            },
            {"task_index": 1, "subagent_id": "sa-1", "goal": "sleep 10", "status": "pending"},
        ],
    }
    active = [{"subagent_id": "sa-1", "started_at": 100.0, "tool_count": 0}]

    rows = build_async_subagent_roster_rows(record, active, now=108.0)
    text = format_subagent_roster(rows)

    assert "🤖 Subagents — 1 running, 1 done" in text
    assert "✓ sleep 6 · 0:06" in text
    assert "▶ sleep 10 · 0:08" in text


def test_async_terminal_row_keeps_final_tool_count():
    # A finished background child keeps its tool count. The child record carries
    # the final count (tool_count, falling back to api_calls); the registry has
    # already dropped the live entry by the time it completes.
    record = {
        "delegation_id": "deleg_1",
        "children": [
            {
                "task_index": 0,
                "subagent_id": "sa-0",
                "goal": "review",
                "status": "completed",
                "duration_seconds": 949.0,
                "tool_count": 56,
            },
            {
                "task_index": 1,
                "subagent_id": "sa-1",
                "goal": "audit",
                "status": "completed",
                "duration_seconds": 287.0,
                "api_calls": 23,
            },
        ],
    }
    rows = build_async_subagent_roster_rows(record, [], now=2000.0)
    assert rows[0]["tools"] == 56
    assert rows[1]["tools"] == 23  # falls back to api_calls
    text = format_subagent_roster(rows, collapsed=True)
    assert "✓ review · 15:49 · 56 tools" in text
    assert "✓ audit · 4:47 · 23 tools" in text


def test_async_final_rows_fallback_to_results_when_children_missing():
    record = {
        "delegation_id": "deleg_1",
        "goals": ["sleep 6", "sleep 10"],
        "results": [
            {"task_index": 0, "status": "completed", "duration_seconds": 6.0},
            {"task_index": 1, "status": "failed", "duration_seconds": 10.0},
        ],
    }

    rows = build_async_subagent_roster_rows(record, [], now=200.0)
    text = format_subagent_roster(rows, collapsed=True)

    # Collapsed render now keeps the per-child breakdown under a summary header.
    lines = text.split("\n")
    assert lines[0] == "🤖 2 subagents · 1 ✓ · 1 ✗ · 0:10"
    assert lines[1] == "✓ sleep 6 · 0:06"
    assert lines[2] == "✗ sleep 10 · 0:10"


# ---------------------------------------------------------------------------
# Watcher-owned publisher integration tests (Task 7 + B1/B3/C5 folds)
# ---------------------------------------------------------------------------

import types

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult


class AsyncRosterAdapter(BasePlatformAdapter):
    def __init__(self, platform=Platform.TELEGRAM):
        super().__init__(PlatformConfig(enabled=True, token="***"), platform)
        self.sent = []
        self.edits = []
        self._seq = 0

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None, buttons=None):
        self._seq += 1
        self.sent.append({"chat_id": chat_id, "content": content, "metadata": metadata})
        return SendResult(success=True, message_id=f"async-roster-{self._seq}")

    async def edit_message(self, chat_id, message_id, content, **kwargs):
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "content": content,
                "kwargs": kwargs,
            }
        )
        return SendResult(success=True, message_id=message_id)

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


class NoEditAsyncRosterAdapter(AsyncRosterAdapter):
    edit_message = BasePlatformAdapter.edit_message


def _runner(adapter, *, entries=None):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {adapter.platform: adapter}
    runner.session_store = types.SimpleNamespace(
        _ensure_loaded=lambda: None, _entries=entries or {}
    )
    runner._session_sources = {}
    runner._async_subagent_roster_bubbles = {}
    return runner


def _record(status="running"):
    return {
        "type": "async_delegation",
        "delegation_id": "deleg_bg",
        "session_key": "agent:main:telegram:group:-1001:77",
        "platform": "telegram",
        "chat_type": "group",
        "chat_id": "-1001",
        "thread_id": "77",
        "message_id": "42",
        "is_batch": True,
        "status": status,
        "dispatched_at": 100.0,
        "children": [
            {"task_index": 0, "subagent_id": "sa-0", "goal": "sleep 6", "status": "pending"},
            {"task_index": 1, "subagent_id": "sa-1", "goal": "sleep 10", "status": "pending"},
        ],
    }


@pytest.mark.asyncio
async def test_watcher_roster_seeds_edits_and_collapses(monkeypatch):
    import gateway.run as gateway_run

    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"display": {"platforms": {"telegram": {"subagent_roster": "on"}}}},
    )

    adapter = AsyncRosterAdapter()
    runner = _runner(adapter)
    record = _record()

    await runner._tick_async_delegation_rosters(
        [record],
        [
            {"subagent_id": "sa-0", "started_at": 101.0, "tool_count": 1},
            {"subagent_id": "sa-1", "started_at": 102.0, "tool_count": 0},
        ],
    )

    assert len(adapter.sent) == 1
    assert "🤖 Subagents" in adapter.sent[0]["content"]
    assert "sleep 6" in adapter.sent[0]["content"]
    assert adapter.sent[0]["metadata"]["thread_id"] == "77"

    record["children"][0]["status"] = "completed"
    record["children"][0]["duration_seconds"] = 6.0

    await runner._publish_async_delegation_roster(
        record,
        [{"subagent_id": "sa-1", "started_at": 102.0, "tool_count": 0}],
        force=True,
        collapsed=False,
    )

    assert len(adapter.sent) == 1
    assert adapter.edits
    assert "1 running, 1 done" in adapter.edits[-1]["content"]
    assert "✓ sleep 6 · 0:06" in adapter.edits[-1]["content"]

    final_evt = _record(status="completed")
    final_evt["children"][0]["status"] = "completed"
    final_evt["children"][0]["duration_seconds"] = 6.0
    final_evt["children"][1]["status"] = "completed"
    final_evt["children"][1]["duration_seconds"] = 10.0

    await runner._finalize_async_delegation_roster(final_evt, [])

    assert "🤖 2 subagents · 2 ✓ · 0:10" in adapter.edits[-1]["content"]
    assert "deleg_bg" not in runner._async_subagent_roster_bubbles


@pytest.mark.asyncio
async def test_watcher_roster_respects_off_knob(monkeypatch):
    import gateway.run as gateway_run

    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"display": {"platforms": {"telegram": {"subagent_roster": "off"}}}},
    )

    adapter = AsyncRosterAdapter()
    runner = _runner(adapter)

    await runner._tick_async_delegation_rosters([_record()], [])

    assert adapter.sent == []
    assert adapter.edits == []


@pytest.mark.asyncio
async def test_watcher_roster_noops_without_edit_adapter(monkeypatch):
    import gateway.run as gateway_run

    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"display": {"platforms": {"telegram": {"subagent_roster": "on"}}}},
    )

    adapter = NoEditAsyncRosterAdapter()
    runner = _runner(adapter)

    await runner._tick_async_delegation_rosters([_record()], [])

    assert adapter.sent == []
    assert adapter.edits == []


@pytest.mark.asyncio
async def test_watcher_roster_routing_wins_over_session_store_origin(monkeypatch):
    """B1: stored routing must beat a stale/foreground session-store origin.

    The session-store entry for this session_key carries an origin with a
    DIFFERENT thread (a foreground topic). The roster must still post to the
    dispatch-time routing thread (77) with reply anchor 42, proving the
    session-store-origin fast path did NOT override the captured routing.
    """
    import gateway.run as gateway_run

    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"display": {"platforms": {"telegram": {"subagent_roster": "on"}}}},
    )

    from gateway.session import SessionSource

    stale_origin = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        thread_id="999",  # WRONG topic — foreground origin
    )
    entries = {
        "agent:main:telegram:group:-1001:77": types.SimpleNamespace(origin=stale_origin)
    }

    adapter = AsyncRosterAdapter()
    runner = _runner(adapter, entries=entries)
    record = _record()

    await runner._tick_async_delegation_rosters(
        [record],
        [{"subagent_id": "sa-0", "started_at": 101.0, "tool_count": 0}],
    )

    assert len(adapter.sent) == 1
    md = adapter.sent[0]["metadata"]
    assert md is not None
    # routing (77) wins over stale session-store origin (999)
    assert md["thread_id"] == "77"
    # reply anchor is the captured message_id (42), surfaced for Telegram
    assert str(md.get("telegram_reply_to_message_id") or md.get("reply_to_message_id") or "") in ("42", "")


@pytest.mark.asyncio
async def test_watcher_roster_collapses_when_batch_finished_before_first_tick(monkeypatch):
    """B3: a fast batch can complete before the watcher's first tick — the
    finalizer must still SEED a collapsed bubble even though none ever existed.
    """
    import gateway.run as gateway_run

    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"display": {"platforms": {"telegram": {"subagent_roster": "on"}}}},
    )

    adapter = AsyncRosterAdapter()
    runner = _runner(adapter)

    final_evt = _record(status="completed")
    for c in final_evt["children"]:
        c["status"] = "completed"
        c["duration_seconds"] = 6.0

    # No _tick first — straight to finalize.
    await runner._finalize_async_delegation_roster(final_evt, [])

    # Exactly one SEND (the collapsed seed), no edits, bubble popped.
    assert len(adapter.sent) == 1
    assert "🤖 2 subagents · 2 ✓" in adapter.sent[0]["content"]
    assert adapter.edits == []
    assert "deleg_bg" not in runner._async_subagent_roster_bubbles


@pytest.mark.asyncio
async def test_watcher_roster_collapses_interrupted_batch_with_failed_counts(monkeypatch):
    """C5: an interrupted/failed batch completion event must still collapse the
    bubble (with ✗ counts) and pop it from the registry.
    """
    import gateway.run as gateway_run

    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"display": {"platforms": {"telegram": {"subagent_roster": "on"}}}},
    )

    adapter = AsyncRosterAdapter()
    runner = _runner(adapter)

    # Seed a live bubble first.
    await runner._tick_async_delegation_rosters(
        [_record()],
        [{"subagent_id": "sa-0", "started_at": 101.0, "tool_count": 0}],
    )
    assert len(adapter.sent) == 1

    interrupted = _record(status="interrupted")
    interrupted["children"][0]["status"] = "completed"
    interrupted["children"][0]["duration_seconds"] = 6.0
    interrupted["children"][1]["status"] = "interrupted"
    interrupted["children"][1]["duration_seconds"] = 3.0

    await runner._finalize_async_delegation_roster(interrupted, [])

    last = adapter.edits[-1]["content"] if adapter.edits else adapter.sent[-1]["content"]
    assert "1 ✓" in last
    assert "1 ✗" in last
    assert "deleg_bg" not in runner._async_subagent_roster_bubbles
