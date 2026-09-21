from __future__ import annotations

from jobagent.db import candidates as cand_repo
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo
from jobagent.evaluate import evaluate_database, format_evaluation


def test_evaluate_is_read_only(db_path):
    a = cand_repo.save_candidate({"name": "Ada", "search_enabled": 1}, ["SRE"], [])
    job_repo.upsert_job(
        {"title": "SRE", "company": "Acme", "url": "https://example.com/sre-eval", "source": "remotive"}
    )
    jid = job_repo.list_job_ids()[0]
    match_repo.update_match_score(a, jid, 88, "strong", rank_provider="mock", rank_model="mock")
    before_jobs = job_repo.count_jobs()
    before_matches = match_repo.count_all_matches()
    report = evaluate_database(top_n=5)
    text = format_evaluation(report)
    assert job_repo.count_jobs() == before_jobs
    assert match_repo.count_all_matches() == before_matches
    assert report["catalog_jobs"] == 1
    person = report["candidates"][0]
    assert person["matches"] == 1
    assert person["score_distribution"]["70-89"] == 1
    assert person["top_ranked"][0]["score"] == 88
    assert "Ada" in text
    assert "88" in text
