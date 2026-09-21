from __future__ import annotations

import sqlite3

import pytest

from jobagent.db import candidates as cand_repo
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo
from jobagent.db.connection import connect
from jobagent.db.migrate import apply_migrations, available_migrations


JOB_FORBIDDEN_COLUMNS = {
    "score",
    "score_reason",
    "work_type",
    "commute_minutes",
    "commute_note",
    "status",
    "resume_path",
    "cover_path",
    "applied_at",
    "notified",
    "username",
    "auto_apply_eligible",
    "application_state",
}


def test_migrations_are_deterministic_and_idempotent(db_path):
    versions = [p.stem for p in available_migrations()]
    assert versions == ["001_initial", "002_ranking_and_search"]
    assert apply_migrations(db_path) == []
    conn = connect(db_path)
    try:
        applied = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert applied == ["001_initial", "002_ranking_and_search"]
    finally:
        conn.close()


def test_repeated_init_db_does_not_reapply_001_or_alter_data(db_path):
    """schema_migrations tracks 001; a second init-db must be a no-op."""
    cid = cand_repo.save_candidate({"name": "Ada Lovelace", "email": "ada@example.com"}, ["Engineer"], [])
    inserted = job_repo.upsert_job(
        {
            "title": "SRE",
            "company": "Acme",
            "url": "https://example.com/jobs/sre-init",
            "source": "test",
        },
        link_candidates=False,
    )
    assert inserted is True
    conn = connect(db_path)
    try:
        before_jobs = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        before_cands = conn.execute("SELECT id, name, email FROM candidates ORDER BY id").fetchall()
        before_matches = conn.execute("SELECT * FROM candidate_job_matches").fetchall()
        before_migrations = conn.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
    finally:
        conn.close()
    assert len(before_migrations) == 2
    assert [row[0] for row in before_migrations] == ["001_initial", "002_ranking_and_search"]

    assert apply_migrations(db_path) == []
    from jobagent.db import init_db

    assert init_db(db_path) == []

    conn = connect(db_path)
    try:
        after_jobs = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        after_cands = conn.execute("SELECT id, name, email FROM candidates ORDER BY id").fetchall()
        after_matches = conn.execute("SELECT * FROM candidate_job_matches").fetchall()
        after_migrations = conn.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
    finally:
        conn.close()

    assert after_migrations == before_migrations
    assert after_jobs == before_jobs
    assert after_cands == before_cands
    assert after_matches == before_matches
    assert cand_repo.get_candidate(cid)["name"] == "Ada Lovelace"


def test_jobs_table_has_no_candidate_specific_columns(db_path):
    conn = connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    finally:
        conn.close()
    assert JOB_FORBIDDEN_COLUMNS.isdisjoint(cols)
    assert {"id", "title", "company", "url", "found_at"} <= cols


def test_foreign_keys_are_enforced(db_path):
    conn = connect(db_path)
    try:
        enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert enabled == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO candidate_job_matches
                    (candidate_id, job_id, status, status_reason, matched_at, status_updated_at, updated_at)
                VALUES (999, 'missing-job', 'new', '', 't', 't', 't')
                """
            )
            conn.commit()
    finally:
        conn.close()


def test_deleting_candidate_cascades_matches_not_jobs(db_path):
    cid = cand_repo.save_candidate({"name": "Ada"}, ["Engineer"], [])
    job_repo.upsert_job(
        {
            "title": "SRE",
            "company": "Acme",
            "url": "https://example.com/jobs/sre",
            "source": "test",
        }
    )
    assert match_repo.count_matches(cid) == 1
    assert job_repo.count_jobs() == 1
    cand_repo.delete_candidate(cid)
    assert match_repo.count_all_matches() == 0
    assert job_repo.count_jobs() == 1


def test_unique_candidate_job_match(db_path):
    cid = cand_repo.save_candidate({"name": "Ada"}, [], [])
    job_repo.upsert_job(
        {"title": "SRE", "company": "Acme", "url": "https://example.com/jobs/sre", "source": "test"},
        link_candidates=False,
    )
    jid = job_repo.list_job_ids()[0]
    assert match_repo.link_job_to_candidate(jid, cid) is True
    assert match_repo.link_job_to_candidate(jid, cid) is False
    assert match_repo.count_matches(cid) == 1
