from __future__ import annotations

from jobagent import AUTO_APPLY_ENABLED
from jobagent.commute import classify_job, detect_work_type
from jobagent.pipeline import run_candidate_cycle


def test_auto_apply_is_hard_disabled():
    assert AUTO_APPLY_ENABLED is False


def test_detect_remote_without_network():
    job = {"title": "SRE", "location": "Remote, USA", "description": "Fully remote team"}
    assert detect_work_type(job) == "remote"
    result = classify_job(
        job,
        {"profile": {"location": "Fort Smith, AR"}, "search": {}},
    )
    assert result["work_type"] == "remote"
    assert result["action"] == "auto_apply"
    assert result["commute_minutes"] is None


def test_candidate_cycle_never_applies(db_path, monkeypatch):
    from jobagent.db import candidates as cand_repo
    from jobagent.db import jobs as job_repo

    cid = cand_repo.save_candidate(
        {"name": "Ada", "location": "Fort Smith, AR", "resume_text": "ops"},
        ["SRE"],
        [],
    )
    cand = cand_repo.get_candidate(cid)
    job_repo.upsert_job(
        {
            "title": "SRE",
            "company": "Acme",
            "url": "https://example.com/sre",
            "source": "test",
            "description": "remote linux",
        }
    )

    monkeypatch.setattr("jobagent.ranking.score_job", lambda job, config: (90, "fit"))
    monkeypatch.setattr(
        "jobagent.pipeline.classify_job",
        lambda job, config: {
            "work_type": "remote",
            "commute_minutes": None,
            "action": "auto_apply",
            "commute_note": "Fully remote — no commute",
        },
    )
    result = run_candidate_cycle(cand, {"scheduler": {"max_rank_per_run": 10}, "search": {"min_match_score": 50}})
    assert result["applied"] == 0
    assert result["auto_apply_eligible"] >= 0
