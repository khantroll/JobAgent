from __future__ import annotations

from jobagent.db import candidates as cand_repo
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo


def test_dashboard_stats_include_package_queue(db_path):
    cid = cand_repo.save_candidate(
        {"name": "Candidate", "resume_text": "Experience", "search_enabled": 1},
        ["Systems Administrator"],
        [],
    )
    for suffix in ("needed", "ready"):
        job_repo.upsert_job(
            {
                "title": f"Systems Administrator {suffix}",
                "company": "Example",
                "url": f"https://example.test/{suffix}",
                "description": "Role",
                "source": "test",
            }
        )
    job_ids = job_repo.list_job_ids()
    for jid in job_ids:
        match_repo.link_job_to_candidate(jid, cid)
        match_repo.update_match_status(cid, jid, "reviewed")
    match_repo.update_match_documents(cid, job_ids[0], "/tmp/resume.txt", "/tmp/cover.txt")

    stats = match_repo.dashboard_stats(cid)
    assert stats["package_ready"] == 1
    assert stats["package_needed"] == 1


    needed, needed_total = match_repo.list_matches(cid, package_state="needed")
    ready, ready_total = match_repo.list_matches(cid, package_state="ready")
    assert needed_total == 1
    assert ready_total == 1
    assert needed[0]["job_id"] != ready[0]["job_id"]
