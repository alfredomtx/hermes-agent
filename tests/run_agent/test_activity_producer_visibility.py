import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent


SECRET = "secret-value"


def _make_tool_defs(*names: str) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


def _tool_call(name: str, command: str, call_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps({"command": command}),
        ),
    )


def _assistant_message(*tool_calls: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(content="", tool_calls=list(tool_calls))


@pytest.fixture()
def activity_agent():
    """A real, narrowly initialized AIAgent for executor-boundary tests."""
    with (
        patch(
            "run_agent.get_tool_definitions",
            return_value=_make_tool_defs("terminal"),
        ),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
        patch("hermes_logging.setup_logging"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="http://127.0.0.1:1/v1",
            provider="openai",
            model="gpt-4o-mini",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    return agent


def _assert_safe_preview(preview: object) -> None:
    assert isinstance(preview, str) and preview
    assert SECRET not in preview
    lowered = preview.lower()
    assert any(
        marker in lowered
        for marker in ("redact", "terminal", "command", "hidden", "safe")
    )


def _assert_executor_finished(thread: threading.Thread, errors: list[BaseException]) -> None:
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not errors


def test_sequential_executor_publishes_safe_live_and_completed_activity(
    activity_agent,
):
    started = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def blocking_tool(_name, _args, _task_id, **_kwargs):
        started.set()
        release.wait(timeout=2)
        return "completed"

    message = _assistant_message(
        _tool_call("terminal", f"deploy --password {SECRET}", "sequential-1")
    )
    messages = []

    def execute():
        try:
            activity_agent._execute_tool_calls_sequential(message, messages, "task-1")
        except BaseException as exc:  # surface fixture/executor failures after cleanup
            errors.append(exc)

    worker = threading.Thread(target=execute)
    with patch("run_agent.handle_function_call", side_effect=blocking_tool):
        worker.start()
        try:
            assert started.wait(timeout=2)

            live = AIAgent.get_activity_summary(activity_agent)

            assert live["current_tool"] == "terminal"
            _assert_safe_preview(live["current_tool_preview"])
            assert SECRET not in repr(live["recent_tool_activity"])
            assert live["current_tool_elapsed"] is not None
            assert live["current_tool_elapsed"] >= 0
        finally:
            release.set()
            _assert_executor_finished(worker, errors)

    completed = AIAgent.get_activity_summary(activity_agent)

    assert completed["current_tool"] is None
    assert completed["current_tool_preview"] is None
    assert completed["current_tool_elapsed"] is None
    assert completed["last_completed_tool"]["name"] == "terminal"
    assert completed["last_completed_tool"]["duration"] >= 0
    assert completed["last_completed_tool"]["is_error"] is False
    done_entries = [
        item for item in completed["recent_tool_activity"] if item.get("state") == "done"
    ]
    assert done_entries
    assert any(
        item.get("name") == "terminal" or item.get("label") == "terminal"
        for item in done_entries
    )
    assert len(completed["recent_tool_activity"]) <= 3
    assert SECRET not in repr(completed["recent_tool_activity"])
    assert messages[0]["role"] == "tool"


def test_concurrent_executor_publishes_one_bounded_safe_activity_snapshot(
    activity_agent,
):
    started_count = 0
    started_lock = threading.Lock()
    all_started = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def blocking_tool(_name, _args, _task_id, **_kwargs):
        nonlocal started_count
        with started_lock:
            started_count += 1
            if started_count == 2:
                all_started.set()
        release.wait(timeout=2)
        return "completed"

    message = _assistant_message(
        _tool_call("terminal", f"first --password {SECRET}", "concurrent-1"),
        _tool_call("terminal", f"second --password {SECRET}", "concurrent-2"),
    )
    messages = []

    def execute():
        try:
            activity_agent._execute_tool_calls_concurrent(message, messages, "task-1")
        except BaseException as exc:  # surface fixture/executor failures after cleanup
            errors.append(exc)

    worker = threading.Thread(target=execute)
    with patch("run_agent.handle_function_call", side_effect=blocking_tool):
        worker.start()
        try:
            assert all_started.wait(timeout=2)
            live = AIAgent.get_activity_summary(activity_agent)

            assert live["current_tool"]
            _assert_safe_preview(live["current_tool_preview"])
            assert len(live["current_tool_preview"]) <= 200
            assert SECRET not in repr(live["recent_tool_activity"])
        finally:
            release.set()
            _assert_executor_finished(worker, errors)

    completed = AIAgent.get_activity_summary(activity_agent)

    assert completed["current_tool"] is None
    assert completed["current_tool_preview"] is None
    assert completed["current_tool_elapsed"] is None
    assert completed["last_completed_tool"]["name"] == "terminal"
    assert completed["last_completed_tool"]["duration"] >= 0
    assert completed["last_completed_tool"]["is_error"] is False
    assert any(
        item.get("state") == "done"
        and (item.get("name") == "terminal" or item.get("label") == "terminal")
        for item in completed["recent_tool_activity"]
    )
    assert len(completed["recent_tool_activity"]) <= 3
    assert SECRET not in repr(completed["recent_tool_activity"])
    assert len(messages) == 2
    assert all(message["role"] == "tool" for message in messages)


def test_sequential_executor_closes_activity_before_post_processing_failure(
    activity_agent,
):
    message = _assistant_message(
        _tool_call("terminal", "deploy", "post-processing-1")
    )

    with (
        patch("run_agent.handle_function_call", return_value="completed"),
        patch.object(
            activity_agent,
            "_append_guardrail_observation",
            side_effect=RuntimeError("post-processing failed"),
        ),
        pytest.raises(RuntimeError, match="post-processing failed"),
    ):
        activity_agent._execute_tool_calls_sequential(message, [], "task-1")

    completed = AIAgent.get_activity_summary(activity_agent)
    assert completed["current_tool"] is None
    assert completed["current_tool_preview"] is None
    assert completed["current_tool_elapsed"] is None
    assert completed["last_completed_tool"]["name"] == "terminal"
