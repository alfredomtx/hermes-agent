from gateway.run import (
    _format_delegate_task_args_progress,
    _tool_progress_pipeline_enabled,
)


def _body_from_card(card: str) -> str:
    return card.split("```\n", 1)[1].rsplit("\n```", 1)[0]


def test_delegate_task_args_keeps_pipeline_alive_when_tool_progress_off():
    """The bug: tool_progress="off" tore down the progress queue, so the
    delegate_task_args card never rendered. The pipeline MUST stay alive when
    delegate_task_args is enabled even with every other progress source off."""
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


def test_delegate_task_args_progress_includes_only_call_parameters():
    cards = _format_delegate_task_args_progress(
        {
            "goal": "Review API handlers",
            "context": "Use the project at /tmp/app and report exact files read.",
            "toolsets": ["terminal", "file"],
            "profile": "file-explorer",
        }
    )

    assert len(cards) == 1
    card = cards[0]
    body = _body_from_card(card)
    assert card.startswith("🔀 delegate_task parameters")
    assert "\n" not in body
    assert '"goal":"Review API handlers"' in body
    assert '"context":"Use the project at /tmp/app and report exact files read."' in body
    assert '"toolsets":["terminal","file"]' in body
    assert '"profile":"file-explorer"' in body
    assert "summary" not in card
    assert "tool_trace" not in card


def test_delegate_task_args_progress_truncates_large_goal_and_context():
    long_goal = "g" * 200
    long_context = "c" * 1200

    cards = _format_delegate_task_args_progress(
        {
            "goal": long_goal,
            "context": long_context,
            "toolsets": ["file"],
        }
    )

    card = "\n".join(cards)
    body = _body_from_card(cards[0])
    assert len(cards) == 1
    assert "\n" not in body
    assert '"goal":"' + ("g" * 160) + '..."' in card
    assert '"context":"' + ("c" * 160) + '..."' in card
    assert "g" * 161 not in card
    assert "c" * 161 not in card
    assert all(len(card) < 450 for card in cards)


def test_delegate_task_args_progress_truncates_nested_task_goal_and_context():
    cards = _format_delegate_task_args_progress(
        {
            "tasks": [
                {
                    "goal": "nested-goal-" + ("g" * 200),
                    "context": "nested-context-" + ("c" * 200),
                    "toolsets": ["terminal"],
                }
            ],
            "profile": "dual-review",
        },
        chunk_size=500,
    )

    card = "\n".join(cards)
    body = _body_from_card(cards[0])
    assert "\n" not in body
    assert '"goal":"' + ("nested-goal-" + ("g" * 200))[:160] + '..."' in card
    assert '"context":"' + ("nested-context-" + ("c" * 200))[:160] + '..."' in card
    assert "g" * 170 not in card
    assert "c" * 170 not in card
    assert '"toolsets":["terminal"]' in card
