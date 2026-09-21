from __future__ import annotations

from jobagent.db import candidates as cand_repo
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo
from jobagent.discovery import run_discovery
from jobagent.sources._common import insert_job


def test_url_dedup_and_blank_matches_for_searching_candidates_only(db_path):
    a = cand_repo.save_candidate({"name": "A", "search_enabled": 1}, ["IT Manager"], [])
    b = cand_repo.save_candidate({"name": "B", "search_enabled": 1}, ["Nurse"], [])
    c = cand_repo.save_candidate({"name": "C", "search_enabled": 0}, ["Clerk"], [])
    assert insert_job(
        title="SRE", company="Acme", url="https://example.com/sre-1", source="test"
    )
    assert job_repo.count_jobs() == 1
    assert match_repo.count_matches(a) == 1
    assert match_repo.count_matches(b) == 1
    assert match_repo.count_matches(c) == 0

    assert insert_job(
        title="SRE updated", company="Acme", url="https://example.com/sre-1", source="test"
    ) is False
    assert job_repo.count_jobs() == 1
    assert match_repo.count_all_matches() == 2
    assert job_repo.get_job(job_repo.list_job_ids()[0])["title"] == "SRE"


def test_source_failure_does_not_abort_crawl(db_path, monkeypatch):
    cand_repo.save_candidate({"name": "A", "search_enabled": 1}, ["IT Manager"], [])

    def ok_crawl(config):
        insert_job(title="Good", company="Ok", url="https://example.com/ok", source="remotive")
        return 1

    def boom(config):
        raise RuntimeError("simulated outage")

    monkeypatch.setattr("jobagent.sources.remotive.crawl", ok_crawl)
    monkeypatch.setattr("jobagent.sources.adzuna.crawl", boom)
    monkeypatch.setattr(
        "jobagent.discovery.source_skip_reason",
        lambda name, config: None if name in {"adzuna", "remotive"} else "disabled in settings",
    )
    monkeypatch.setattr(
        "jobagent.discovery.source_enabled",
        lambda config, name: name in {"adzuna", "remotive"},
    )

    summary = run_discovery()
    assert "adzuna" in summary["sources_failed"]
    assert "remotive" in summary["sources_succeeded"]
    assert summary["jobs_newly_inserted"] == 1
    assert job_repo.count_jobs() == 1

    summary2 = run_discovery()
    assert summary2["jobs_newly_inserted"] == 0
    assert job_repo.count_jobs() == 1
    assert match_repo.count_all_matches() == 1


def test_missing_credentials_are_skipped_not_failed(db_path, monkeypatch):
    monkeypatch.setattr(
        "jobagent.discovery.load_settings",
        lambda path=None: {
            "sources": {"adzuna": {"enabled": True}, "remotive": {"enabled": False}},
            "search": {},
            "api": {},
            "scheduler": {"dry_run": True},
        },
    )
    summary = run_discovery()
    adzuna = next(s for s in summary["sources"] if s["name"] == "adzuna")
    assert adzuna["attempted"] is False
    assert "missing" in (adzuna["skipped_reason"] or "")
    assert adzuna["ok"] is True
