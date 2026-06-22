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

    assert text == "🤖 2 subagents · 1 ✓ · 1 ✗ · 0:10"
