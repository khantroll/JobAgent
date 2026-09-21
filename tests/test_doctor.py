from __future__ import annotations

from jobagent.doctor import collect_report, format_report, run_doctor


def test_doctor_reports_fresh_db_without_mutating_or_leaking_secrets(db_path, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "super-secret-value-do-not-print")
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "")
    from jobagent.db import candidates as cand_repo
    from jobagent.db import jobs as job_repo
    from jobagent.db import matches as match_repo

    before_jobs = job_repo.count_jobs()
    before_cands = cand_repo.count_candidates()
    before_matches = match_repo.count_all_matches()

    code, text = run_doctor()
    assert code == 0
    report = collect_report()
    assert report["ok"] is True
    assert report["version"]
    assert report["database_path"] == str(db_path)
    assert report["integrity"] == "ok"
    assert report["migrations"] == ["001_initial", "002_ranking_and_search"]
    assert report["counts"]["jobs"] == before_jobs
    assert report["counts"]["candidates"] == before_cands
    assert report["counts"]["matches"] == before_matches
    assert report["auto_apply_enabled"] is False
    assert report["auth_configured"] is False
    assert report["settings_ok"] is True
    assert isinstance(report["enabled_sources"], list)
    assert "MISTRAL_API_KEY" in report["api_keys_present"]
    assert report["api_keys_present"]["MISTRAL_API_KEY"] is True
    assert "super-secret-value-do-not-print" not in text
    assert "super-secret-value-do-not-print" not in format_report(report)
    assert job_repo.count_jobs() == before_jobs
    assert cand_repo.count_candidates() == before_cands
    assert match_repo.count_all_matches() == before_matches


def test_doctor_auth_flag_follows_token(db_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "present")
    report = collect_report()
    assert report["auth_configured"] is True
    text = format_report(report)
    assert "configured" in text
    assert "present" not in text.split("UI/API auth:")[-1].splitlines()[0]
