from gateway.stream_dispatch import GatewayEventDispatcher
from gateway.stream_events import ToolCallChunk
from gateway.todo_progress import format_todo_progress


TODO_ARGS = {
    "todos": [
        {"id": "inspect", "content": "Inspect gateway progress rendering", "status": "pending"},
        {"id": "implement", "content": "Add compact todo renderer", "status": "in_progress"},
        {"id": "test", "content": "Run regression tests", "status": "completed"},
        {"id": "drop", "content": "Skip obsolete path", "status": "cancelled"},
    ]
}


def _base_adapter():
    from gateway.platforms.base import BasePlatformAdapter

    Concrete = type("Concrete", (BasePlatformAdapter,), {})
    Concrete.__abstractmethods__ = frozenset()  # type: ignore[attr-defined]
    return Concrete.__new__(Concrete)


class _FakeSink:
    def on_delta(self, text):
        pass

    def on_commentary(self, text):
        pass

    def on_segment_break(self):
        pass


def test_todo_progress_renders_plan_items_as_bullets():
    card = format_todo_progress(TODO_ARGS)
    assert card is not None

    assert card.startswith("📋 Plan (4 tasks)")
    assert "1. ⏳ pending - Inspect gateway progress rendering" in card
    assert "2. 🔄 in progress - Add compact todo renderer" in card
    assert "3. ✅ completed - Run regression tests" in card
    assert "4. ✗ cancelled - Skip obsolete path" in card


def test_todo_progress_renders_merge_as_update():
    card = format_todo_progress(
        {
            "merge": True,
            "todos": [
                {"id": "implement", "content": "Add renderer", "status": "completed"},
            ],
        }
    )
    assert card is not None

    assert card.startswith("📋 Plan update (1 task)")
    assert "✅ completed - Add renderer" in card


def test_todo_progress_limits_large_lists_and_long_content():
    card = format_todo_progress(
        {
            "todos": [
                {"id": str(i), "content": "x" * 200, "status": "pending"}
                for i in range(14)
            ]
        },
        max_items=3,
        content_limit=12,
    )
    assert card is not None

    assert "1. ⏳ pending - xxxxxxxxx..." in card
    assert "3. ⏳ pending - xxxxxxxxx..." in card
    assert "... 11 more" in card
    assert "4. ⏳" not in card


def test_todo_progress_reading_state():
    assert format_todo_progress({}) == "📋 Todo\nReading task list"


def test_todo_progress_renders_durations_from_result():
    result = {
        "todos": [
            {"id": "a", "content": "build core", "status": "completed", "elapsed_seconds": 134.0},
            {"id": "b", "content": "write tests", "status": "in_progress", "elapsed_seconds": 5.0},
            {"id": "c", "content": "review", "status": "pending", "elapsed_seconds": None},
        ]
    }
    card = format_todo_progress({"merge": True}, result=result)
    assert card is not None
    # Per-item wall-clock durations appear; pending (unmeasured) shows none.
    assert "1. ✅ completed (2m 14s) - build core" in card
    assert "2. 🔄 in progress (5.0s) - write tests" in card
    assert "3. ⏳ pending - review" in card
    assert "(" not in card.splitlines()[3]  # pending row has no duration paren


def test_todo_progress_result_supersedes_args():
    # Args (tool-start) carry no timing; result (completion) does and wins.
    args = {"todos": [{"id": "a", "content": "stale", "status": "pending"}]}
    result = {"todos": [{"id": "a", "content": "fresh", "status": "completed", "elapsed_seconds": 1.5}]}
    card = format_todo_progress(args, result=result)
    assert card is not None
    assert "fresh" in card and "stale" not in card
    assert "(1.5s)" in card


def test_todo_progress_result_as_json_string():
    # Gateway passes the raw tool result, which is a JSON string.
    import json as _json
    result = _json.dumps({"todos": [
        {"id": "a", "content": "task", "status": "completed", "elapsed_seconds": 0.05},
    ]})
    card = format_todo_progress({"merge": False}, result=result)
    assert card is not None
    assert "✅ completed (50ms) - task" in card


def test_base_adapter_uses_todo_progress_renderer():
    lines = []
    dispatcher = GatewayEventDispatcher(
        _base_adapter(),
        _FakeSink(),
        enqueue_tool_line=lines.append,
        tool_mode="all",
    )

    dispatcher.dispatch(
        ToolCallChunk(
            tool_name="todo",
            preview="planning 4 task(s)",
            args=TODO_ARGS,
        )
    )

    assert len(lines) == 1
    assert lines[0].startswith("📋 Plan (4 tasks)")
    assert "planning 4 task(s)" not in lines[0]
    assert "Inspect gateway progress rendering" in lines[0]


# ── header whole-plan wall-clock (DECISION B) ───────────────────────────────
def test_header_wall_clock_appears_with_start_end_stamps():
    # When items carry raw started_at/ended_at stamps (the result payload), the
    # header gets a "· <wall>" suffix = earliest start -> latest end.
    result = {
        "todos": [
            {"id": "a", "content": "first", "status": "completed",
             "started_at": 1000.0, "ended_at": 1030.0, "elapsed_seconds": 30.0},
            {"id": "b", "content": "second", "status": "completed",
             "started_at": 1020.0, "ended_at": 1090.0, "elapsed_seconds": 70.0},
        ]
    }
    card = format_todo_progress({"merge": False}, result=result)
    assert card is not None
    # wall-clock = 1090 - 1000 = 90s = "1m 30s"
    assert card.startswith("📋 Plan (2 tasks) · 1m 30s")


def test_header_wall_clock_absent_without_stamps():
    # Existing fixtures (and the model's start args) carry elapsed_seconds but
    # NO started_at/ended_at -> no suffix -> back-compat assertions stay valid.
    result = {
        "todos": [
            {"id": "a", "content": "x", "status": "completed", "elapsed_seconds": 30.0},
        ]
    }
    card = format_todo_progress({"merge": False}, result=result)
    assert card is not None
    assert card.startswith("📋 Plan (1 task)")
    assert " · " not in card.splitlines()[0]


def test_header_wall_clock_uses_now_for_in_progress():
    # An in_progress item (started_at, no ended_at) counts up to now.
    import gateway.todo_progress as tp
    items = [
        {"id": "a", "content": "running", "status": "in_progress",
         "started_at": 1000.0, "elapsed_seconds": 45.0},
    ]
    # _plan_wall_clock_seconds imports time lazily; pass now explicitly.
    wall = tp._plan_wall_clock_seconds(items, now=1045.0)
    assert wall == 45.0


def test_header_wall_clock_none_when_no_started_at():
    import gateway.todo_progress as tp
    items = [{"id": "a", "content": "x", "status": "pending", "elapsed_seconds": None}]
    assert tp._plan_wall_clock_seconds(items, now=1045.0) is None

