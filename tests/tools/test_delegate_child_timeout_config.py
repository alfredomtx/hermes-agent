from __future__ import annotations

import os
from pathlib import Path

import yaml


def test_root_child_timeout_ignores_stale_config():
    hermes_home = Path(os.environ["HERMES_HOME"])
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "delegation": {
                    "child_timeout_seconds": 120,
                    "profiles": {
                        "quick-review": {
                            "provider": "openai-codex",
                            "model": "gpt-5.5",
                            "child_timeout_seconds": 45,
                        },
                        "no-cap": {"child_timeout_seconds": 0},
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    from hermes_cli.config import load_config
    from tools.delegate_tool import _get_child_timeout

    delegation = load_config()["delegation"]
    assert _get_child_timeout(delegation) == 120.0
