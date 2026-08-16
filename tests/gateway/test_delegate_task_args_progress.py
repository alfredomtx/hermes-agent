from gateway.run import (
    _DELEGATE_TASK_GOAL_PREVIEW_CHARS,
    _format_delegate_task_args_progress,
    _tool_progress_pipeline_enabled,
)


def _lines(cards):
    """A single card is always returned; split it into header + rows."""
    assert len(cards) == 1
    return cards[0].split("\n")


def test_delegate_task_args_keeps_pipeline_alive_when_tool_progress_off():
    assert _tool_progress_pipeline_enabled(
        is_webhook=False,
        progress_mode="off",
        tool_completion_durations_enabled=False,
        subagent_progress_enabled=False,
        delegate_task_args_enabled=True,
    ) is True


def test_pipeline_stays_off_when_everything_disabled():
    assert _tool_progress_pipeline_enabled(
        is_webhook=False,
        progress_mode="off",
        tool_completion_durations_enabled=False,
        subagent_progress_enabled=False,
        delegate_task_args_enabled=False,
    ) is False


def test_webhook_never_gets_pipeline_even_with_delegate_args():
    assert _tool_progress_pipeline_enabled(
        is_webhook=True,
        progress_mode="off",
        tool_completion_durations_enabled=False,
        subagent_progress_enabled=False,
        delegate_task_args_enabled=True,
    ) is False


def test_single_task_row_is_just_the_backticked_goal():
    cards = _format_delegate_task_args_progress(
        {
            "goal": "Review API handlers",
            "context": "Use the project at /tmp/app and report exact files read.",
        }
    )

    lines = _lines(cards)
    assert lines[0] == "🔀 Delegate task"
    assert lines[1] == "`Review API handlers`"
    assert "context" not in cards[0]
    assert "/tmp/app" not in cards[0]


def test_extra_params_render_before_the_goal():
    cards = _format_delegate_task_args_progress(
        {
            "goal": "Second opinion on the sharding plan",
            "role": "orchestrator",
            "toolsets": ["terminal", "file"],
        }
    )

    assert _lines(cards)[1] == (
        "role=orchestrator · toolsets=terminal,file "
        "· `Second opinion on the sharding plan`"
    )


def test_goal_truncated_to_120_chars_with_ellipsis():
    long_goal = "g" * 200
    cards = _format_delegate_task_args_progress({"goal": long_goal})

    row = _lines(cards)[1]
    cap = _DELEGATE_TASK_GOAL_PREVIEW_CHARS
    assert cap == 120
    expected_goal = "g" * (cap - 1) + "…"
    assert row == f"`{expected_goal}`"
    assert ("g" * cap) not in row


def test_batch_tasks_render_one_generic_row_each():
    cards = _format_delegate_task_args_progress(
        {
            "tasks": [
                {"goal": "Review auth refactor"},
                {"goal": "Audit the migration"},
            ],
        }
    )

    lines = _lines(cards)
    assert lines[0] == "🔀 Delegate task — 2 tasks"
    assert lines[1:] == ["`Review auth refactor`", "`Audit the migration`"]


def test_batch_truncates_to_max_rows_with_more_line():
    tasks = [{"goal": f"task {i}"} for i in range(13)]
    lines = _lines(_format_delegate_task_args_progress({"tasks": tasks}))

    assert lines[0] == "🔀 Delegate task — 13 tasks"
    assert len(lines) == 12
    assert lines[-1] == "… +3 more"


def test_missing_goal_renders_placeholder_and_no_crash():
    assert _lines(_format_delegate_task_args_progress({}))[1] == "`goal`"


def test_backticks_in_goal_are_stripped():
    cards = _format_delegate_task_args_progress({"goal": "fix `foo` bug"})
    assert _lines(cards)[1] == "`fix foo bug`"


def test_backticks_in_params_are_stripped():
    cards = _format_delegate_task_args_progress(
        {
            "goal": "review diff",
            "role": "orch`estrator",
            "toolsets": ["ter`minal", "file"],
        }
    )
    row = _lines(cards)[1]
    assert row.count("`") == 2
    assert row == "role=orchestrator · toolsets=terminal,file · `review diff`"


def test_empty_args_does_not_crash():
    lines = _lines(_format_delegate_task_args_progress(None))
    assert lines[0] == "🔀 Delegate task"
    assert lines[1] == "`goal`"
