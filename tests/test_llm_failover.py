"""OpenRouter ranking, bounded 429 backoff, and provider failover."""
from __future__ import annotations

import logging

import pytest
import yaml
from fastapi.testclient import TestClient

from jobagent.cli import main
from jobagent.config import load_api_token, load_settings, save_api_secrets
from jobagent.doctor import collect_report, format_report
from jobagent.llm import (
    OPENROUTER_URL,
    RateLimitError,
    begin_llm_cycle,
    clear_provider_cooldowns,
    complete,
    llm_cycle_stats,
    log_llm_cycle_summary,
    model_for,
)
from jobagent.ranking import ordered_for_ranking, score_job
from jobagent.web.app import app


SECRET = "openrouter-secret-ZZ91"
TOKEN = "cycle-token-QQ77"


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch):
    ticker = _Clock()
    monkeypatch.setattr("jobagent.llm.time.monotonic", ticker.monotonic)
    monkeypatch.setattr("jobagent.llm.time.sleep", ticker.sleep)
    begin_llm_cycle()
    clear_provider_cooldowns()
    yield ticker
    begin_llm_cycle()
    clear_provider_cooldowns()


def _config(**llm):
    base = {
        "providers": ["mistral", "openrouter"],
        "max_llm_calls": 10,
        "max_llm_seconds": 60,
        "max_backoff_seconds": 8,
        "cooldown_seconds": 120,
        "retry_backoff_seconds": 2,
    }
    base.update(llm)
    return {
        "llm": base,
        "api": {"mistral_key": "mistral-secret", "openrouter_key": SECRET},
        "search": {"titles": ["Admin"], "keywords": ["linux"]},
        "profile": {"name": "Ada"},
    }


def test_openrouter_default_model_and_request(monkeypatch, caplog):
    captured = {}

    class _Response:
        status_code = 200
        headers: dict[str, str] = {}

        def json(self):
            return {"choices": [{"message": {"content": '{"score": 81, "reason": "fit"}'}}]}

    def fake_post(url, headers=None, json=None, timeout=30):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _Response()

    monkeypatch.setattr("jobagent.llm.requests.post", fake_post)
    begin_llm_cycle()
    cfg = {
        "llm": {"provider": "openrouter"},
        "api": {"openrouter_key": SECRET},
    }
    assert model_for(cfg, "openrouter") == "openrouter/free"
    with caplog.at_level(logging.DEBUG):
        text = complete(cfg, system="sys", user="user", max_tokens=32)
    assert text.startswith("{")
    assert captured["url"] == OPENROUTER_URL
    assert captured["json"]["model"] == "openrouter/free"
    assert captured["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert SECRET not in caplog.text


def test_429_waits_bounded_retry_after_then_fails_over(clock, monkeypatch):
    calls: list[str] = []

    def mistral(*args, **kwargs):
        calls.append("mistral")
        raise RateLimitError(30)

    def openrouter(*args, **kwargs):
        calls.append("openrouter")
        return '{"score": 80, "reason": "fit"}'

    from jobagent import llm

    monkeypatch.setitem(llm._COMPLETERS, "mistral", mistral)
    monkeypatch.setitem(llm._COMPLETERS, "openrouter", openrouter)
    result = score_job({"title": "Admin", "description": "linux"}, _config())
    assert calls == ["mistral", "mistral", "openrouter"]
    assert clock.slept == [8]
    stats = llm_cycle_stats()
    assert stats.rate_limits == 2
    assert stats.failovers == 1
    assert stats.calls == 3
    assert result.provider == "openrouter"
    assert result.model == "openrouter/free"
    assert result.score == 80


def test_cooldown_skips_the_failed_provider(clock, monkeypatch):
    calls: list[str] = []

    def mistral(*args, **kwargs):
        calls.append("mistral")
        raise RuntimeError("provider down")

    def openrouter(*args, **kwargs):
        calls.append("openrouter")
        return "ok"

    from jobagent import llm

    monkeypatch.setitem(llm._COMPLETERS, "mistral", mistral)
    monkeypatch.setitem(llm._COMPLETERS, "openrouter", openrouter)
    assert complete(_config(), system="", user="one") == "ok"
    assert complete(_config(), system="", user="two") == "ok"
    assert calls == ["mistral", "openrouter", "openrouter"]
    clock.now += 121
    assert complete(_config(), system="", user="three") == "ok"
    assert calls[-2] == "mistral"


def test_call_cap_falls_back_to_keywords(clock, monkeypatch, caplog):
    calls: list[str] = []

    def openrouter(*args, **kwargs):
        calls.append("openrouter")
        return '{"score": 77, "reason": "fit"}'

    from jobagent import llm

    monkeypatch.setitem(llm._COMPLETERS, "openrouter", openrouter)
    cfg = _config(providers=["openrouter"], max_llm_calls=1)
    job = {"title": "Linux Admin", "description": "linux systems", "commute_result": "needs_review"}
    first = score_job(job, cfg)
    second = score_job(dict(job, title="Another Admin"), cfg)
    assert first.provider == "openrouter"
    assert second.provider == "fallback"
    assert "cap reached" in second.reason
    assert calls == ["openrouter"]
    with caplog.at_level(logging.INFO):
        log_llm_cycle_summary()
    assert "LLM cycle summary — calls=1 rate_limits=0 failovers=0 fallbacks=1" in caplog.text


def test_commute_skip_does_not_call_the_llm_and_passers_go_first(monkeypatch):
    calls: list[str] = []

    def openrouter(*args, **kwargs):
        calls.append("yes")
        return '{"score": 70, "reason": "fit"}'

    from jobagent import llm

    monkeypatch.setitem(llm._COMPLETERS, "openrouter", openrouter)
    begin_llm_cycle()
    clear_provider_cooldowns()
    jobs = [
        {"title": "Far", "commute_result": "skip", "description": "linux"},
        {"title": "Near", "commute_result": "needs_review", "description": "linux"},
        {"title": "Maybe", "description": "linux"},
    ]
    assert [job["title"] for job in ordered_for_ranking(jobs)] == ["Near", "Maybe", "Far"]
    cfg = _config(providers=["openrouter"])
    skipped = score_job(jobs[0], cfg)
    assert skipped.provider == "fallback"
    assert calls == []
    assert score_job(jobs[1], cfg).provider == "openrouter"
    assert calls == ["yes"]


def test_settings_saves_model_and_masks_openrouter_key(db_path, tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "scheduler:\n  dry_run: true\n  auto_apply: false\nsearch: {}\nsources: {}\n"
        "llm:\n  provider: openrouter\n  model: openrouter/free\n",
        encoding="utf-8",
    )
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text(f"api:\n  openrouter_key: {SECRET}\n", encoding="utf-8")
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("JOBAGENT_SECRETS_PATH", str(secrets))
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with TestClient(app) as client:
        page = client.get("/settings")
        assert page.status_code == 200
        assert SECRET not in page.text
        assert "••••ZZ91" in page.text
        assert 'name="llm_model" value="openrouter/free"' in page.text
        assert 'id="dry-run"' in page.text
        saved = client.post(
            "/settings",
            data={
                "dry_run": "1",
                "auto_apply": "0",
                "llm_model": "openrouter/free",
                "llm_providers": "openrouter, mistral",
                "llm_max_calls": "25",
                "llm_max_seconds": "45",
                "key_openrouter_key": "",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
    stored = yaml.safe_load(settings.read_text(encoding="utf-8"))
    assert stored["scheduler"]["dry_run"] is True
    assert stored["scheduler"]["auto_apply"] is False
    assert stored["llm"]["model"] == "openrouter/free"
    assert stored["llm"]["providers"] == ["openrouter", "mistral"]
    assert stored["llm"]["max_llm_calls"] == 25
    assert stored["llm"]["max_llm_seconds"] == 45
    assert SECRET not in settings.read_text(encoding="utf-8")
    assert secrets.read_text(encoding="utf-8").count(SECRET) == 1
    again = load_settings()
    assert again["llm"]["max_llm_calls"] == 25
    assert again["api"]["openrouter_key"] == SECRET


def test_blank_settings_post_keeps_model_and_api_token(tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text("llm:\n  model: openrouter/custom\nscheduler:\n  dry_run: true\n", encoding="utf-8")
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text(f"api:\n  mistral_key: keep-me\napi_token: {TOKEN}\n", encoding="utf-8")
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("JOBAGENT_SECRETS_PATH", str(secrets))
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "")
    save_api_secrets({"openrouter_key": SECRET})
    text = secrets.read_text(encoding="utf-8")
    assert TOKEN in text
    assert SECRET in text
    assert load_api_token() == TOKEN
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "env-token-value")
    assert load_api_token() == "env-token-value"
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "")
    report = collect_report()
    doctor = format_report(report)
    assert TOKEN not in doctor
    assert SECRET not in doctor
    assert report["auth_configured"] is True
    assert "model: openrouter/custom" in settings.read_text(encoding="utf-8")


def test_run_cycle_token_flag_warns_and_is_not_logged(monkeypatch, caplog):
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "")
    monkeypatch.setattr("jobagent.pipeline.run_cycle", lambda candidate_id=None: {"ok": True, "applied": 0})
    with caplog.at_level(logging.WARNING):
        code = main(["run-cycle", "--api-token", TOKEN])
    assert code == 0
    assert TOKEN not in caplog.text
    assert "deprecated" in caplog.text.lower()
    assert load_api_token() == TOKEN
    help_text = __import__("subprocess").run(
        ["python3", "-m", "jobagent.cli", "run-cycle", "--help"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "Deprecated" in help_text
    assert "process listings" in help_text
