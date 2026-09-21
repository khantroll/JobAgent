"""Shared job catalog. Candidate-specific fields must never be stored here."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from jobagent.db.connection import get_conn, row_to_dict

JOB_SORT_FIELDS = frozenset({"found_at", "posted_at", "title", "company", "source"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def upsert_job(job: dict, *, link_candidates: bool = True) -> bool:
    """Insert a shared listing. Returns True if a new row was created.

    Newly inserted jobs are linked to every **search-enabled** candidate as a blank
    match unless ``link_candidates`` is False (legacy import). Candidates with
    ``search_enabled=0`` are not auto-linked.

    Re-upserting an existing URL is a no-op for both the catalog row and matches:
    INSERT OR IGNORE does not update the job, and linking runs only on insert.
    Ranking one candidate's match never writes to another candidate's row.
    """
    jid = job.get("id") or job_id(job["url"])
    now = _utcnow()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO jobs
                (id, title, company, location, url, description,
                 source, salary_raw, posted_at, found_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                jid,
                job.get("title") or "",
                job.get("company") or "Unknown",
                job.get("location") or "",
                job["url"],
                job.get("description") or "",
                job.get("source") or "",
                job.get("salary_raw") or "",
                job.get("posted_at") or "",
                job.get("found_at") or now,
                now,
                now,
            ),
        )
        inserted = cur.rowcount > 0
    if inserted and link_candidates:
        from jobagent.db import matches as match_repo

        match_repo.link_job_to_all_candidates(jid)
    return inserted


def get_job(jid: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return row_to_dict(row)


def count_jobs() -> int:
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])


def list_jobs(
    *,
    source: str | None = None,
    q: str | None = None,
    sort_by: str = "found_at",
    sort_dir: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    if sort_by not in JOB_SORT_FIELDS:
        sort_by = "found_at"
    direction = "ASC" if sort_dir == "asc" else "DESC"
    clauses: list[str] = ["1=1"]
    params: list = []
    if source:
        clauses.append("source = ?")
        params.append(source)
    if q:
        clauses.append("(title LIKE ? OR company LIKE ? OR location LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    where = " AND ".join(clauses)
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT * FROM jobs WHERE {where}
                ORDER BY {sort_by} {direction}, id
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        return [row_to_dict(r) for r in rows], int(total)


def list_job_ids() -> list[str]:
    with get_conn() as conn:
        return [row[0] for row in conn.execute("SELECT id FROM jobs").fetchall()]


def list_sources() -> list[str]:
    with get_conn() as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT source FROM jobs WHERE source IS NOT NULL AND source != '' ORDER BY source"
            ).fetchall()
        ]
