"""Per-candidate job matches. All candidate-specific job state lives here."""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from jobagent.db.connection import get_conn, row_to_dict

MATCH_STATUSES = frozenset({"new", "reviewed", "applied", "rejected", "ignored"})
REVIEW_STATUSES = frozenset({"pending", "needs_review", "skipped", "cleared"})
APPLICATION_STATES = frozenset({"none", "recorded", "failed"})
COMMUTE_RESULTS = frozenset({"auto_apply", "needs_review", "skip"})
MATCH_SORT_FIELDS = frozenset(
    {
        "score",
        "status",
        "matched_at",
        "status_updated_at",
        "found_at",
        "applied_at",
        "ranked_at",
        "title",
        "company",
        "source",
        "work_type",
    }
)
MATCH_DATE_FIELDS = frozenset({"matched_at", "status_updated_at", "found_at", "applied_at", "ranked_at"})

_COMPANY_SUFFIXES = (
    " inc",
    " llc",
    " ltd",
    " corp",
    " co",
    " company",
    " university",
    " college",
    " the",
)


def catalog_job_id(row: dict) -> str:
    """Return the shared catalog job hash.

    Joined match rows expose the hash as ``job_id`` while ``id`` is the match
    row primary key. Catalog rows from ``jobs`` use ``id`` as the hash.
    """
    value = row.get("job_id") or row.get("id")
    if value is None or str(value).strip() == "":
        raise KeyError("row has neither job_id nor id")
    return str(value)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_dup_part(text: str) -> str:
    s = (text or "").lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for suffix in _COMPANY_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


def job_dup_key(title: str, company: str) -> str:
    t = _norm_dup_part(title)
    c = _norm_dup_part(company)
    if not t or not c:
        return ""
    return f"{c}|{t}"


def annotate_match_duplicates(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        row.pop("is_duplicate", None)
        row.pop("duplicate_of_job_id", None)
        row.pop("duplicate_of_title", None)
        row.pop("duplicate_group_size", None)
        key = job_dup_key(row.get("title", ""), row.get("company", ""))
        if key:
            groups[key].append(row)
    for items in groups.values():
        if len(items) < 2:
            continue
        items.sort(
            key=lambda x: (
                -(x.get("score") if x.get("score") is not None else -1),
                x.get("matched_at") or "",
                x.get("job_id") or "",
            )
        )
        canon = items[0]
        canon["duplicate_group_size"] = len(items)
        for dup in items[1:]:
            dup["is_duplicate"] = True
            dup["duplicate_of_job_id"] = canon["job_id"]
            dup["duplicate_of_title"] = canon.get("title", "")
    return rows


def _match_select() -> str:
    return """
        SELECT m.*, j.title, j.company, j.location, j.url, j.source, j.salary_raw,
               j.posted_at, j.found_at, j.description
        FROM candidate_job_matches m
        JOIN jobs j ON j.id = m.job_id
    """


def get_match(candidate_id: int, job_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            _match_select() + " WHERE m.candidate_id=? AND m.job_id=?",
            (candidate_id, job_id),
        ).fetchone()
        return row_to_dict(row)


def count_matches(candidate_id: int, *, status: str | None = None) -> int:
    with get_conn() as conn:
        if status:
            if status not in MATCH_STATUSES:
                raise ValueError(f"status must be one of {sorted(MATCH_STATUSES)}")
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM candidate_job_matches WHERE candidate_id=? AND status=?",
                    (candidate_id, status),
                ).fetchone()[0]
            )
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM candidate_job_matches WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()[0]
        )


def count_all_matches() -> int:
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM candidate_job_matches").fetchone()[0])


def link_job_to_candidate(job_id: str, candidate_id: int, *, status: str = "new") -> bool:
    if status not in MATCH_STATUSES:
        raise ValueError(f"status must be one of {sorted(MATCH_STATUSES)}")
    now = _utcnow()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO candidate_job_matches
                (candidate_id, job_id, status, status_reason, matched_at,
                 status_updated_at, updated_at)
            VALUES (?, ?, ?, '', ?, ?, ?)
            ON CONFLICT(candidate_id, job_id) DO NOTHING
            """,
            (candidate_id, job_id, status, now, now, now),
        )
        return cur.rowcount > 0


def link_job_to_all_candidates(job_id: str) -> int:
    from jobagent.db.candidates import list_candidates, search_enabled_value

    added = 0
    for cand in list_candidates():
        if not search_enabled_value(cand):
            continue
        if link_job_to_candidate(job_id, cand["id"]):
            added += 1
    return added


def link_all_jobs_to_candidate(candidate_id: int) -> int:
    """Explicit operator action: attach every catalog job as a blank match."""
    now = _utcnow()
    added = 0
    with get_conn() as conn:
        jobs = conn.execute("SELECT id FROM jobs").fetchall()
        for (jid,) in jobs:
            cur = conn.execute(
                """
                INSERT INTO candidate_job_matches
                    (candidate_id, job_id, status, status_reason, matched_at,
                     status_updated_at, updated_at)
                VALUES (?, ?, 'new', '', ?, ?, ?)
                ON CONFLICT(candidate_id, job_id) DO NOTHING
                """,
                (candidate_id, jid, now, now, now),
            )
            added += cur.rowcount
    return added


def upsert_imported_match(row: dict) -> bool:
    """Import an existing legacy match row without guessing missing fields."""
    now = _utcnow()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO candidate_job_matches (
                candidate_id, job_id, score, score_reason, status, status_reason,
                work_type, commute_minutes, commute_note, commute_result,
                auto_apply_eligible, review_status, resume_path, cover_path,
                notified, notified_at, application_state, applied_at,
                application_result, application_error, matched_at,
                status_updated_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(candidate_id, job_id) DO UPDATE SET
                score=COALESCE(excluded.score, candidate_job_matches.score),
                score_reason=COALESCE(excluded.score_reason, candidate_job_matches.score_reason),
                status=excluded.status,
                status_reason=excluded.status_reason,
                work_type=COALESCE(excluded.work_type, candidate_job_matches.work_type),
                commute_minutes=COALESCE(excluded.commute_minutes, candidate_job_matches.commute_minutes),
                commute_note=COALESCE(excluded.commute_note, candidate_job_matches.commute_note),
                commute_result=COALESCE(excluded.commute_result, candidate_job_matches.commute_result),
                auto_apply_eligible=excluded.auto_apply_eligible,
                review_status=excluded.review_status,
                resume_path=COALESCE(excluded.resume_path, candidate_job_matches.resume_path),
                cover_path=COALESCE(excluded.cover_path, candidate_job_matches.cover_path),
                notified=excluded.notified,
                notified_at=COALESCE(excluded.notified_at, candidate_job_matches.notified_at),
                application_state=excluded.application_state,
                applied_at=COALESCE(excluded.applied_at, candidate_job_matches.applied_at),
                application_result=COALESCE(excluded.application_result, candidate_job_matches.application_result),
                application_error=COALESCE(excluded.application_error, candidate_job_matches.application_error),
                status_updated_at=excluded.status_updated_at,
                updated_at=excluded.updated_at
            """,
            (
                row["candidate_id"],
                row["job_id"],
                row.get("score"),
                row.get("score_reason"),
                row.get("status") or "new",
                row.get("status_reason") or "",
                row.get("work_type"),
                row.get("commute_minutes"),
                row.get("commute_note"),
                row.get("commute_result"),
                int(row.get("auto_apply_eligible") or 0),
                row.get("review_status") or "pending",
                row.get("resume_path"),
                row.get("cover_path"),
                int(row.get("notified") or 0),
                row.get("notified_at"),
                row.get("application_state") or "none",
                row.get("applied_at"),
                row.get("application_result"),
                row.get("application_error"),
                row.get("matched_at") or now,
                row.get("status_updated_at") or now,
                now,
            ),
        )
        return cur.rowcount > 0


def update_match_score(
    candidate_id: int,
    job_id: str,
    score: int,
    reason: str,
    *,
    rank_provider: str | None = None,
    rank_model: str | None = None,
) -> None:
    now = _utcnow()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE candidate_job_matches
            SET score=?, score_reason=?, ranked_at=?, rank_provider=?, rank_model=?,
                status_updated_at=?, updated_at=?
            WHERE candidate_id=? AND job_id=?
            """,
            (score, reason, now, rank_provider, rank_model, now, now, candidate_id, job_id),
        )


def update_match_commute(
    candidate_id: int,
    job_id: str,
    *,
    work_type: str,
    commute_minutes,
    commute_note: str,
    commute_result: str,
) -> None:
    if commute_result not in COMMUTE_RESULTS:
        raise ValueError(f"commute_result must be one of {sorted(COMMUTE_RESULTS)}")
    now = _utcnow()
    auto_eligible = 1 if commute_result == "auto_apply" else 0
    if commute_result == "skip":
        status = "ignored"
        review_status = "skipped"
        status_reason = commute_note
    elif commute_result == "needs_review":
        status = None
        review_status = "needs_review"
        status_reason = commute_note
    else:
        status = None
        review_status = "pending"
        status_reason = commute_note
    with get_conn() as conn:
        if status == "ignored":
            conn.execute(
                """
                UPDATE candidate_job_matches
                SET work_type=?, commute_minutes=?, commute_note=?, commute_result=?,
                    auto_apply_eligible=?, review_status=?, status=?, status_reason=?,
                    status_updated_at=?, updated_at=?
                WHERE candidate_id=? AND job_id=?
                """,
                (
                    work_type,
                    commute_minutes,
                    commute_note,
                    commute_result,
                    auto_eligible,
                    review_status,
                    status,
                    status_reason,
                    now,
                    now,
                    candidate_id,
                    job_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE candidate_job_matches
                SET work_type=?, commute_minutes=?, commute_note=?, commute_result=?,
                    auto_apply_eligible=?, review_status=?, status_reason=?,
                    status_updated_at=?, updated_at=?
                WHERE candidate_id=? AND job_id=?
                """,
                (
                    work_type,
                    commute_minutes,
                    commute_note,
                    commute_result,
                    auto_eligible,
                    review_status,
                    status_reason,
                    now,
                    now,
                    candidate_id,
                    job_id,
                ),
            )


def update_match_status(
    candidate_id: int,
    job_id: str,
    status: str,
    status_reason: str = "",
) -> bool:
    if status not in MATCH_STATUSES:
        raise ValueError(f"status must be one of {sorted(MATCH_STATUSES)}")
    now = _utcnow()
    extra_sql = ""
    extra_params: list = []
    if status == "applied":
        extra_sql = ", application_state='recorded', applied_at=COALESCE(applied_at, ?)"
        extra_params.append(now)
    with get_conn() as conn:
        cur = conn.execute(
            f"""
            UPDATE candidate_job_matches
            SET status=?, status_reason=?, status_updated_at=?, updated_at=?{extra_sql}
            WHERE candidate_id=? AND job_id=?
            """,
            (status, status_reason or "", now, now, *extra_params, candidate_id, job_id),
        )
        return cur.rowcount > 0


def update_match_documents(candidate_id: int, job_id: str, resume_path: str, cover_path: str) -> None:
    now = _utcnow()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE candidate_job_matches
            SET resume_path=?, cover_path=?, updated_at=?
            WHERE candidate_id=? AND job_id=?
            """,
            (resume_path, cover_path, now, candidate_id, job_id),
        )


def get_unscored_matches(candidate_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            _match_select()
            + " WHERE m.candidate_id=? AND m.score IS NULL AND m.status='new'",
            (candidate_id,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def get_matches_needing_commute(candidate_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            _match_select()
            + """
            WHERE m.candidate_id=? AND m.score IS NOT NULL
              AND m.work_type IS NULL AND m.status='new'
            """,
            (candidate_id,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def _date_bounds(date_from: str | None, date_to: str | None) -> tuple[str | None, str | None]:
    start = f"{date_from}T00:00:00" if date_from else None
    end = None
    if date_to:
        try:
            end_dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            end = end_dt.strftime("%Y-%m-%dT00:00:00")
        except ValueError:
            end = f"{date_to}T23:59:59"
    return start, end


_MATCH_STATUS_ORDER = """
CASE m.status
    WHEN 'applied' THEN 1
    WHEN 'reviewed' THEN 2
    WHEN 'new' THEN 3
    WHEN 'rejected' THEN 4
    WHEN 'ignored' THEN 5
    ELSE 6
END
"""


def _match_order_clause(sort_by: str = "status_updated_at", sort_dir: str = "desc") -> str:
    if sort_by not in MATCH_SORT_FIELDS:
        sort_by = "status_updated_at"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"
    direction = "ASC" if sort_dir == "asc" else "DESC"
    if sort_by == "status":
        primary = f"{_MATCH_STATUS_ORDER} {direction}"
    elif sort_by == "score":
        primary = f"COALESCE(m.score, -1) {direction}"
    else:
        col = {
            "matched_at": "m.matched_at",
            "status_updated_at": "m.status_updated_at",
            "found_at": "j.found_at",
            "applied_at": "m.applied_at",
            "ranked_at": "m.ranked_at",
            "title": "j.title",
            "company": "j.company",
            "source": "j.source",
            "work_type": "m.work_type",
        }[sort_by]
        primary = f"{col} {direction}"
    if sort_by == "score":
        secondary = ", m.status_updated_at DESC"
    elif sort_by == "status":
        secondary = ", m.status_updated_at DESC, COALESCE(m.score, -1) DESC"
    else:
        secondary = ", COALESCE(m.score, -1) DESC"
    return f"ORDER BY {primary}{secondary}"


def list_matches(
    candidate_id: int,
    *,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: str = "matched_at",
    sort_by: str = "status_updated_at",
    sort_dir: str = "desc",
    min_score: int | None = None,
    q: str | None = None,
    source: str | None = None,
    work_type: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[dict], int]:
    if status and status not in MATCH_STATUSES:
        raise ValueError(f"status must be one of {sorted(MATCH_STATUSES)}")
    if date_field not in MATCH_DATE_FIELDS:
        date_field = "matched_at"
    date_col = {
        "matched_at": "m.matched_at",
        "status_updated_at": "m.status_updated_at",
        "found_at": "j.found_at",
        "applied_at": "m.applied_at",
        "ranked_at": "m.ranked_at",
    }[date_field]
    clauses = ["m.candidate_id = ?"]
    params: list = [candidate_id]
    if status:
        clauses.append("m.status = ?")
        params.append(status)
    start, end = _date_bounds(date_from, date_to)
    if start:
        clauses.append(f"{date_col} >= ?")
        params.append(start)
    if end:
        clauses.append(f"{date_col} < ?")
        params.append(end)
    if min_score is not None:
        clauses.append("m.score >= ?")
        params.append(min_score)
    if q:
        clauses.append("(j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ? OR m.score_reason LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like, like])
    if source:
        clauses.append("j.source = ?")
        params.append(source)
    if work_type:
        clauses.append("m.work_type = ?")
        params.append(work_type)
    where = " AND ".join(clauses)
    sql = f"{_match_select()} WHERE {where} {_match_order_clause(sort_by, sort_dir)}"
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM candidate_job_matches m JOIN jobs j ON j.id = m.job_id WHERE {where}",
            params,
        ).fetchone()[0]
        rows = conn.execute(sql + " LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
        return [row_to_dict(r) for r in rows], int(total)


def dashboard_stats(candidate_id: int | None = None) -> dict:
    with get_conn() as conn:
        if candidate_id is not None:
            by_status = {
                row[0]: row[1]
                for row in conn.execute(
                    """
                    SELECT status, COUNT(*) FROM candidate_job_matches
                    WHERE candidate_id=? GROUP BY status
                    """,
                    (candidate_id,),
                ).fetchall()
            }
            unscored = conn.execute(
                """
                SELECT COUNT(*) FROM candidate_job_matches
                WHERE candidate_id=? AND score IS NULL AND status='new'
                """,
                (candidate_id,),
            ).fetchone()[0]
            total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            return {
                "by_status": by_status,
                "unscored": unscored,
                "total": sum(by_status.values()),
                "catalog_jobs": total_jobs,
            }
        catalog = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        matches = conn.execute("SELECT COUNT(*) FROM candidate_job_matches").fetchone()[0]
        candidates = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        return {
            "by_status": {},
            "unscored": 0,
            "total": catalog,
            "catalog_jobs": catalog,
            "match_rows": matches,
            "candidates": candidates,
        }


def clear_candidate_matches(candidate_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM candidate_job_matches WHERE candidate_id=?",
            (candidate_id,),
        )
        return cur.rowcount


def ignore_duplicate_matches(candidate_id: int) -> int:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT m.job_id, j.title, j.company, m.score, m.matched_at, m.status
            FROM candidate_job_matches m
            JOIN jobs j ON j.id = m.job_id
            WHERE m.candidate_id = ?
            """,
            (candidate_id,),
        ).fetchall()
    items = [row_to_dict(r) for r in rows]
    annotate_match_duplicates(items)
    now = _utcnow()
    updated = 0
    with get_conn() as conn:
        for row in items:
            if not row.get("is_duplicate"):
                continue
            if row.get("status") in {"applied", "rejected"}:
                continue
            reason = (
                f"Duplicate of: {row.get('duplicate_of_title') or 'same role'} "
                f"(job {str(row.get('duplicate_of_job_id', ''))[:8]}…)"
            )
            conn.execute(
                """
                UPDATE candidate_job_matches
                SET status='ignored', status_reason=?, status_updated_at=?, updated_at=?
                WHERE candidate_id=? AND job_id=?
                """,
                (reason, now, now, candidate_id, row["job_id"]),
            )
            updated += 1
    return updated


def export_matches_csv(candidate_id: int, **kwargs) -> str:
    rows, _ = list_matches(candidate_id, limit=100_000, offset=0, **kwargs)
    annotate_match_duplicates(rows)
    buf = io.StringIO()
    fields = [
        "is_duplicate",
        "duplicate_of_job_id",
        "status",
        "status_reason",
        "status_updated_at",
        "matched_at",
        "title",
        "company",
        "location",
        "url",
        "source",
        "score",
        "score_reason",
        "ranked_at",
        "rank_provider",
        "rank_model",
        "work_type",
        "commute_minutes",
        "commute_note",
        "commute_result",
        "auto_apply_eligible",
        "review_status",
        "found_at",
        "posted_at",
        "applied_at",
        "application_state",
        "salary_raw",
        "job_id",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fields})
    return buf.getvalue()
