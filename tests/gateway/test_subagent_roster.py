"""Tests for the live delegate_task subagent roster bubble.

Covers: the subagent_roster display knob (resolution + normalisation), the pure
SubagentRosterState fold + formatter, the pipeline gate, and gateway-level
wiring (callback attach, sentinel relay, seed/edit/collapse, races).
"""

from gateway.display_config import OVERRIDEABLE_KEYS, resolve_display_setting


# ── display_config: resolution + normalisation ──────────────────────────────
class TestSubagentRosterConfig:
    def test_default_is_off_everywhere(self):
        assert resolve_display_setting({}, "telegram", "subagent_roster") == "off"
        assert resolve_display_setting({}, "discord", "subagent_roster") == "off"
        assert resolve_display_setting({}, "unknown", "subagent_roster") == "off"

    def test_global_on_applies(self):
        cfg = {"display": {"subagent_roster": "on"}}
        assert resolve_display_setting(cfg, "telegram", "subagent_roster") == "on"

    def test_platform_override_wins(self):
        cfg = {
            "display": {
                "subagent_roster": "off",
                "platforms": {"telegram": {"subagent_roster": "on"}},
            }
        }
        assert resolve_display_setting(cfg, "telegram", "subagent_roster") == "on"
        assert resolve_display_setting(cfg, "discord", "subagent_roster") == "off"

    def test_unknown_string_fails_safe_to_off(self):
        cfg = {"display": {"platforms": {"telegram": {"subagent_roster": "loud"}}}}
        assert resolve_display_setting(cfg, "telegram", "subagent_roster") == "off"

    def test_legacy_booleans(self):
        on = {"display": {"platforms": {"telegram": {"subagent_roster": True}}}}
        off = {"display": {"platforms": {"telegram": {"subagent_roster": False}}}}
        assert resolve_display_setting(on, "telegram", "subagent_roster") == "on"
        assert resolve_display_setting(off, "telegram", "subagent_roster") == "off"

    def test_case_insensitive(self):
        cfg = {"display": {"platforms": {"telegram": {"subagent_roster": "ON"}}}}
        assert resolve_display_setting(cfg, "telegram", "subagent_roster") == "on"

    def test_flag_is_in_overrideable_keys(self):
        assert "subagent_roster" in OVERRIDEABLE_KEYS
