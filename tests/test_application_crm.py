from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jobagent.db import applications as app_repo
from jobagent.db import candidates as cand_repo
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo


def fixture_match():
    cid = cand_repo.save_candidate({"name": "Candidate"}, ["Admin"], [])
    job_repo.upsert_job({"title": "Admin", "company": "Example", "url": "https://example.test/crm", "source": "test"})
    jid = job_repo.list_job_ids()[0]
    match_repo.link_job_to_candidate(jid, cid)
    return cid, jid


def test_stage_history_and_transition_guards(db_path):
    cid, jid = fixture_match()
    match_repo.update_application_tracking(cid, jid, stage="applied")
    match_repo.update_application_tracking(cid, jid, stage="screening", notes="Recruiter call")
    match_repo.update_application_tracking(cid, jid, stage="interview")
    match_repo.update_application_tracking(cid, jid, stage="offer")
    match_repo.update_application_tracking(cid, jid, stage="accepted")
    events = app_repo.list_events(cid, jid)
    assert [e["to_stage"] for e in reversed(events)] == ["applied", "screening", "interview", "offer", "accepted"]
    with pytest.raises(ValueError, match="terminal"):
        match_repo.update_application_tracking(cid, jid, stage="interview")


def test_offer_is_not_terminal_and_rejection_is(db_path):
    cid, jid = fixture_match()
    match_repo.update_application_tracking(cid, jid, stage="applied")
    match_repo.update_application_tracking(cid, jid, stage="offer")
    assert match_repo.get_match(cid, jid)["outcome_at"] is None
    match_repo.update_application_tracking(cid, jid, stage="declined_offer")
    assert match_repo.get_match(cid, jid)["outcome_at"]


def test_contacts_are_candidate_job_scoped(db_path):
    cid, jid = fixture_match()
    contact_id = app_repo.add_contact(cid, jid, name="Recruiter", role="Talent", email="r@example.test")
    contacts = app_repo.list_contacts(cid, jid)
    assert contacts[0]["email"] == "r@example.test"
    assert app_repo.delete_contact(cid, jid, contact_id)
    assert app_repo.list_contacts(cid, jid) == []


def test_follow_up_queues_and_next_action(db_path):
    cid, jid = fixture_match()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    match_repo.update_application_tracking(cid, jid, stage="applied", follow_up_at=past, next_action="Email recruiter")
    due, total = match_repo.list_matches(cid, follow_up="due")
    assert total == 1
    assert due[0]["next_action"] == "Email recruiter"
    match_repo.update_application_tracking(cid, jid, stage="rejected")
    due, total = match_repo.list_matches(cid, follow_up="due")
    assert total == 0
