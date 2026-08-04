from __future__ import annotations

import shutil
import time
import logging
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Mapping, Optional

from utils import base_url_hostname


logger = logging.getLogger(__name__)


_ROUTE_KEYS = ("provider", "model", "base_url", "api_key", "api_mode", "request_overrides", "max_output_tokens", "command", "args")
_VALID_API_MODES = {"chat_completions", "codex_responses", "anthropic_messages"}
_RUNTIME_PROVIDER_CUSTOM = "custom"


def merge_child_route(
    cfg: Optional[Mapping[str, Any]],
    profile: Optional[str] = None,
    overrides: Optional[Mapping[str, Any]] = None,
) -> dict:
    source = dict(cfg or {})
    profiles = source.pop("profiles", None)
    route = dict(source)
    profile_name = str(profile or "").strip() or None
    if profile_name:
        if not isinstance(profiles, Mapping) or profile_name not in profiles:
            known = sorted(str(name) for name in profiles) if isinstance(profiles, Mapping) else []
            suffix = f" Known profiles: {', '.join(known)}." if known else " No profiles configured."
            raise ValueError(f"Unknown delegation profile '{profile_name}'.{suffix}")
        profile_cfg = profiles[profile_name]
        if not isinstance(profile_cfg, Mapping):
            raise ValueError(f"Delegation profile '{profile_name}' must be a mapping.")
        route.update({key: value for key, value in profile_cfg.items() if value is not None})
        route["_profile"] = profile_name
        route["_profile_child_timeout_overridden"] = (
            "child_timeout_seconds" in profile_cfg
            and profile_cfg.get("child_timeout_seconds") is not None
        )
        route["_global_child_timeout_seconds"] = source.get("child_timeout_seconds")

    for key, value in (overrides or {}).items():
        if key in _ROUTE_KEYS and value is not None:
            route[key] = value
    return route


def _normalized_runtime_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    return text


def inherit_parent_base_url(parent_agent: Any, fallback_base_url: Optional[str]) -> Optional[str]:
    surface_url = _normalized_runtime_url(fallback_base_url)
    client_kwargs = getattr(parent_agent, "_client_kwargs", None)
    if isinstance(client_kwargs, dict):
        kwargs_url = _normalized_runtime_url(client_kwargs.get("base_url"))
        if kwargs_url and kwargs_url != surface_url and kwargs_url.startswith(("http://", "https://")):
            return kwargs_url

    client = getattr(parent_agent, "client", None)
    if client is not None:
        live_url = _normalized_runtime_url(getattr(client, "base_url", ""))
        if live_url and live_url != surface_url and live_url.startswith(("http://", "https://")):
            return live_url
    return fallback_base_url or None


def _request_overrides_for_child(
    model: Optional[str], cfg: Mapping[str, Any], parent_agent: Any
) -> tuple[Optional[str], dict]:
    raw_tier = cfg.get("service_tier")
    if raw_tier in (None, ""):
        raw_tier = getattr(parent_agent, "service_tier", None)
    value = str(raw_tier or "").strip().lower()
    if not value or value in {"normal", "default", "standard", "off", "none"}:
        tier = None
    elif value in {"fast", "priority", "on"}:
        tier = "priority"
    else:
        logger.warning("Unknown delegation service_tier '%s', ignoring", raw_tier)
        tier = None

    overrides = dict(cfg.get("request_overrides") or {})
    if tier:
        try:
            from hermes_cli.models import resolve_fast_mode_overrides

            overrides.update(resolve_fast_mode_overrides(model) or {})
        except Exception:
            pass
    return tier, overrides


def resolve_child_credentials(cfg: Mapping[str, Any], parent_agent: Any) -> dict:
    configured_model = str(cfg.get("model") or "").strip() or None
    configured_provider = str(cfg.get("provider") or "").strip() or None
    configured_base_url = str(cfg.get("base_url") or "").strip() or None
    configured_api_key = str(cfg.get("api_key") or "").strip() or None
    configured_api_mode = str(cfg.get("api_mode") or "").strip().lower() or None

    native_sdk_providers = {"bedrock", "vertex", "google", "google-genai"}
    if configured_base_url and (configured_provider or "").lower() not in native_sdk_providers:
        from hermes_cli.runtime_provider import _detect_api_mode_for_url

        api_mode = _detect_api_mode_for_url(configured_base_url) or "chat_completions"
        base_lower = configured_base_url.lower()
        hostname = base_url_hostname(configured_base_url)
        provider = _RUNTIME_PROVIDER_CUSTOM
        if hostname == "chatgpt.com" and "/backend-api/codex" in base_lower:
            provider, api_mode = "openai-codex", "codex_responses"
        elif hostname == "api.anthropic.com":
            provider, api_mode = "anthropic", "anthropic_messages"
        elif hostname == "api.kimi.com" and "/coding" in base_lower:
            api_mode = "anthropic_messages"
        if configured_api_mode in _VALID_API_MODES:
            api_mode = configured_api_mode
        return {
            "model": configured_model,
            "provider": provider,
            "base_url": configured_base_url,
            "api_key": configured_api_key,
            "api_mode": api_mode,
        }

    if not configured_provider:
        return {
            "model": configured_model,
            "provider": None,
            "base_url": None,
            "api_key": None,
            "api_mode": None,
            "request_overrides": None,
            "max_output_tokens": None,
        }

    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(
            requested=configured_provider, target_model=configured_model
        )
    except Exception as exc:
        raise ValueError(
            f"Cannot resolve delegation provider '{configured_provider}': {exc}. "
            "Check that the provider is configured (API key set, valid provider name), "
            "or set delegation.base_url/delegation.api_key for a direct endpoint. "
            "Available providers: openrouter, nous, zai, kimi-coding, minimax."
        ) from exc

    api_key = runtime.get("api_key", "")
    if not api_key:
        raise ValueError(
            f"Delegation provider '{configured_provider}' resolved but has no API key. "
            "Set the appropriate environment variable or run 'hermes auth'."
        )
    return {
        "model": configured_model or runtime.get("model") or None,
        "provider": (
            configured_provider
            if runtime.get("provider") == _RUNTIME_PROVIDER_CUSTOM
            else runtime.get("provider")
        ),
        "base_url": runtime.get("base_url"),
        "api_key": api_key,
        "api_mode": runtime.get("api_mode"),
        "request_overrides": dict(runtime.get("request_overrides") or {}),
        "max_output_tokens": runtime.get("max_output_tokens"),
        "command": runtime.get("command"),
        "args": list(runtime.get("args") or []),
    }


def resolve_child_route(
    parent_agent: Any, spec: Mapping[str, Any], context: Mapping[str, Any]
) -> tuple[dict, dict]:
    route = merge_child_route(
        context.get("delegation_cfg"),
        spec.get("profile"),
        spec,
    )
    resolved = spec.get("resolved_credentials")
    credentials = (
        dict(resolved)
        if isinstance(resolved, Mapping)
        else resolve_child_credentials(route, parent_agent)
    )
    return route, credentials


def create_child(
    parent_agent: Any,
    spec: Mapping[str, Any],
    callbacks: Optional[Mapping[str, Any]] = None,
    context: Optional[Mapping[str, Any]] = None,
):
    if not isinstance(spec, Mapping):
        raise TypeError("child spec must be a mapping")
    if not isinstance(context or {}, Mapping):
        raise TypeError("child context must be a mapping")
    context = dict(context or {})
    callbacks = dict(callbacks or {})
    route_cfg, credentials = resolve_child_route(parent_agent, spec, context)

    model = credentials.get("model") or getattr(parent_agent, "model", None)
    provider = credentials.get("provider") or getattr(parent_agent, "provider", None)
    base_url = credentials.get("base_url") or getattr(parent_agent, "base_url", None)
    if not credentials.get("base_url"):
        base_url = inherit_parent_base_url(parent_agent, base_url)
    api_key = credentials.get("api_key")
    if not api_key:
        api_key = getattr(parent_agent, "api_key", None)
        if not api_key and hasattr(parent_agent, "_client_kwargs"):
            api_key = parent_agent._client_kwargs.get("api_key")

    parent_provider = getattr(parent_agent, "provider", None) or ""
    api_mode = credentials.get("api_mode")
    if api_mode is None and provider == parent_provider:
        api_mode = getattr(parent_agent, "api_mode", None)

    route_overridden = bool(
        spec.get("provider")
        or spec.get("base_url")
        or route_cfg.get("provider")
        or route_cfg.get("base_url")
    )
    provider_filters = {
        "providers_allowed": getattr(parent_agent, "providers_allowed", None),
        "providers_ignored": getattr(parent_agent, "providers_ignored", None),
        "providers_order": getattr(parent_agent, "providers_order", None),
        "provider_sort": getattr(parent_agent, "provider_sort", None),
        "provider_require_parameters": getattr(parent_agent, "provider_require_parameters", False),
        "provider_data_collection": getattr(parent_agent, "provider_data_collection", None) or "",
    }
    if route_overridden:
        provider_filters.update(
            {
                "providers_allowed": None,
                "providers_ignored": None,
                "providers_order": None,
                "provider_sort": None,
                "provider_require_parameters": False,
                "provider_data_collection": "",
            }
        )

    service_tier, profile_overrides = _request_overrides_for_child(
        model, route_cfg, parent_agent
    )
    request_overrides = (
        {}
        if route_overridden
        else dict(getattr(parent_agent, "request_overrides", {}) or {})
    )
    request_overrides.update(credentials.get("request_overrides") or {})
    request_overrides.update(spec.get("request_overrides") or {})
    request_overrides.update(profile_overrides)

    reasoning = spec.get(
        "reasoning_config",
        spec.get("reasoning", getattr(parent_agent, "reasoning_config", None)),
    )
    effort = spec.get("reasoning_effort", route_cfg.get("reasoning_effort"))
    if effort is not None:
        from hermes_constants import parse_reasoning_effort

        parsed = parse_reasoning_effort(effort)
        if parsed is not None:
            reasoning = parsed

    child_kwargs = {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "provider": provider,
        "api_mode": api_mode,
        "max_iterations": context.get(
            "max_iterations", getattr(parent_agent, "max_iterations", 90)
        ),
        "reasoning_config": reasoning,
        "service_tier": service_tier,
        "prefill_messages": context.get(
            "prefill_messages", getattr(parent_agent, "prefill_messages", None)
        ),
        "fallback_model": getattr(parent_agent, "_fallback_chain", None) or None,
        "enabled_toolsets": spec.get("enabled_toolsets", spec.get("toolsets")),
        "disabled_toolsets": list(spec.get("disabled_toolsets") or []),
        "quiet_mode": True,
        "ephemeral_system_prompt": spec.get("instructions", ""),
        "log_prefix": context.get("log_prefix", "[subagent]"),
        "platform": context.get("platform", "subagent"),
        "skip_context_files": True,
        "skip_memory": True,
        "clarify_callback": callbacks.get("clarify"),
        "thinking_callback": callbacks.get("thinking"),
        "session_db": context.get("session_db", getattr(parent_agent, "_session_db", None)),
        "parent_session_id": context.get(
            "parent_session_id", getattr(parent_agent, "session_id", None)
        ),
        "session_id": spec.get("session_id", context.get("session_id")),
        "request_overrides": request_overrides,
        "openrouter_min_coding_score": getattr(
            parent_agent, "openrouter_min_coding_score", None
        ),
        "tool_progress_callback": callbacks.get("progress"),
        "iteration_budget": None,
        **provider_filters,
    }
    max_tokens = spec.get("max_tokens")
    if not isinstance(max_tokens, int):
        max_tokens = credentials.get("max_output_tokens")
    if not isinstance(max_tokens, int):
        max_tokens = getattr(parent_agent, "max_tokens", None)
    if isinstance(max_tokens, int):
        child_kwargs["max_tokens"] = max_tokens

    command = credentials.get("command") or route_cfg.get("command")
    args = credentials.get("args") or route_cfg.get("args")
    if command:
        if shutil.which(command):
            child_kwargs["acp_command"] = command
            child_kwargs["acp_args"] = list(args or [])
            child_kwargs["provider"] = "copilot-acp"
            child_kwargs["api_mode"] = "chat_completions"
        else:
            child_kwargs["acp_command"] = None
            child_kwargs["acp_args"] = []
    elif route_overridden:
        child_kwargs["acp_command"] = None
        child_kwargs["acp_args"] = []
    else:
        child_kwargs["acp_command"] = getattr(parent_agent, "acp_command", None)
        child_kwargs["acp_args"] = list(getattr(parent_agent, "acp_args", []) or [])

    if spec.get("provider") and not spec.get("command"):
        child_kwargs["acp_command"] = None
        child_kwargs["acp_args"] = []

    import model_tools
    from agent.delegation_context import delegated_child_context
    from run_agent import AIAgent

    saved_tool_names = list(model_tools._last_resolved_tool_names)
    try:
        with delegated_child_context():
            child = AIAgent(**child_kwargs)
    finally:
        model_tools._last_resolved_tool_names = list(saved_tool_names)

    if context.get("saved_tool_names") is not None:
        child._child_saved_tool_names = list(context["saved_tool_names"])
    else:
        child._child_saved_tool_names = saved_tool_names
    cwd = context.get("cwd")
    if cwd is not None:
        child.cwd = str(cwd)
    return child


def run_child(
    child: Any,
    prompt: str,
    *,
    task_id: str,
    timeout: Optional[float] = None,
    stream_callback: Any = None,
    initializer: Any = None,
    initargs: tuple = (),
):
    import model_tools

    saved_tool_names = getattr(child, "_child_saved_tool_names", None)
    if saved_tool_names is None:
        saved_tool_names = list(model_tools._last_resolved_tool_names)
    started = time.monotonic()

    def execute():
        kwargs = {"user_message": prompt, "task_id": task_id}
        if stream_callback is not None:
            kwargs["stream_callback"] = stream_callback
        return child.run_conversation(**kwargs)

    executor = None
    try:
        if timeout is None:
            raw = execute()
        else:
            from tools.daemon_pool import DaemonThreadPoolExecutor

            executor = DaemonThreadPoolExecutor(
                max_workers=1,
                initializer=initializer,
                initargs=initargs,
            )
            raw = executor.submit(execute).result(timeout=timeout)
    except (FuturesTimeoutError, TimeoutError):
        try:
            if hasattr(child, "interrupt"):
                child.interrupt()
            elif hasattr(child, "_interrupt_requested"):
                child._interrupt_requested = True
        except Exception:
            pass
        return {
            "status": "timeout",
            "exit_reason": "timeout",
            "duration_seconds": round(time.monotonic() - started, 2),
        }
    finally:
        if executor is not None:
            executor.shutdown(wait=False)
        model_tools._last_resolved_tool_names = list(saved_tool_names)

    result = dict(raw) if isinstance(raw, dict) else {"final_response": raw}
    result.setdefault("status", "completed")
    result.setdefault("exit_reason", "completed")
    result.setdefault("duration_seconds", round(time.monotonic() - started, 2))
    return result
