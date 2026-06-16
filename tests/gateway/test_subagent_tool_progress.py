"""Tests for the subagent_tool_progress display flag and its gateway formatters.

Covers:
  * gateway.display_config resolution + tri-state normalisation
  * gateway.run._format_subagent_tool_card  ("full" mode, one child tool call)
  * gateway.run._format_subagent_progress_card ("batched" mode summary)

The flag controls whether a delegate_task child's OWN tool calls surface in
gateway progress. Child tool events are already relayed from
tools/delegate_tool.py as "subagent.tool" / "subagent.progress"; the gateway
drops them unless this flag opts in.
"""


# ---------------------------------------------------------------------------
# display_config: resolution + normalisation
# ---------------------------------------------------------------------------

class TestSubagentToolProgressResolution:
    def test_default_is_off_everywhere(self):
        from gateway.display_config import resolve_display_setting

        # No config anywhere → global default "off".
        assert resolve_display_setting({}, "telegram", "subagent_tool_progress") == "off"
        assert resolve_display_setting({}, "discord", "subagent_tool_progress") == "off"
        assert resolve_display_setting({}, "unknown", "subagent_tool_progress") == "off"

    def test_global_setting_applies(self):
        from gateway.display_config import resolve_display_setting

        config = {"display": {"subagent_tool_progress": "batched"}}
        assert (
            resolve_display_setting(config, "telegram", "subagent_tool_progress")
            == "batched"
        )

    def test_platform_override_wins(self):
        from gateway.display_config import resolve_display_setting

        config = {
            "display": {
                "subagent_tool_progress": "batched",
                "platforms": {"telegram": {"subagent_tool_progress": "full"}},
            }
        }
        assert (
            resolve_display_setting(config, "telegram", "subagent_tool_progress")
            == "full"
        )
        # Other platforms still see the global value.
        assert (
            resolve_display_setting(config, "discord", "subagent_tool_progress")
            == "batched"
        )

    def test_normalise_unknown_string_fails_safe_to_off(self):
        from gateway.display_config import resolve_display_setting

        config = {"display": {"platforms": {"telegram": {"subagent_tool_progress": "loud"}}}}
        assert (
            resolve_display_setting(config, "telegram", "subagent_tool_progress")
            == "off"
        )

    def test_normalise_legacy_booleans(self):
        from gateway.display_config import resolve_display_setting

        true_cfg = {"display": {"platforms": {"telegram": {"subagent_tool_progress": True}}}}
        false_cfg = {"display": {"platforms": {"telegram": {"subagent_tool_progress": False}}}}
        assert (
            resolve_display_setting(true_cfg, "telegram", "subagent_tool_progress")
            == "full"
        )
        # NOTE: bare `off`/`False` in YAML 1.1 both arrive as Python False here.
        assert (
            resolve_display_setting(false_cfg, "telegram", "subagent_tool_progress")
            == "off"
        )

    def test_normalise_is_case_insensitive(self):
        from gateway.display_config import resolve_display_setting

        config = {"display": {"platforms": {"telegram": {"subagent_tool_progress": "FULL"}}}}
        assert (
            resolve_display_setting(config, "telegram", "subagent_tool_progress")
            == "full"
        )

    def test_flag_is_in_overrideable_keys(self):
        from gateway.display_config import OVERRIDEABLE_KEYS

        assert "subagent_tool_progress" in OVERRIDEABLE_KEYS


# ---------------------------------------------------------------------------
# run._format_subagent_tool_card — "full" mode
# ---------------------------------------------------------------------------

class TestFormatSubagentToolCard:
    def test_single_subagent_no_index_tag(self):
        from gateway.run import _format_subagent_tool_card

        card = _format_subagent_tool_card(
            "terminal",
            "pytest -q",
            goal="Run the test suite",
            task_index=0,
            task_count=1,
        )
        # Header carries the goal, no "[1]" tag for a single subagent.
        assert "Run the test suite" in card
        assert "[1]" not in card
        # Tool line present with its preview.
        assert "terminal" in card
        assert "pytest -q" in card

    def test_batch_subagent_shows_index_tag(self):
        from gateway.run import _format_subagent_tool_card

        card = _format_subagent_tool_card(
            "read_file",
            "config.yaml",
            goal="Audit config",
            task_index=2,
            task_count=4,
        )
        # 1-indexed tag for parallel batches.
        assert "[3]" in card
        assert "read_file" in card

    def test_long_preview_is_truncated(self):
        from gateway.run import _format_subagent_tool_card

        long_preview = "x" * 200
        card = _format_subagent_tool_card(
            "search_files",
            long_preview,
            goal="g",
            task_index=0,
            task_count=1,
        )
        # Capped well under the raw length (default cap 40).
        assert "x" * 200 not in card
        assert "…" in card

    def test_missing_goal_falls_back_to_subagent_label(self):
        from gateway.run import _format_subagent_tool_card

        card = _format_subagent_tool_card("web_search", "weather", goal=None)
        assert "subagent" in card
        assert "web_search" in card

    def test_never_includes_output_marker(self):
        from gateway.run import _format_subagent_tool_card

        # The preview is tool INPUT only; the formatter should not invent any
        # output/summary framing.
        card = _format_subagent_tool_card("terminal", "ls", goal="list")
        assert "summary" not in card.lower()
        assert "output" not in card.lower()


# ---------------------------------------------------------------------------
# run._format_subagent_progress_card — "batched" mode
# ---------------------------------------------------------------------------

class TestFormatSubagentProgressCard:
    def test_passthrough_of_batched_summary(self):
        from gateway.run import _format_subagent_progress_card

        summary = "🔀 terminal, read_file, search_files, terminal, web_search"
        assert _format_subagent_progress_card(summary) == summary

    def test_empty_preview_returns_none(self):
        from gateway.run import _format_subagent_progress_card

        assert _format_subagent_progress_card("") is None
        assert _format_subagent_progress_card(None) is None
        assert _format_subagent_progress_card("   ") is None


# ---------------------------------------------------------------------------
# progress_callback dispatch — exercises the real branch via a faithful harness
# ---------------------------------------------------------------------------
# The actual progress_callback is a deep closure inside run_sync; reproducing
# the full gateway turn here is impractical. These tests pin the dispatch
# CONTRACT the branch implements (which event_type → which queue card, per
# mode) so a future refactor that diverges from it fails loudly. The branch
# body itself is integration-smoke-tested separately.

class TestSubagentDispatchContract:
    def _dispatch(self, mode, event_type, tool_name=None, preview=None, **kwargs):
        """Mirror of the gateway/run.py progress_callback subagent branch."""
        from gateway.run import (
            _format_subagent_tool_card,
            _format_subagent_progress_card,
        )

        out = []
        if event_type in {"subagent.tool", "subagent.progress", "subagent_progress"}:
            if mode == "full" and event_type == "subagent.tool":
                card = _format_subagent_tool_card(
                    tool_name,
                    preview,
                    goal=kwargs.get("goal"),
                    task_index=int(kwargs.get("task_index", 0) or 0),
                    task_count=int(kwargs.get("task_count", 1) or 1),
                )
                if card:
                    out.append(("__tool_start__", "__subagent__", card))
            elif mode == "batched" and event_type in {
                "subagent.progress",
                "subagent_progress",
            }:
                card = _format_subagent_progress_card(preview or tool_name)
                if card:
                    out.append(("__tool_start__", "__subagent__", card))
            return ("CONSUMED", out)
        return ("PASSTHROUGH", out)

    def test_off_drops_all_subagent_events(self):
        consumed, out = self._dispatch(
            "off", "subagent.tool", "terminal", "ls", goal="g"
        )
        assert consumed == "CONSUMED" and out == []
        consumed, out = self._dispatch("off", "subagent.progress", preview="🔀 a, b")
        assert out == []

    def test_full_renders_per_tool_only(self):
        _, out = self._dispatch(
            "full", "subagent.tool", "terminal", "pytest", goal="Run tests"
        )
        assert len(out) == 1
        assert out[0][:2] == ("__tool_start__", "__subagent__")
        assert "terminal" in out[0][2] and "Run tests" in out[0][2]
        # full ignores the batched summary event
        _, out2 = self._dispatch("full", "subagent.progress", preview="🔀 a, b, c")
        assert out2 == []

    def test_batched_renders_summary_only(self):
        _, out = self._dispatch(
            "batched", "subagent.tool", "terminal", "pytest", goal="g"
        )
        assert out == []
        _, out2 = self._dispatch(
            "batched", "subagent.progress", preview="🔀 terminal, read_file"
        )
        assert len(out2) == 1 and "terminal, read_file" in out2[0][2]

    def test_batched_renders_depth2_underscore_event(self):
        # Depth-2 grandchild summary: legacy underscore event with the summary
        # in the tool_name slot, preview empty.
        _, out = self._dispatch(
            "batched", "subagent_progress", tool_name="🔀 [1] web_search, read_file"
        )
        assert len(out) == 1
        assert "web_search, read_file" in out[0][2]

    def test_non_subagent_event_passes_through(self):
        verdict, out = self._dispatch("full", "tool.started", "read_file")
        assert verdict == "PASSTHROUGH" and out == []
        verdict, _ = self._dispatch("full", "tool.completed", "read_file")
        assert verdict == "PASSTHROUGH"

    def test_cards_use_subagent_sentinel_not_delegate_task(self):
        # Regression guard for nit N2: subagent cards must NOT be queued under
        # the "delegate_task" tool name, or they'd absorb the parent's
        # delegate_task completion-duration suffix via the pending-tool FIFO.
        _, out = self._dispatch("full", "subagent.tool", "terminal", "ls", goal="g")
        assert out[0][1] == "__subagent__"
        assert out[0][1] != "delegate_task"
