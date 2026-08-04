"""RED contract for the core seam shared by native and workflow children."""

from __future__ import annotations

import builtins
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

from agent import child_execution
from agent.child_execution import (
    _request_overrides_for_child,
    create_child,
    inherit_parent_base_url,
    resolve_child_credentials,
    resolve_child_route,
    run_child,
)


class _FakeAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__dict__.update(kwargs)
        self.model = kwargs.get("model")
        self.provider = kwargs.get("provider")
        self.session_id = kwargs.get("session_id", "constructed-child")


class _RecordingChild:
    def __init__(self, raw_result):
        self.raw_result = raw_result
        self.calls = []
        self.model = "routed-model"
        self.provider = "openrouter"
        self.session_id = "child-session"
        self.session_prompt_tokens = 11
        self.session_completion_tokens = 7
        self.session_reasoning_tokens = 3
        self.session_estimated_cost_usd = 0.02

    def run_conversation(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.raw_result

    def get_activity_summary(self):
        return {"api_call_count": 1, "max_iterations": 5, "current_tool": None}


def _parent(session_db=None):
    return SimpleNamespace(
        base_url="https://parent.example.test/v1",
        api_key="parent-key",
        provider="parent-provider",
        api_mode="chat_completions",
        model="parent-model",
        platform="cli",
        session_id="parent-session",
        _session_db=session_db,
        _delegate_depth=0,
        _subagent_id=None,
        _active_children=[],
        _active_children_lock=threading.Lock(),
        _print_fn=None,
        tool_progress_callback=None,
        thinking_callback=None,
        providers_allowed=None,
        providers_ignored=None,
        providers_order=None,
        provider_sort=None,
        provider_require_parameters=False,
        provider_data_collection=None,
        request_overrides={},
        enabled_toolsets=["file", "terminal"],
        disabled_toolsets=[],
        _current_task_id="parent-task",
        _current_turn_id="parent-turn",
        session_estimated_cost_usd=0.0,
        session_cost_source="none",
        session_cost_status="unknown",
        _touch_activity=lambda _description: None,
    )


def test_create_child_resolves_native_profile_and_preserves_generic_overrides(
    monkeypatch, tmp_path: Path
):
    """A profile selector must use native routing while retaining core context."""
    session_db = object()
    parent = _parent(session_db)
    profile = {
        "provider": "openrouter",
        "model": "google/gemini-3-flash-preview",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "profile-key",
        "api_mode": "chat_completions",
        "toolsets": ["file"],
    }
    monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)

    progress = lambda *_args, **_kwargs: None
    clarify = lambda *_args, **_kwargs: "continue"
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    child = create_child(
        parent,
        {
            "profile": "reviewer",
            "instructions": "Inspect only the requested repository.",
            "toolsets": ["file"],
            "session_id": "workflow-child",
        },
        callbacks={"progress": progress, "clarify": clarify},
        context={
            "delegation_cfg": {"profiles": {"reviewer": profile}},
            "session_db": session_db,
            "cwd": str(cwd),
        },
    )

    assert child.provider == "custom"
    assert child.model == "google/gemini-3-flash-preview"
    assert child.kwargs["enabled_toolsets"] == ["file"]
    assert child.kwargs["ephemeral_system_prompt"] == (
        "Inspect only the requested repository."
    )
    assert child.kwargs["session_db"] is session_db
    assert child.kwargs["parent_session_id"] == parent.session_id
    assert child.kwargs["session_id"] == "workflow-child"
    assert child.kwargs["tool_progress_callback"] is progress
    assert child.kwargs["clarify_callback"] is clarify
    assert child.cwd == str(cwd)


def test_resolve_child_route_prefers_explicit_context_config(monkeypatch):
    parent = _parent()
    explicit_cfg = {
        "profiles": {
            "reviewer": {
                "provider": "context-provider",
                "model": "context-model",
            }
        }
    }
    monkeypatch.setattr(
        child_execution,
        "load_child_config",
        lambda: {"profiles": {"reviewer": {"model": "loader-model"}}},
    )

    route, _credentials = resolve_child_route(
        parent,
        {
            "profile": "reviewer",
            "resolved_credentials": {"model": "resolved-model"},
        },
        {"delegation_cfg": explicit_cfg},
    )

    assert route["provider"] == "context-provider"
    assert route["model"] == "context-model"


def test_resolve_child_route_loads_profile_config_when_context_omits_it(monkeypatch):
    parent = _parent()
    monkeypatch.setattr(
        child_execution,
        "load_child_config",
        lambda: {
            "profiles": {
                "reviewer": {
                    "provider": "loader-provider",
                    "model": "loader-model",
                }
            }
        },
    )

    route, _credentials = resolve_child_route(
        parent,
        {
            "profile": "reviewer",
            "resolved_credentials": {"model": "resolved-model"},
        },
        {},
    )

    assert route["provider"] == "loader-provider"
    assert route["model"] == "loader-model"


def test_create_child_does_not_depend_on_the_delegate_tool_adapter(monkeypatch):
    """The reusable core seam must work without importing native delegation."""
    parent = _parent()
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        imports_delegate_adapter = name == "tools.delegate_tool" or (
            name == "tools" and "delegate_tool" in fromlist
        )
        if imports_delegate_adapter:
            raise AssertionError("core child execution imported the delegate adapter")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)

    child = create_child(
        parent,
        {
            "provider": "openrouter",
            "model": "google/gemini-3-flash-preview",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "profile-key",
            "toolsets": [],
        },
    )

    assert child.provider == "custom"
    assert child.model == "google/gemini-3-flash-preview"
    assert child.kwargs["enabled_toolsets"] == []


def test_run_child_preserves_raw_result_fields_and_adds_terminal_metadata():
    raw = {
        "final_response": "finished",
        "usage": {"input_tokens": 11, "output_tokens": 7},
        "session_id": "child-session",
        "model": "routed-model",
        "provider": "openrouter",
    }
    child = _RecordingChild(raw)

    result = run_child(child, "run the child once", task_id="child-task")

    assert result["final_response"] == raw["final_response"]
    assert result["usage"] == raw["usage"]
    assert result["session_id"] == raw["session_id"]
    assert result["model"] == raw["model"]
    assert result["provider"] == raw["provider"]
    assert result["status"] == "completed"
    assert result["exit_reason"] == "completed"
    assert child.calls == [
        ((), {"user_message": "run the child once", "task_id": "child-task"})
    ]


def test_run_child_passes_exact_conversation_history_object():
    history = [{"role": "user", "content": "prior context"}]
    child = _RecordingChild({"final_response": "finished"})

    run_child(
        child,
        "continue the child",
        task_id="child-task",
        conversation_history=history,
    )

    passed_history = child.calls[0][1]["conversation_history"]
    assert passed_history is history


def test_run_child_timeout_interrupts_the_child():
    started = threading.Event()
    initialized = threading.Event()
    released = threading.Event()

    class BlockingChild(_RecordingChild):
        def run_conversation(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            started.set()
            released.wait()
            return {"final_response": "stopped"}

        def interrupt(self):
            self.interrupted = True
            released.set()

    child = BlockingChild({})
    child.interrupted = False

    result = run_child(
        child,
        "wait for cancellation",
        task_id="child-task",
        timeout=0.05,
        initializer=initialized.set,
    )

    assert initialized.is_set()
    assert started.is_set()
    assert child.interrupted is True
    assert result["status"] == "timeout"
    assert result["exit_reason"] == "timeout"


def test_run_child_propagates_child_failure():
    class FailingChild(_RecordingChild):
        def run_conversation(self, *args, **kwargs):
            raise RuntimeError("child failed")

    child = FailingChild({})

    try:
        run_child(child, "fail", task_id="child-task")
    except RuntimeError as exc:
        assert str(exc) == "child failed"
    else:
        raise AssertionError("child failure was swallowed")


def test_inherit_parent_base_url_preserves_case_sensitive_live_url_paths():
    fallback = "https://parent.example.test/v1/CaseSensitive"
    live_url = "https://parent.example.test/v1/casesensitive"

    client_parent = _parent()
    client_parent.client = SimpleNamespace(base_url=live_url)
    assert inherit_parent_base_url(client_parent, fallback) == live_url

    kwargs_parent = _parent()
    kwargs_parent._client_kwargs = {"base_url": live_url}
    assert inherit_parent_base_url(kwargs_parent, fallback) == live_url


def test_resolve_child_credentials_requires_exact_kimi_hostname_for_messages_mode(
    monkeypatch,
):
    monkeypatch.setattr(
        "hermes_cli.runtime_provider._detect_api_mode_for_url",
        lambda _url: "chat_completions",
    )
    parent = _parent()

    kimi = resolve_child_credentials(
        {"base_url": "https://api.kimi.com/coding"}, parent
    )
    spoofed = resolve_child_credentials(
        {"base_url": "https://api.kimi.com.evil.example/coding"}, parent
    )

    assert kimi["api_mode"] == "anthropic_messages"
    assert spoofed["api_mode"] == "chat_completions"


def test_unknown_service_tier_keeps_preserved_warning(caplog):
    caplog.set_level(logging.WARNING, logger="agent.child_execution")

    tier, overrides = _request_overrides_for_child(
        "parent-model", {"service_tier": "turbo"}, _parent()
    )

    assert tier is None
    assert overrides == {}
    assert "Unknown delegation service_tier 'turbo', ignoring" in caplog.text


def test_create_child_profile_only_reasoning_preserves_parent_route_and_filters(
    monkeypatch,
):
    parent = _parent()
    parent.providers_allowed = ["Anthropic"]
    parent.providers_ignored = ["DeepInfra"]
    parent.providers_order = ["Anthropic", "OpenAI"]
    parent.provider_sort = "throughput"
    parent.provider_require_parameters = True
    parent.provider_data_collection = "deny"
    parent.request_overrides = {"temperature": 0.2}
    parent.acp_command = "copilot"
    parent.acp_args = ["--stdio"]
    monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)

    child = create_child(
        parent,
        {
            "profile": "reasoning-review",
            "instructions": "Inspect only the requested repository.",
        },
        context={
            "delegation_cfg": {
                "profiles": {"reasoning-review": {"reasoning_effort": "high"}}
            }
        },
    )

    assert child.kwargs["provider"] == parent.provider
    assert child.kwargs["model"] == parent.model
    assert child.kwargs["base_url"] == parent.base_url
    assert child.kwargs["api_key"] == parent.api_key
    assert child.kwargs["api_mode"] == parent.api_mode
    assert child.kwargs["providers_allowed"] == parent.providers_allowed
    assert child.kwargs["providers_ignored"] == parent.providers_ignored
    assert child.kwargs["providers_order"] == parent.providers_order
    assert child.kwargs["provider_sort"] == parent.provider_sort
    assert child.kwargs["provider_require_parameters"] is True
    assert child.kwargs["provider_data_collection"] == parent.provider_data_collection
    assert child.kwargs["request_overrides"] == parent.request_overrides
    assert child.kwargs["acp_command"] == parent.acp_command
    assert child.kwargs["acp_args"] == parent.acp_args
    assert child.kwargs["reasoning_config"] == {"enabled": True, "effort": "high"}
    assert child.kwargs["ephemeral_system_prompt"] == (
        "Inspect only the requested repository."
    )


def test_native_delegate_task_uses_shared_child_seam(monkeypatch):
    """Native delegation must not retain an independent build/run path."""
    from agent import child_execution
    from tools import delegate_tool

    parent = _parent()
    child = _RecordingChild({"final_response": "native"})
    calls = {}

    def fake_create(parent_agent, spec, **kwargs):
        calls["create"] = (parent_agent, spec, kwargs)
        return child

    def fake_run(child_agent, prompt, task_id, **kwargs):
        calls["run"] = (child_agent, prompt, task_id, kwargs)
        return {
            "final_response": "native",
            "summary": "native",
            "status": "completed",
            "exit_reason": "completed",
            "completed": True,
            "interrupted": False,
            "api_calls": 1,
            "duration_seconds": 0.0,
        }

    monkeypatch.setattr(child_execution, "create_child", fake_create)
    monkeypatch.setattr(child_execution, "run_child", fake_run)
    monkeypatch.setattr(delegate_tool, "create_child", fake_create, raising=False)
    monkeypatch.setattr(delegate_tool, "run_child", fake_run, raising=False)

    monkeypatch.setattr(
        delegate_tool,
        "_load_config",
        lambda: {"max_iterations": 5, "profiles": {}},
    )
    monkeypatch.setattr(
        "tools.delegation_live_log.create_live_transcripts",
        lambda *_args, **_kwargs: ("delegation-id", [], []),
    )

    result = json.loads(
        delegate_tool.delegate_task(goal="use the shared path", parent_agent=parent)
    )

    assert result["results"][0]["summary"] == "native"
    assert calls["create"][0] is parent
    assert calls["run"][0] is child
    assert calls["run"][1] == "use the shared path"
