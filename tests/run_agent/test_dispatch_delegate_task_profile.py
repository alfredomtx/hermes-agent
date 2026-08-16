#!/usr/bin/env python3
"""Regression tests for the generic delegate_task dispatch contract."""

import unittest
from unittest.mock import MagicMock, patch


class TestDispatchDelegateTaskForwardsSupportedFields(unittest.TestCase):
    def _call_dispatch(self, function_args: dict):
        from run_agent import AIAgent

        captured = {}

        def _fake_delegate_task(**kwargs):
            captured.update(kwargs)
            return "{}"

        fake_self = MagicMock()
        fake_self._delegate_depth = 0
        with patch("tools.delegate_tool.delegate_task", _fake_delegate_task):
            AIAgent._dispatch_delegate_task(fake_self, function_args)
        return captured

    def test_dispatch_forwards_supported_delegate_fields(self):
        captured = self._call_dispatch(
            {
                "goal": "review X",
                "context": "ctx",
                "role": "leaf",
                "tasks": None,
                "max_iterations": 17,
            }
        )

        self.assertNotIn("profile", captured)
        self.assertEqual(captured["goal"], "review X")
        self.assertEqual(captured["context"], "ctx")
        self.assertEqual(captured["role"], "leaf")

    def test_all_schema_top_level_args_are_forwarded(self):
        from tools.delegate_tool import DELEGATE_TASK_SCHEMA

        schema_props = set(DELEGATE_TASK_SCHEMA["parameters"]["properties"].keys())
        captured = self._call_dispatch(
            {
                "goal": "g",
                "context": "c",
                "tasks": None,
                "role": "leaf",
                "background": False,
            }
        )

        for prop in schema_props:
            self.assertIn(
                prop,
                captured,
                f"schema exposes top-level '{prop}' but the inline forwarder "
                f"does not pass it to delegate_task",
            )


if __name__ == "__main__":
    unittest.main()
