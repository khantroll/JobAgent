"""Dry-run is a saved setting. Auto-apply is a separate opt-in and does not send."""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from jobagent import AUTO_APPLY_ENABLED
from jobagent.config import load_settings, save_scheduler_settings, submission_allowed
from jobagent.db import runs as run_repo
from jobagent.pipeline import run_cycle
from jobagent.web.app import app


def test_auto_apply_sender_stays_off():
    assert AUTO_APPLY_ENABLED is False


def test_load_settings_honors_dry_run_false_and_keeps_auto_apply_off(tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    path.write_text(
        "scheduler:\n  dry_run: false\nsearch: {}\nsources: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(path))
    monkeypatch.setenv("MISTRAL_API_KEY", "super-secret-value")
    cfg = load_settings()
    assert cfg["scheduler"]["dry_run"] is False
    assert cfg["scheduler"]["auto_apply"] is False
    assert submission_allowed(cfg) is False

    save_scheduler_settings(dry_run=False, auto_apply=True)
    text = path.read_text(encoding="utf-8")
    assert "super-secret-value" not in text
    assert "dry_run: false" in text
    assert "auto_apply: true" in text
    again = load_settings()
    assert again["scheduler"]["dry_run"] is False
    assert again["scheduler"]["auto_apply"] is True
    assert submission_allowed(again) is False


def test_missing_dry_run_defaults_on(tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    path.write_text("scheduler:\n  run_every_hours: 4\n", encoding="utf-8")
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(path))
    cfg = load_settings()
    assert cfg["scheduler"]["dry_run"] is True
    assert cfg["scheduler"]["auto_apply"] is False


def test_cycle_records_dry_run_from_settings_and_does_not_apply(db_path, monkeypatch):
    from jobagent.db import candidates as cand_repo
    from jobagent.db import jobs as job_repo
    from jobagent.db import matches as match_repo

    cid = cand_repo.save_candidate(
        {
            "name": "Ada",
            "location": "Fort Smith, AR",
            "commute_review_minutes": 30,
            "commute_auto_apply_minutes": 15,
            "min_match_score": 50,
        },
        ["Sysadmin"],
        [],
    )
    job_repo.upsert_job(
        {
            "title": "System Administrator",
            "company": "Acme",
            "location": "Dallas, TX",
            "url": "https://example.com/dallas",
            "source": "test",
            "description": "On site. Remote desktop support. This is not a remote position.",
        }
    )
    monkeypatch.setattr(
        "jobagent.pipeline.load_settings",
        lambda path=None: {
            "scheduler": {"dry_run": False, "auto_apply": True, "max_rank_per_run": 10},
            "search": {"min_match_score": 50, "filter_by_location": True},
            "sources": {},
        },
    )
    monkeypatch.setattr("jobagent.pipeline.run_all_crawlers", lambda config: 0)
    monkeypatch.setattr("jobagent.ranking.score_job", lambda job, config: (90, "fit"))
    monkeypatch.setattr(
        "jobagent.commute.resolve_drive_minutes",
        lambda *args, **kwargs: (40, False),
    )

    summary = run_cycle(candidate_id=cid)
    assert summary["applied"] == 0
    assert summary["dry_run"] is False
    assert summary["auto_apply_opt_in"] is True
    assert summary["auto_apply_enabled"] is False
    assert summary["submitted"] is False
    rows, _total = match_repo.list_matches(cid)
    assert rows[0]["commute_result"] == "skip"
    assert rows[0]["commute_minutes"] == 40
    history = run_repo.list_run_history(1)
    assert history[0]["dry_run"] == 0
    assert history[0]["applied"] == 0


def test_settings_page_persists_flags_without_sending(db_path, tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    path.write_text(
        "scheduler:\n  dry_run: true\n  auto_apply: false\nsearch: {}\nsources: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(path))
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "")
    with TestClient(app) as client:
        page = client.get("/settings")
        assert page.status_code == 200
        dry = re.search(r'id="dry-run"[^>]*>', page.text)
        auto = re.search(r'id="auto-apply"[^>]*>', page.text)
        assert dry and "checked" in dry.group(0)
        assert auto and "checked" not in auto.group(0)
        assert "does not submit" in page.text

        saved = client.post(
            "/settings",
            data={"dry_run": "0", "auto_apply": "0"},
            follow_redirects=True,
        )
        assert saved.status_code == 200
        dry = re.search(r'id="dry-run"[^>]*>', saved.text)
        assert dry and "checked" not in dry.group(0)
        home = client.get("/")
        assert "Dry run is off" in home.text
        assert "nothing is submitted" in home.text

        opted = client.post(
            "/settings",
            data={"dry_run": ["0", "1"], "auto_apply": ["0", "1"]},
            follow_redirects=True,
        )
        assert opted.status_code == 200
        auto = re.search(r'id="auto-apply"[^>]*>', opted.text)
        assert auto and "checked" in auto.group(0)

    cfg = load_settings()
    assert cfg["scheduler"]["dry_run"] is True
    assert cfg["scheduler"]["auto_apply"] is True
    assert submission_allowed(cfg) is False
