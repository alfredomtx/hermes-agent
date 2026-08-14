"""Regression tests for numeric PID protection of Hermes runtimes."""

import json
from types import SimpleNamespace

import pytest

import gateway.status as gateway_status
import tools.terminal_tool as terminal_module


@pytest.fixture
def terminal_harness(monkeypatch, tmp_path):
    fake_env = SimpleNamespace(cwd=str(tmp_path), env={}, execute_calls=[])

    def execute(command, **kwargs):
        fake_env.execute_calls.append((command, kwargs))
        return {"output": "executed", "returncode": 0}

    fake_env.execute = execute
    config = {
        "env_type": "local",
        "cwd": str(tmp_path),
        "timeout": 30,
        "local_persistent": False,
        "docker_mount_cwd_to_workspace": False,
        "docker_image": "python:3.11",
    }
    terminal_module._active_environments.clear()
    terminal_module._last_activity.clear()
    monkeypatch.setattr(terminal_module, "_get_env_config", lambda: config)
    monkeypatch.setattr(terminal_module, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(terminal_module, "_create_environment", lambda **_kwargs: fake_env)
    monkeypatch.setattr(
        terminal_module,
        "_check_all_guards",
        lambda *_args, **_kwargs: {"approved": True},
    )
    monkeypatch.setenv("_HERMES_GATEWAY", "0")
    yield config, fake_env
    terminal_module._active_environments.clear()
    terminal_module._last_activity.clear()


def run_terminal(terminal_harness, command):
    _config, fake_env = terminal_harness
    result = json.loads(terminal_module.terminal_tool(command, task_id="numeric-kill", force=True))
    return result, fake_env


def set_process(monkeypatch, cmdline, start_time=17):
    monkeypatch.setattr(gateway_status, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(gateway_status, "_read_process_cmdline", lambda _pid: cmdline)
    monkeypatch.setattr(gateway_status, "get_process_start_time", lambda _pid: start_time)


@pytest.mark.parametrize(
    "command",
    [
        "kill -TERM 30031",
        "/bin/kill -15 30031",
        "sudo -n /usr/bin/kill -s TERM 30031",
        "env FOO=bar /bin/kill --signal TERM 30031",
        "exec nohup setsid time kill -TERM 30031",
        "true; kill -TERM 30031",
        "echo ok && /bin/kill -TERM 30031",
        "kill -TERM 11111 30031",
    ],
)
def test_blocks_hermes_serve_runtime_before_force(terminal_harness, monkeypatch, command):
    set_process(monkeypatch, "/venv/bin/python -m hermes_cli.main serve")
    result, fake_env = run_terminal(terminal_harness, command)
    assert result["status"] == "blocked"
    assert "Hermes serve runtime" in result["error"]
    assert fake_env.execute_calls == []


@pytest.mark.parametrize(
    "cmdline",
    [
        "hermes serve",
        "/opt/hermes/bin/hermes serve",
        "/opt/hermes/bin/hermes serve --host 127.0.0.1",
    ],
)
def test_blocks_packaged_hermes_serve_entry_point(terminal_harness, monkeypatch, cmdline):
    set_process(monkeypatch, cmdline)
    result, fake_env = run_terminal(terminal_harness, "kill -TERM 30031")
    assert result["status"] == "blocked"
    assert "Hermes serve runtime" in result["error"]
    assert fake_env.execute_calls == []


@pytest.mark.parametrize(
    "cmdline",
    [
        "hermes -p work serve",
        "hermes --profile work serve",
        "hermes -p=work serve",
        "hermes --profile=work serve",
        "python -m hermes_cli.main -p work serve",
        "python -m hermes_cli.main --profile work serve",
        "python -m hermes_cli.main -p=work serve",
        "python -m hermes_cli.main --profile=work serve",
    ],
)
def test_blocks_hermes_serve_with_profile_selector(terminal_harness, monkeypatch, cmdline):
    set_process(monkeypatch, cmdline)
    result, fake_env = run_terminal(terminal_harness, "kill -TERM 30031")
    assert result["status"] == "blocked"
    assert "Hermes serve runtime" in result["error"]
    assert fake_env.execute_calls == []


@pytest.mark.parametrize(
    "cmdline",
    [
        "echo hermes serve",
        "/opt/hermes-helper serve",
        "hermes dashboard",
        "hermes --profile serve",
        "python -m unrelated --profile work serve",
    ],
)
def test_does_not_classify_unrelated_hermes_serve_text(cmdline):
    assert terminal_module._looks_like_hermes_serve_runtime(cmdline) is False


@pytest.mark.parametrize(
    "command",
    [
        "kill -l 15",
        "/bin/kill --list 15",
        "sudo -n /usr/bin/kill -l 15",
        "env FOO=bar /bin/kill --list 15",
        "exec nohup setsid time kill --list 15",
        "true; kill -l 15",
        "echo ok && /bin/kill --list 15",
    ],
)
def test_list_queries_do_not_probe_numeric_pid_targets(terminal_harness, monkeypatch, command):
    set_process(monkeypatch, "/venv/bin/python -m hermes_cli.main serve")
    probed_pids = []
    monkeypatch.setattr(
        gateway_status,
        "_pid_exists",
        lambda pid: probed_pids.append(pid) or True,
    )

    result, fake_env = run_terminal(terminal_harness, command)
    assert result["exit_code"] == 0
    assert probed_pids == []
    assert len(fake_env.execute_calls) == 1


def test_padded_numeric_target_reaches_runtime_protection(terminal_harness, monkeypatch):
    set_process(monkeypatch, "/venv/bin/python -m hermes_cli.main serve")
    probed_pids = []
    monkeypatch.setattr(
        gateway_status,
        "_pid_exists",
        lambda pid: probed_pids.append(pid) or True,
    )

    result, fake_env = run_terminal(terminal_harness, "kill -TERM 00030031")
    assert result["status"] == "blocked"
    assert "Hermes serve runtime" in result["error"]
    assert probed_pids == [30031]
    assert fake_env.execute_calls == []


@pytest.mark.parametrize(
    "cmdline",
    [
        "hermes gateway run",
        "/venv/bin/python -m hermes_cli.main gateway run",
        "/opt/hermes/gateway/run.py",
    ],
)
def test_blocks_canonical_gateway_runtime(terminal_harness, monkeypatch, cmdline):
    set_process(monkeypatch, cmdline)
    result, fake_env = run_terminal(terminal_harness, "kill -TERM 30031")
    assert result["status"] == "blocked"
    assert "Hermes gateway runtime" in result["error"]
    assert fake_env.execute_calls == []


def test_blocks_live_unknown_owner(terminal_harness, monkeypatch):
    monkeypatch.setattr(
        gateway_status,
        "_pid_exists",
        lambda _pid: (_ for _ in ()).throw(PermissionError()),
    )
    result, fake_env = run_terminal(terminal_harness, "kill -TERM 30031")
    assert result["status"] == "blocked"
    assert "unknown owner" in result["error"]
    assert fake_env.execute_calls == []


def test_windows_liveness_probe_does_not_call_os_kill(terminal_harness, monkeypatch):
    monkeypatch.setattr(gateway_status, "_IS_WINDOWS", True)
    monkeypatch.setattr(gateway_status, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(
        terminal_module.os,
        "kill",
        lambda *_args: pytest.fail("Windows liveness probe must not call os.kill"),
    )
    monkeypatch.setattr(gateway_status, "_read_process_cmdline", lambda _pid: "/venv/bin/python -m hermes_cli.main serve")
    monkeypatch.setattr(gateway_status, "get_process_start_time", lambda _pid: 17)

    result, fake_env = run_terminal(terminal_harness, "kill -TERM 30031")
    assert result["status"] == "blocked"
    assert "Hermes serve runtime" in result["error"]
    assert fake_env.execute_calls == []


def test_blocks_unstable_live_pid_after_retry(terminal_harness, monkeypatch):
    monkeypatch.setattr(gateway_status, "_pid_exists", lambda _pid: True)
    start_times = iter([17, 18, 29, 30])
    monkeypatch.setattr(gateway_status, "get_process_start_time", lambda _pid: next(start_times))
    monkeypatch.setattr(gateway_status, "_read_process_cmdline", lambda _pid: "/usr/bin/sleep 100")
    result, fake_env = run_terminal(terminal_harness, "kill -TERM 30031")
    assert result["status"] == "blocked"
    assert "unknown owner" in result["error"]
    assert fake_env.execute_calls == []


def test_preserves_nonexistent_and_unrelated_targets(terminal_harness, monkeypatch):
    monkeypatch.setattr(
        gateway_status, "_pid_exists", lambda _pid: False
    )
    result, fake_env = run_terminal(terminal_harness, "kill -TERM 30031")
    assert result["exit_code"] == 0
    assert len(fake_env.execute_calls) == 1

    set_process(monkeypatch, "/usr/bin/sleep 100")
    result, fake_env = run_terminal(terminal_harness, "kill -TERM 30031")
    assert result["exit_code"] == 0
    assert len(fake_env.execute_calls) == 2


@pytest.mark.parametrize(
    "command",
    [
        "kill -TERM -1",
        "kill -TERM 0",
        "kill -TERM not-a-pid",
        "echo \"kill -TERM 30031\"",
        "python -c \"print('kill -TERM 30031')\"",
    ],
)
def test_preserves_non_targets_and_quoted_data(terminal_harness, monkeypatch, command):
    set_process(monkeypatch, "/venv/bin/python -m hermes_cli.main serve")
    result, fake_env = run_terminal(terminal_harness, command)
    assert result["exit_code"] == 0
    assert len(fake_env.execute_calls) == 1


def test_guard_is_local_backend_only(terminal_harness, monkeypatch):
    config, fake_env = terminal_harness
    config["env_type"] = "docker"
    set_process(monkeypatch, "/venv/bin/python -m hermes_cli.main serve")
    result, fake_env = run_terminal(terminal_harness, "kill -TERM 30031")
    assert result["exit_code"] == 0
    assert len(fake_env.execute_calls) == 1
