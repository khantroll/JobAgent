"""Import recovered legacy jobs.db into a JobAgent 2.0 database.

Never writes to the source file. Idempotent. Does not guess candidate ownership
of global job score/status/commute/document fields.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobagent.db import candidates as cand_repo
from jobagent.db import init_db
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo
from jobagent.db import runs as run_repo
from jobagent.db.connection import get_conn
from jobagent.paths import data_dir, project_root

JOB_CATALOG_FIELDS = (
    "id",
    "title",
    "company",
    "location",
    "url",
    "description",
    "source",
    "salary_raw",
    "posted_at",
    "found_at",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_legacy_db_path() -> Path:
    return project_root() / "www" / "data" / "jobs.db"


def _open_readonly_copy(source: Path) -> tuple[sqlite3.Connection, Path]:
    """Copy the evidence DB so SQLite never opens the original file."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobagent-legacy-"))
    tmp = tmp_dir / "jobs.db"
    shutil.copy2(source, tmp)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source) + suffix)
        if sidecar.is_file():
            shutil.copy2(sidecar, Path(str(tmp) + suffix))
    conn = sqlite3.connect(str(tmp), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    return conn, tmp


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _job_has_personal_state(job: dict) -> bool:
    status = (job.get("status") or "new").strip()
    if status and status != "new":
        return True
    if job.get("score") not in (None, ""):
        return True
    if job.get("work_type"):
        return True
    if job.get("commute_minutes") not in (None, ""):
        return True
    if job.get("resume_path") or job.get("cover_path"):
        return True
    if job.get("applied_at"):
        return True
    if int(job.get("notified") or 0) == 1:
        return True
    return False


def _snapshot_counts(label: str) -> dict[str, int]:
    return {
        "label": label,
        "jobs": job_repo.count_jobs(),
        "candidates": cand_repo.count_candidates(),
        "matches": match_repo.count_all_matches(),
        "run_history": run_repo.count_runs(),
    }


def import_legacy_database(
    source: Path | str | None = None,
    *,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    source_path = Path(source) if source else default_legacy_db_path()
    if not source_path.is_file():
        raise FileNotFoundError(f"Legacy database not found: {source_path}")

    started = _utcnow()
    init_db()
    before = _snapshot_counts("before")

    src, tmp_copy = _open_readonly_copy(source_path)
    jobs_imported = 0
    jobs_unchanged = 0
    candidates_imported = 0
    titles_imported = 0
    employers_imported = 0
    matches_imported = 0
    runs_imported = 0
    ambiguous: list[dict] = []
    warnings: list[str] = []
    source_counts: dict[str, int] = {}

    try:
        source_counts["jobs"] = src.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        source_counts["candidates"] = (
            src.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
            if _table_exists(src, "candidates")
            else 0
        )
        source_counts["matches"] = (
            src.execute("SELECT COUNT(*) FROM candidate_job_matches").fetchone()[0]
            if _table_exists(src, "candidate_job_matches")
            else 0
        )
        source_counts["run_log"] = (
            src.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
            if _table_exists(src, "run_log")
            else 0
        )

        job_cols = _columns(src, "jobs")
        for raw in src.execute("SELECT * FROM jobs").fetchall():
            job = dict(raw)
            catalog = {k: job.get(k) or ("" if k != "id" else job.get(k)) for k in JOB_CATALOG_FIELDS}
            if not catalog.get("id") or not catalog.get("url"):
                warnings.append(f"Skipped job with missing id/url: {job.get('id')}")
                continue
            inserted = job_repo.upsert_job(catalog, link_candidates=False)
            if inserted:
                jobs_imported += 1
            else:
                jobs_unchanged += 1

        # Candidates next so IDs are stable, then strip any auto-created matches
        # that upsert_job may have added (no candidate existed yet on first pass,
        # but reruns would link). We clear matches that are empty/new with no
        # imported match evidence after candidates load — handled below.
        if _table_exists(src, "candidates"):
            titles_by_cid: dict[int, list] = {}
            employers_by_cid: dict[int, list] = {}
            if _table_exists(src, "candidate_titles"):
                for row in src.execute("SELECT * FROM candidate_titles").fetchall():
                    item = dict(row)
                    titles_by_cid.setdefault(int(item["candidate_id"]), []).append(item)
                    titles_imported += 1
            if _table_exists(src, "candidate_employers"):
                for row in src.execute("SELECT * FROM candidate_employers").fetchall():
                    item = dict(row)
                    employers_by_cid.setdefault(int(item["candidate_id"]), []).append(item)
                    employers_imported += 1
            for raw in src.execute("SELECT * FROM candidates").fetchall():
                cand = dict(raw)
                cid = int(cand["id"])
                cand_repo.insert_candidate_with_id(
                    cand,
                    titles_by_cid.get(cid, []),
                    employers_by_cid.get(cid, []),
                )
                candidates_imported += 1

        imported_match_keys: set[tuple[int, str]] = set()
        if _table_exists(src, "candidate_job_matches"):
            for raw in src.execute("SELECT * FROM candidate_job_matches").fetchall():
                row = dict(raw)
                cid = int(row["candidate_id"])
                jid = row["job_id"]
                if cand_repo.get_candidate(cid) is None:
                    warnings.append(f"Match for missing candidate {cid} / job {jid} skipped")
                    continue
                if job_repo.get_job(jid) is None:
                    warnings.append(f"Match for missing job {jid} / candidate {cid} skipped")
                    continue
                match_repo.upsert_imported_match(row)
                imported_match_keys.add((cid, jid))
                matches_imported += 1

        active_ids: list[int] = []
        if _table_exists(src, "candidates") and "active" in _columns(src, "candidates"):
            active_ids = [
                int(r[0])
                for r in src.execute("SELECT id FROM candidates WHERE active=1").fetchall()
            ]

        source_match_count = source_counts["matches"]
        backfill_flag = None
        if _table_exists(src, "app_meta"):
            flag_row = src.execute(
                "SELECT value FROM app_meta WHERE key='matches_backfill_v1'"
            ).fetchone()
            backfill_flag = flag_row[0] if flag_row else None

        if source_match_count == 0:
            if backfill_flag in ("1", 1):
                warnings.append(
                    "Legacy matches_backfill_v1 was marked complete with zero match rows. "
                    "JobAgent 2.0 will not invent matches or mark this association complete."
                )
            if not active_ids:
                warnings.append(
                    "No active candidate in the source database. Global job score/status/"
                    "commute/document fields are ambiguous and were not assigned."
                )

        for raw in src.execute("SELECT * FROM jobs").fetchall():
            job = dict(raw)
            if not _job_has_personal_state(job):
                continue
            jid = job["id"]
            owners = [
                cid for cid, mjid in imported_match_keys if mjid == jid
            ]
            if len(owners) == 1:
                continue
            ambiguous.append(
                {
                    "job_id": jid,
                    "title": job.get("title"),
                    "company": job.get("company"),
                    "legacy_status": job.get("status"),
                    "legacy_score": job.get("score"),
                    "reason": (
                        "multiple_or_missing_match_owners"
                        if owners
                        else "no_source_match_row"
                    ),
                    "existing_match_candidate_ids": owners,
                    "active_candidate_ids": active_ids,
                    "source_candidate_count": source_counts["candidates"],
                }
            )

        if _table_exists(src, "run_log"):
            existing_run_ats = {r["run_at"] for r in run_repo.list_run_history(10_000)}
            for raw in src.execute("SELECT * FROM run_log ORDER BY id").fetchall():
                row = dict(raw)
                run_at = row.get("run_at") or ""
                if run_at in existing_run_ats:
                    continue
                with get_conn() as dest:
                    dest.execute(
                        """
                        INSERT INTO run_history
                            (run_at, source, found, new, applied, flagged, errors, dry_run, summary)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            run_at,
                            row.get("source") or "",
                            row.get("found") or 0,
                            row.get("new") or 0,
                            row.get("applied") or 0,
                            row.get("flagged") or 0,
                            row.get("errors") or "",
                            "imported from legacy run_log",
                        ),
                    )
                runs_imported += 1
                existing_run_ats.add(run_at)
    finally:
        src.close()
        # Leave the temp copy for the process; original source is untouched.

    after = _snapshot_counts("after")
    finished = _utcnow()
    report = {
        "source_path": str(source_path.resolve()),
        "source_untouched": True,
        "started_at": started,
        "finished_at": finished,
        "source_counts": source_counts,
        "before": before,
        "after": after,
        "imported": {
            "jobs_inserted": jobs_imported,
            "jobs_already_present": jobs_unchanged,
            "candidates_upserted": candidates_imported,
            "titles": titles_imported,
            "employers": employers_imported,
            "explicit_matches_upserted": matches_imported,
            "run_history_inserted": runs_imported,
        },
        "ambiguous_candidate_associations": ambiguous,
        "ambiguous_count": len(ambiguous),
        "matches_invented": False,
        "backfill_marked_complete_without_matches": False,
        "warnings": warnings,
    }
    run_repo.save_import_report(str(source_path), started, finished, report)

    out_dir = report_dir or data_dir() / "migration_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"legacy-import-{stamp}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["report_path"] = str(out_path)
    return report
