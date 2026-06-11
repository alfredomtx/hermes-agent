from gateway.run import _format_delegate_task_args_progress


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
    assert card.startswith("🔀 delegate_task parameters")
    assert '"goal": "Review API handlers"' in card
    assert '"context": "Use the project at /tmp/app and report exact files read."' in card
    assert '"toolsets": [' in card
    assert '"profile": "file-explorer"' in card
    assert "summary" not in card
    assert "tool_trace" not in card


def test_delegate_task_args_progress_chunks_large_context():
    cards = _format_delegate_task_args_progress(
        {
            "goal": "Audit a large area",
            "context": "x" * 1200,
            "toolsets": ["file"],
        },
        chunk_size=300,
    )

    assert len(cards) > 1
    assert cards[0].startswith("🔀 delegate_task parameters (1/")
    assert cards[-1].startswith(f"🔀 delegate_task parameters ({len(cards)}/{len(cards)})")
    assert all(len(card) < 450 for card in cards)
