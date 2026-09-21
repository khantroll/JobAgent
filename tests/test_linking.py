"""New catalog jobs get a blank match per candidate; import does not.

Crawl unions search preferences, inserts into the shared catalog, then
``upsert_job(..., link_candidates=True)`` creates one blank match row for each
person. Ranking and status are candidate-specific. Re-upserting the same URL
must not duplicate matches. Legacy import uses ``link_candidates=False``.
"""
from __future__ import annotations

from jobagent.config import crawl_config_for_candidates
from jobagent.db import candidates as cand_repo
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo
from jobagent.ranking import run_ranking


JOB = {
    "title": "Systems Engineer",
    "company": "City Hall",
    "url": "https://example.com/jobs/syseng-link",
    "source": "test",
    "location": "Fort Smith, AR",
    "description": "VMware and Active Directory",
}


def test_crawl_config_unions_candidate_search_prefs():
    cfg = crawl_config_for_candidates(
        [
            {"titles": [{"title": "IT Manager"}], "hej_category_ids": "101", "location": "Fort Smith, AR"},
            {"titles": [{"title": "Nurse"}], "hej_category_ids": "202", "location": "Austin, TX"},
        ],
        base={"search": {}, "sources": {}},
    )
    assert set(cfg["search"]["titles"]) == {"IT Manager", "Nurse"}
    assert "101" in cfg["_hej_category_ids"]
    assert "202" in cfg["_hej_category_ids"]


def test_crawl_config_unions_ats_employers():
    cfg = crawl_config_for_candidates(
        [
            {
                "titles": [{"title": "IT Manager"}],
                "employers": [
                    {"name": "Sumologic", "greenhouse_slug": "sumologic"},
                    {"name": "Wachter", "lever_slug": "wachter"},
                    {
                        "name": "ArcBest",
                        "source_type": "workday",
                        "workday_url": "https://arcbest.wd1.myworkdayjobs.com/ArcBest",
                    },
                ],
            }
        ],
        base={"search": {}, "sources": {"workday": {"companies": []}}},
    )
    assert "sumologic" in cfg["sources"]["greenhouse"]["companies"]
    assert "wachter" in cfg["sources"]["lever"]["companies"]
    assert cfg["sources"]["workday"]["companies"][0]["name"] == "ArcBest"


def test_new_shared_job_creates_blank_match_for_each_candidate(db_path):
    a = cand_repo.save_candidate({"name": "Candidate A"}, ["IT Manager"], [])
    b = cand_repo.save_candidate({"name": "Candidate B"}, ["Nurse"], [])
    assert match_repo.count_all_matches() == 0

    inserted = job_repo.upsert_job(JOB)
    assert inserted is True
    jid = job_repo.list_job_ids()[0]
    assert job_repo.count_jobs() == 1
    assert match_repo.count_all_matches() == 2

    match_a = match_repo.get_match(a, jid)
    match_b = match_repo.get_match(b, jid)
    assert match_a and match_b
    assert match_a["status"] == "new"
    assert match_b["status"] == "new"
    assert match_a["score"] is None
    assert match_b["score"] is None


def test_ranking_candidate_a_does_not_affect_b(db_path, monkeypatch):
    a = cand_repo.save_candidate({"name": "Candidate A"}, ["IT Manager"], [])
    b = cand_repo.save_candidate({"name": "Candidate B"}, ["Nurse"], [])
    job_repo.upsert_job(JOB)
    jid = job_repo.list_job_ids()[0]

    monkeypatch.setattr("jobagent.ranking.score_job", lambda job, config: (88, "fit for A"))
    run_ranking([job_repo.get_job(jid)], {"search": {}, "profile": {"name": "A"}}, candidate_id=a)
    match_repo.update_match_status(a, jid, "reviewed", "A reviewed")

    match_a = match_repo.get_match(a, jid)
    match_b = match_repo.get_match(b, jid)
    assert match_a["score"] == 88
    assert match_a["status"] == "reviewed"
    assert match_b["score"] is None
    assert match_b["status"] == "new"


def test_reupsert_same_job_does_not_duplicate_matches(db_path):
    cand_repo.save_candidate({"name": "Candidate A"}, ["IT Manager"], [])
    cand_repo.save_candidate({"name": "Candidate B"}, ["Nurse"], [])
    assert job_repo.upsert_job(JOB) is True
    jid = job_repo.list_job_ids()[0]
    assert match_repo.count_all_matches() == 2

    assert job_repo.upsert_job(dict(JOB, title="Systems Engineer (updated)")) is False
    assert job_repo.count_jobs() == 1
    assert job_repo.list_job_ids() == [jid]
    assert match_repo.count_all_matches() == 2
    assert job_repo.get_job(jid)["title"] == "Systems Engineer"
