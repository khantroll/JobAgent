from __future__ import annotations

from jobagent.db import candidates as cand_repo
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo
from jobagent.ranking import run_ranking


def _two_candidates_one_job(db_path):
    a = cand_repo.save_candidate({"name": "Candidate A", "location": "Fort Smith, AR"}, ["IT Manager"], [])
    b = cand_repo.save_candidate({"name": "Candidate B", "location": "Austin, TX"}, ["Nurse"], [])
    job_repo.upsert_job(
        {
            "title": "Systems Engineer",
            "company": "City Hall",
            "url": "https://example.com/jobs/syseng",
            "source": "test",
            "location": "Fort Smith, AR",
            "description": "VMware and Active Directory",
        }
    )
    jid = job_repo.list_job_ids()[0]
    return a, b, jid


def test_candidate_a_status_docs_and_application_do_not_affect_b(db_path):
    a, b, jid = _two_candidates_one_job(db_path)
    match_a = match_repo.get_match(a, jid)
    match_b = match_repo.get_match(b, jid)
    assert match_a and match_b
    assert match_a["status"] == "new"
    assert match_b["status"] == "new"

    match_repo.update_match_score(a, jid, 91, "Strong infrastructure fit")
    match_repo.update_match_commute(
        a,
        jid,
        work_type="onsite",
        commute_minutes=12,
        commute_note="12 min drive",
        commute_result="auto_apply",
    )
    match_repo.update_match_documents(a, jid, "/tmp/a-resume.txt", "/tmp/a-cover.txt")
    match_repo.update_match_status(a, jid, "applied", "Recorded manually")

    match_a = match_repo.get_match(a, jid)
    match_b = match_repo.get_match(b, jid)
    job = job_repo.get_job(jid)

    assert match_a["score"] == 91
    assert match_a["status"] == "applied"
    assert match_a["application_state"] == "recorded"
    assert match_a["resume_path"] == "/tmp/a-resume.txt"
    assert match_a["cover_path"] == "/tmp/a-cover.txt"
    assert match_a["auto_apply_eligible"] == 1
    assert match_a["commute_minutes"] == 12

    assert match_b["score"] is None
    assert match_b["status"] == "new"
    assert match_b["application_state"] == "none"
    assert match_b["resume_path"] is None
    assert match_b["cover_path"] is None
    assert match_b["auto_apply_eligible"] == 0
    assert match_b["commute_minutes"] is None
    assert match_b["applied_at"] is None

    assert job.get("status") is None
    assert "resume_path" not in job


def test_ranking_writes_only_to_the_requested_candidate(db_path, monkeypatch):
    a, b, jid = _two_candidates_one_job(db_path)

    def fake_score(job, config):
        return 77, "mocked"

    monkeypatch.setattr("jobagent.ranking.score_job", fake_score)
    run_ranking([job_repo.get_job(jid)], {"search": {}, "profile": {"name": "A"}}, candidate_id=a)

    assert match_repo.get_match(a, jid)["score"] == 77
    assert match_repo.get_match(b, jid)["score"] is None


def test_ranking_persists_when_given_unscored_match_rows(db_path, monkeypatch):
    """Joined match rows use integer ``id`` for the match PK; ranking must use job_id."""
    a, b, jid = _two_candidates_one_job(db_path)
    monkeypatch.setattr("jobagent.ranking.score_job", lambda job, config: (81, "from match row"))
    rows = match_repo.get_unscored_matches(a)
    assert rows and rows[0]["id"] != jid
    assert rows[0]["job_id"] == jid
    run_ranking(rows, {"search": {}, "profile": {"name": "A"}}, candidate_id=a)
    match_a = match_repo.get_match(a, jid)
    assert match_a["score"] == 81
    assert match_a["ranked_at"]
    assert match_a["rank_provider"] == "test"
    assert match_repo.get_match(b, jid)["score"] is None


def test_commute_skip_is_per_candidate(db_path):
    a, b, jid = _two_candidates_one_job(db_path)
    match_repo.update_match_commute(
        a,
        jid,
        work_type="onsite",
        commute_minutes=180,
        commute_note="too far",
        commute_result="skip",
    )
    assert match_repo.get_match(a, jid)["status"] == "ignored"
    assert match_repo.get_match(b, jid)["status"] == "new"
