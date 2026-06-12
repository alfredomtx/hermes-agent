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
