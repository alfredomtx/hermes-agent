"""Generic child-route contracts after named delegation profiles are removed."""

from types import SimpleNamespace

import pytest

from agent.child_execution import merge_child_route, resolve_child_route


def _parent():
    return SimpleNamespace(
        model="parent-model",
        provider="parent-provider",
        base_url="https://parent.example.test/v1",
        api_key="parent-key",
        api_mode="chat_completions",
    )


def test_stale_config_profiles_are_ignored_and_generic_overrides_apply():
    route = merge_child_route(
        {
            "provider": "root-provider",
            "model": "root-model",
            "profiles": {"reviewer": {"provider": "profile-provider"}},
        },
        {"model": "override-model"},
    )

    assert route["provider"] == "root-provider"
    assert route["model"] == "override-model"
    assert "profiles" not in route
    assert "_profile" not in route


def test_resolve_child_route_uses_generic_context_config_without_profile():
    route, credentials = resolve_child_route(
        _parent(),
        {"resolved_credentials": {"model": "resolved-model"}},
        {
            "delegation_cfg": {
                "provider": "context-provider",
                "model": "context-model",
                "reasoning_effort": "high",
            }
        },
    )

    assert route["provider"] == "context-provider"
    assert route["model"] == "context-model"
    assert route["reasoning_effort"] == "high"
    assert credentials["model"] == "resolved-model"
