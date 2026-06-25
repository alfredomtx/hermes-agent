"""Tests for cron/topic_heal.py — the in-delivery Telegram topic self-heal.

Unit-level: metadata resolution, seed sidecar write, co-located repoint, and the
heal_dead_thread orchestrator. E2E-style repoint runs against a real temp
jobs.json (cron.jobs) under a temp HERMES_HOME.
"""
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    (home / "state").mkdir(parents=True)
    (home / "cron").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    # Honor the override in cron.scheduler._get_hermes_home (topic_heal resolves
    # home through it).
    import cron.scheduler as sched
    monkeypatch.setattr(sched, "_hermes_home", home)
    # cron.jobs caches JOBS_FILE/CRON_DIR/OUTPUT_DIR at import — redirect them so
    # save_jobs/load_jobs/update_job hit the temp jobs.json, not real ~/.hermes.
    import cron.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "HERMES_DIR", home)
    monkeypatch.setattr(jobs_mod, "CRON_DIR", home / "cron")
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", home / "cron" / "output")
    return home


class TestTopicMetadata:
    def test_prefers_job_record(self, hermes_home):
        from cron.topic_heal import topic_metadata_for_job
        job = {"id": "j1", "telegram_topic": {"name": "Slack Monitor", "seed": "s", "icon_emoji_id": "9"}}
        meta = topic_metadata_for_job(job)
        assert meta["name"] == "Slack Monitor"
        assert meta["seed"] == "s"
        assert meta["icon_emoji_id"] == "9"

    def test_falls_back_to_sidecar_registry(self, hermes_home):
        from cron.topic_heal import topic_metadata_for_job
        reg = hermes_home / "state" / "agent-topic-registry.json"
        reg.write_text(json.dumps({"jobs": {"j2": {"topic_name": "Audit", "seed": "x", "icon_emoji_id": "7"}}}))
        meta = topic_metadata_for_job({"id": "j2"})
        assert meta["name"] == "Audit"
        assert meta["seed"] == "x"

    def test_none_when_unknown(self, hermes_home):
        from cron.topic_heal import topic_metadata_for_job
        assert topic_metadata_for_job({"id": "nope"}) is None

    def test_none_when_name_present_but_no_seed(self, hermes_home):
        from cron.topic_heal import topic_metadata_for_job
        # Cannot recreate a context-aware topic without a seed.
        assert topic_metadata_for_job({"id": "j", "telegram_topic": {"name": "X"}}) is None


class TestForumTopicTargetGuard:
    def test_true_for_supergroup_numeric_thread(self):
        from cron.topic_heal import is_telegram_forum_topic_target
        assert is_telegram_forum_topic_target("telegram", "-1003714194882", "43095") is True

    def test_false_for_general_topic_1(self):
        from cron.topic_heal import is_telegram_forum_topic_target
        assert is_telegram_forum_topic_target("telegram", "-1003714194882", "1") is False

    def test_false_for_no_thread(self):
        from cron.topic_heal import is_telegram_forum_topic_target
        assert is_telegram_forum_topic_target("telegram", "-1003714194882", None) is False

    def test_false_for_non_supergroup_chat(self):
        from cron.topic_heal import is_telegram_forum_topic_target
        assert is_telegram_forum_topic_target("telegram", "12345", "99") is False

    def test_false_for_non_telegram(self):
        from cron.topic_heal import is_telegram_forum_topic_target
        assert is_telegram_forum_topic_target("discord", "-1003714194882", "99") is False


class TestThreadNotFoundDetection:
    def test_live_send_result_raw_response(self):
        from cron.topic_heal import thread_not_found_in_result
        from gateway.platforms.base import SendResult
        r = SendResult(success=False, error="thread not found",
                       raw_response={"thread_not_found": True, "thread_fallback": False})
        assert thread_not_found_in_result(r) is True

    def test_live_send_result_thread_fallback(self):
        from cron.topic_heal import thread_not_found_in_result
        from gateway.platforms.base import SendResult
        r = SendResult(success=True, message_id="1", raw_response={"thread_fallback": True})
        assert thread_not_found_in_result(r) is True

    def test_standalone_dict(self):
        from cron.topic_heal import thread_not_found_in_result
        assert thread_not_found_in_result({"error": "Bad Request: message thread not found", "thread_not_found": True}) is True

    def test_clean_result_false(self):
        from cron.topic_heal import thread_not_found_in_result
        from gateway.platforms.base import SendResult
        assert thread_not_found_in_result(SendResult(success=True, message_id="1", raw_response={})) is False
        assert thread_not_found_in_result({"success": True}) is False


class TestWriteSeed:
    def test_writes_sidecar(self, hermes_home):
        from cron.topic_heal import write_topic_seed
        assert write_topic_seed(55501, "seed text") is True
        p = hermes_home / "state" / "topic-seeds" / "55501.json"
        data = json.loads(p.read_text())
        assert data["thread_id"] == "55501"
        assert data["seed_text"] == "seed text"
        assert "created_at" in data

    def test_empty_seed_is_noop(self, hermes_home):
        from cron.topic_heal import write_topic_seed
        assert write_topic_seed(1, "") is False


class TestRepointColocated:
    def test_repoints_all_enabled_jobs_on_thread(self, hermes_home):
        from cron.jobs import save_jobs, load_jobs
        from cron.topic_heal import repoint_colocated_jobs

        save_jobs([
            {"id": "a", "enabled": True, "deliver": "telegram:-100:500",
             "origin": {"platform": "telegram", "chat_id": "-100", "thread_id": "500"},
             "schedule": {"kind": "interval", "minutes": 30}},
            {"id": "b", "enabled": True, "deliver": "telegram:-100:500", "origin": {},
             "schedule": {"kind": "interval", "minutes": 30}},
            {"id": "c", "enabled": True, "deliver": "telegram:-100:999", "origin": {},
             "schedule": {"kind": "interval", "minutes": 30}},   # different thread
            {"id": "d", "enabled": False, "deliver": "telegram:-100:500", "origin": {},
             "schedule": {"kind": "interval", "minutes": 30}},   # disabled
        ])
        meta = {"name": "T", "seed": "s"}
        repointed = repoint_colocated_jobs("-100", "500", 777, meta)
        assert set(repointed) == {"a", "b"}
        by_id = {j["id"]: j for j in load_jobs()}
        assert by_id["a"]["deliver"] == "telegram:-100:777"
        assert by_id["a"]["origin"]["thread_id"] == "777"
        assert by_id["b"]["deliver"] == "telegram:-100:777"
        # untouched
        assert by_id["c"]["deliver"] == "telegram:-100:999"
        assert by_id["d"]["deliver"] == "telegram:-100:500"
        # metadata copied onto repointed jobs so they can self-heal next time
        assert by_id["a"]["telegram_topic"]["name"] == "T"
        assert by_id["b"]["telegram_topic"]["name"] == "T"


class TestHealDeadThread:
    def test_no_metadata_returns_none(self, hermes_home):
        from cron.topic_heal import heal_dead_thread
        assert heal_dead_thread({"id": "x"}, "-100", "500", adapter=None, loop=None, token="tok") is None

    def test_recreate_seed_repoint_via_adapter(self, hermes_home):
        import asyncio
        import threading
        from cron.jobs import save_jobs, load_jobs
        from cron.topic_heal import heal_dead_thread

        save_jobs([
            {"id": "a", "enabled": True, "deliver": "telegram:-100:500",
             "origin": {"platform": "telegram", "chat_id": "-100", "thread_id": "500"},
             "telegram_topic": {"name": "Slack Monitor", "seed": "ctx seed", "icon_emoji_id": "9"},
             "schedule": {"kind": "interval", "minutes": 30}},
        ])

        class FakeAdapter:
            async def create_forum_topic(self, chat_id, name, icon_color=None, icon_custom_emoji_id=None):
                assert name == "Slack Monitor"
                return 777

        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        try:
            job = load_jobs()[0]
            new_tid = heal_dead_thread(job, "-100", "500", adapter=FakeAdapter(), loop=loop, token=None)
        finally:
            loop.call_soon_threadsafe(loop.stop)

        assert new_tid == 777
        seed = hermes_home / "state" / "topic-seeds" / "777.json"
        assert json.loads(seed.read_text())["seed_text"] == "ctx seed"
        assert load_jobs()[0]["deliver"] == "telegram:-100:777"
