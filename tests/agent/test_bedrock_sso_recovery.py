"""Regression tests for the bounded Bedrock AWS SSO repair path."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import agent.agent_runtime_helpers as runtime_helpers


class TokenRetrievalError(Exception):
    def __init__(
        self,
        message="Error when retrieving token from sso: Token has expired and refresh failed",
    ):
        super().__init__(message)


def _agent():
    visible_statuses = []
    buffered_statuses = []
    agent = SimpleNamespace(
        provider="bedrock",
        api_mode="anthropic_messages",
        _bedrock_region="us-east-1",
        _buffer_status=buffered_statuses.append,
        _emit_status=visible_statuses.append,
        visible_statuses=visible_statuses,
        buffered_statuses=buffered_statuses,
    )
    return agent


def setup_function():
    runtime_helpers._AWS_SSO_LOGIN_COOLDOWNS.clear()


def teardown_function():
    runtime_helpers._AWS_SSO_LOGIN_COOLDOWNS.clear()


def test_successful_login_uses_active_profile_for_anthropic_bedrock(monkeypatch):
    agent = _agent()
    calls = []
    invalidated = []

    monkeypatch.setenv("AWS_PROFILE", "as24-bedrock-readonly-cron")
    monkeypatch.setattr(
        runtime_helpers.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        "agent.bedrock_adapter.invalidate_runtime_client",
        lambda region: invalidated.append(region),
    )

    assert runtime_helpers.try_repair_bedrock_sso(agent, TokenRetrievalError()) is True
    assert calls[0][0][0] == [
        "aws",
        "sso",
        "login",
        "--profile",
        "as24-bedrock-readonly-cron",
    ]
    assert (
        calls[0][1]["timeout"]
        == runtime_helpers._AWS_SSO_LOGIN_TIMEOUT_SECONDS
    )
    assert invalidated == []
    assert any("retrying Bedrock primary" in message for message in agent.visible_statuses)


def test_successful_login_invalidates_native_bedrock_client(monkeypatch):
    agent = _agent()
    agent.api_mode = "bedrock_converse"
    invalidated = []

    monkeypatch.setenv("AWS_PROFILE", "as24-bedrock-readonly-cron")
    monkeypatch.setattr(
        runtime_helpers.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        "agent.bedrock_adapter.invalidate_runtime_client",
        invalidated.append,
    )

    assert runtime_helpers.try_repair_bedrock_sso(agent, TokenRetrievalError()) is True
    assert invalidated == ["us-east-1"]


def test_failed_login_is_bounded_by_profile_cooldown(monkeypatch):
    agent = _agent()
    calls = []
    monkeypatch.setenv("AWS_PROFILE", "as24-bedrock-readonly-cron")
    monkeypatch.setattr(
        runtime_helpers.subprocess,
        "run",
        lambda *args, **kwargs: calls.append(args) or SimpleNamespace(returncode=1),
    )

    assert runtime_helpers.try_repair_bedrock_sso(agent, TokenRetrievalError()) is False
    assert runtime_helpers.try_repair_bedrock_sso(agent, TokenRetrievalError()) is False
    assert len(calls) == 1
    assert any("login failed" in message for message in agent.visible_statuses)


def test_login_timeout_is_failure(monkeypatch):
    agent = _agent()
    calls = []
    monkeypatch.setenv("AWS_PROFILE", "as24-bedrock-readonly-cron")

    def timeout(*args, **kwargs):
        calls.append((args, kwargs))
        raise runtime_helpers.subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(runtime_helpers.subprocess, "run", timeout)

    assert runtime_helpers.try_repair_bedrock_sso(agent, TokenRetrievalError()) is False
    assert len(calls) == 1
    assert any("timed out" in message for message in agent.visible_statuses)


def test_non_bedrock_auth_does_not_launch_login(monkeypatch):
    agent = _agent()
    agent.provider = "anthropic"
    calls = []
    monkeypatch.setenv("AWS_PROFILE", "as24-bedrock-readonly-cron")
    monkeypatch.setattr(
        runtime_helpers.subprocess,
        "run",
        lambda *args, **kwargs: calls.append(args),
    )

    assert runtime_helpers.try_repair_bedrock_sso(agent, TokenRetrievalError()) is False
    assert calls == []


def test_non_sso_bedrock_auth_does_not_launch_login(monkeypatch):
    agent = _agent()
    calls = []
    monkeypatch.setenv("AWS_PROFILE", "as24-bedrock-readonly-cron")
    monkeypatch.setattr(
        runtime_helpers.subprocess,
        "run",
        lambda *args, **kwargs: calls.append(args),
    )

    assert runtime_helpers.try_repair_bedrock_sso(
        agent, Exception("Access denied")
    ) is False
    assert calls == []


def test_concurrent_repairs_share_one_login_attempt(monkeypatch):
    agent = _agent()
    calls = []
    started = threading.Event()
    monkeypatch.setenv("AWS_PROFILE", "as24-bedrock-readonly-cron")

    def fake_run(*args, **kwargs):
        calls.append(args)
        started.set()
        time.sleep(0.03)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runtime_helpers.subprocess, "run", fake_run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                runtime_helpers.try_repair_bedrock_sso,
                agent,
                TokenRetrievalError(),
            )
            for _ in range(2)
        ]
        assert [future.result() for future in futures] == [True, True]

    assert started.is_set()
    assert len(calls) == 1


def _anthropic_response(text="Recovered on Bedrock"):
    return SimpleNamespace(
        id="msg_test",
        model="us.anthropic.claude-opus-5",
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )


def _openai_response(text="Recovered on fallback"):
    return SimpleNamespace(
        id="chatcmpl_test",
        model="gpt-5.6-sol",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _conversation_agent():
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.provider = "bedrock"
    agent.model = "us.anthropic.claude-opus-5"
    agent.api_mode = "anthropic_messages"
    agent._bedrock_region = "us-east-1"
    agent._disable_streaming = True
    agent.client = MagicMock()
    agent._anthropic_client = MagicMock()
    agent._fallback_chain = [
        {"provider": "openai-codex", "model": "gpt-5.6-sol"}
    ]
    agent._fallback_index = 0
    agent._persist_session = lambda *args, **kwargs: None
    agent._save_trajectory = lambda *args, **kwargs: None
    agent._cleanup_task_resources = lambda *args, **kwargs: None
    return agent


def _activate_test_fallback(agent, events):
    def activate(reason=None):
        events.append("fallback")
        agent._fallback_index = 1
        agent.provider = "openai-codex"
        agent.model = "gpt-5.6-sol"
        agent.api_mode = "chat_completions"
        agent.base_url = "https://api.openai.com/v1"
        return True

    return activate


def test_successful_login_retries_primary_before_fallback(monkeypatch):
    agent = _conversation_agent()
    events = []
    outcomes = [TokenRetrievalError(), _anthropic_response()]

    def api_call(_api_kwargs):
        events.append(f"api:{agent.provider}")
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def repair(*_args):
        events.append("repair")
        return True

    agent._interruptible_api_call = api_call
    agent._try_activate_fallback = _activate_test_fallback(agent, events)
    monkeypatch.setattr(runtime_helpers, "try_repair_bedrock_sso", repair)

    result = agent.run_conversation("hello")

    assert result["completed"] is True
    assert result["final_response"] == "Recovered on Bedrock"
    assert events == ["api:bedrock", "repair", "api:bedrock"]


def test_failed_login_continues_to_configured_fallback(monkeypatch):
    agent = _conversation_agent()
    events = []
    outcomes = [TokenRetrievalError(), _openai_response()]

    def api_call(_api_kwargs):
        events.append(f"api:{agent.provider}")
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def repair(*_args):
        events.append("repair")
        return False

    agent._interruptible_api_call = api_call
    agent._try_activate_fallback = _activate_test_fallback(agent, events)
    monkeypatch.setattr(runtime_helpers, "try_repair_bedrock_sso", repair)

    result = agent.run_conversation("hello")

    assert result["completed"] is True
    assert result["final_response"] == "Recovered on fallback"
    assert events == ["api:bedrock", "repair", "fallback", "api:openai-codex"]


def test_failed_primary_retry_falls_back_without_second_login(monkeypatch):
    agent = _conversation_agent()
    events = []
    outcomes = [TokenRetrievalError(), TokenRetrievalError(), _openai_response()]

    def api_call(_api_kwargs):
        events.append(f"api:{agent.provider}")
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def repair(*_args):
        events.append("repair")
        return True

    agent._interruptible_api_call = api_call
    agent._try_activate_fallback = _activate_test_fallback(agent, events)
    monkeypatch.setattr(runtime_helpers, "try_repair_bedrock_sso", repair)

    result = agent.run_conversation("hello")

    assert result["completed"] is True
    assert result["final_response"] == "Recovered on fallback"
    assert events == [
        "api:bedrock",
        "repair",
        "api:bedrock",
        "fallback",
        "api:openai-codex",
    ]
