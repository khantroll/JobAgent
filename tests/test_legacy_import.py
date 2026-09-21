from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from jobagent.db import candidates as cand_repo
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo
from jobagent.db.connection import set_database_path
from jobagent.db import init_db
from jobagent.migrate_legacy import import_legacy_database
from jobagent.paths import project_root


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _build_failed_backfill_db(path: Path) -> None:
    """Recreate the June 25 edge case: jobs exist, candidates inactive, zero matches, flag set."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT,
            url TEXT NOT NULL,
            description TEXT,
            source TEXT,
            salary_raw TEXT,
            posted_at TEXT,
            found_at TEXT NOT NULL,
            score INTEGER,
            score_reason TEXT,
            work_type TEXT,
            commute_minutes INTEGER,
            commute_note TEXT,
            status TEXT DEFAULT 'new',
            resume_path TEXT,
            cover_path TEXT,
            applied_at TEXT,
            notified INTEGER DEFAULT 0,
            username TEXT NOT NULL DEFAULT 'default'
        );
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            location TEXT DEFAULT '',
            linkedin TEXT DEFAULT '',
            github TEXT DEFAULT '',
            resume_text TEXT DEFAULT '',
            resume_file TEXT DEFAULT '',
            active INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE candidate_titles (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER NOT NULL,
            title TEXT NOT NULL
        );
        CREATE TABLE candidate_job_matches (
            candidate_id INTEGER NOT NULL,
            job_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            status_reason TEXT DEFAULT '',
            matched_at TEXT NOT NULL,
            status_updated_at TEXT NOT NULL,
            PRIMARY KEY (candidate_id, job_id)
        );
        CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE run_log (
            id INTEGER PRIMARY KEY,
            run_at TEXT NOT NULL,
            source TEXT,
            found INTEGER,
            new INTEGER,
            applied INTEGER,
            flagged INTEGER,
            errors TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO candidates (id,name,email,active,created_at,updated_at) VALUES (1,'Test User','t@t.com',0,'t','t')"
    )
    conn.execute(
        "INSERT INTO candidates (id,name,email,active,created_at,updated_at) VALUES (2,'Jeff','a@b.com',0,'t','t')"
    )
    conn.execute("INSERT INTO candidate_titles (candidate_id, title) VALUES (1, 'IT Manager')")
    conn.execute("INSERT INTO candidate_titles (candidate_id, title) VALUES (2, 'IT')")
    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES ('matches_backfill_v1', '1')"
    )
    conn.execute(
        """
        INSERT INTO jobs (id,title,company,url,found_at,status,score,work_type,resume_path,applied_at,notified)
        VALUES ('job-applied','DevOps Engineer','Unknown','https://example.com/applied','2026-05-29',
                'applied',65,'remote','/tmp/resume.txt','2026-05-29',1)
        """
    )
    conn.execute(
        """
        INSERT INTO jobs (id,title,company,url,found_at,status,score)
        VALUES ('job-new','Analyst','Acme','https://example.com/new','2026-06-01','new',80)
        """
    )
    conn.execute(
        """
        INSERT INTO jobs (id,title,company,url,found_at,status)
        VALUES ('job-plain','Clerk','Acme','https://example.com/plain','2026-06-02','new')
        """
    )
    conn.execute(
        "INSERT INTO run_log (run_at,source,found,new,applied,flagged) VALUES ('2026-05-29','all',0,0,1,0)"
    )
    conn.commit()
    conn.close()


def test_failed_legacy_backfill_is_not_reproduced(tmp_path, monkeypatch, db_path):
    source = tmp_path / "legacy.db"
    _build_failed_backfill_db(source)
    before_hash = _sha256(source)

    report = import_legacy_database(source, report_dir=tmp_path / "reports")

    assert _sha256(source) == before_hash
    assert source.is_file()
    assert job_repo.count_jobs() == 3
    assert cand_repo.count_candidates() == 2
    assert match_repo.count_all_matches() == 0
    assert report["matches_invented"] is False
    assert report["backfill_marked_complete_without_matches"] is False
    assert report["ambiguous_count"] >= 2
    reasons = {row["job_id"]: row["reason"] for row in report["ambiguous_candidate_associations"]}
    assert reasons["job-applied"] == "no_source_match_row"
    assert reasons["job-new"] == "no_source_match_row"
    assert "job-plain" not in reasons
    assert any("matches_backfill_v1" in w for w in report["warnings"])
    assert any("No active candidate" in w for w in report["warnings"])
    assert match_repo.get_match(1, "job-applied") is None
    assert match_repo.get_match(2, "job-applied") is None

    report2 = import_legacy_database(source, report_dir=tmp_path / "reports")
    assert job_repo.count_jobs() == 3
    assert cand_repo.count_candidates() == 2
    assert match_repo.count_all_matches() == 0
    assert report2["imported"]["jobs_inserted"] == 0
    assert _sha256(source) == before_hash


def test_recovered_jobs_db_import_when_present(tmp_path, monkeypatch):
    source = project_root() / "www" / "data" / "jobs.db"
    if not source.is_file():
        pytest.skip("recovered www/data/jobs.db not present")
    dest = tmp_path / "jobagent.db"
    set_database_path(dest)
    monkeypatch.setenv("JOBAGENT_DATABASE_PATH", str(dest))
    init_db(dest)
    before_hash = _sha256(source)
    before_size = source.stat().st_size

    report = import_legacy_database(source, report_dir=tmp_path / "reports")
    assert source.stat().st_size == before_size
    assert _sha256(source) == before_hash
    # Canonical post-import semantics for the frozen June 25 recovered DB.
    # The 299 match rows that once sat on candidate 1 were smoke-test state, not history.
    assert report["after"]["jobs"] == 299
    assert report["after"]["candidates"] == 2
    assert report["after"]["matches"] == 0
    assert report["after"]["run_history"] == 7
    assert report["source_counts"]["jobs"] == 299
    assert report["source_counts"]["candidates"] == 2
    assert report["source_counts"]["matches"] == 0
    assert report["source_counts"]["run_log"] == 7
    assert report["imported"]["explicit_matches_upserted"] == 0
    assert report["matches_invented"] is False
    assert report["ambiguous_count"] == 119
    assert match_repo.count_all_matches() == 0
    assert job_repo.count_jobs() == 299
    assert cand_repo.count_candidates() == 2

    report2 = import_legacy_database(source, report_dir=tmp_path / "reports")
    assert report2["after"]["jobs"] == 299
    assert report2["after"]["candidates"] == 2
    assert report2["after"]["matches"] == 0
    assert report2["after"]["run_history"] == 7
    assert report2["imported"]["jobs_inserted"] == 0
    assert report2["imported"]["explicit_matches_upserted"] == 0
    assert match_repo.count_all_matches() == 0
    assert _sha256(source) == before_hash
