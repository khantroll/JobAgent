from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jobagent.db import candidates as cand_repo
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo


def _match():
    cid = cand_repo.save_candidate({"name": "Candidate"}, ["Administrator"], [])
    job_repo.upsert_job({"title": "Administrator", "company": "Example", "url": "https://example.test/job", "source": "test"})
    jid = job_repo.list_job_ids()[0]
    match_repo.link_job_to_candidate(jid, cid)
    return cid, jid


def test_application_tracking_records_stage_and_preserves_candidate_scope(db_path):
    cid, jid = _match()
    other = cand_repo.save_candidate({"name": "Other"}, ["Administrator"], [])
    match_repo.link_job_to_candidate(jid, other)
    assert match_repo.update_application_tracking(
        cid, jid, stage="interview", applied_at="2026-09-27T10:00",
        channel="Company site", follow_up_at="2026-09-30T09:00", notes="Phone screen complete",
    )
    mine = match_repo.get_match(cid, jid)
    theirs = match_repo.get_match(other, jid)
    assert mine["application_stage"] == "interview"
    assert mine["application_channel"] == "Company site"
    assert mine["application_notes"] == "Phone screen complete"
    assert mine["status"] == "applied"
    assert theirs["application_stage"] == "not_applied"
    assert theirs["application_notes"] == ""


def test_terminal_outcome_and_follow_up_dashboard(db_path):
    cid, jid = _match()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    match_repo.update_application_tracking(cid, jid, stage="applied", follow_up_at=past)
    stats = match_repo.dashboard_stats(cid)
    assert stats["follow_up_due"] == 1
    assert stats["by_application_stage"]["applied"] == 1

    match_repo.update_application_tracking(cid, jid, stage="rejected", notes="Closed")
    row = match_repo.get_match(cid, jid)
    assert row["status"] == "rejected"
    assert row["outcome_at"]
    assert match_repo.dashboard_stats(cid)["follow_up_due"] == 0


def test_application_tracking_rejects_unknown_stage(db_path):
    cid, jid = _match()
    with pytest.raises(ValueError, match="stage"):
        match_repo.update_application_tracking(cid, jid, stage="teleported")
