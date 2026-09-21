"""Candidate records. There is no global 'active' identity."""
from __future__ import annotations

from datetime import datetime, timezone

from jobagent.db.connection import get_conn, row_to_dict


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_value(value, default: int = 0) -> int:
    try:
        text = str(value if value is not None else "").strip()
        return int(text) if text else int(default)
    except (TypeError, ValueError):
        return int(default)


def search_enabled_value(candidate: dict | None) -> bool:
    if not candidate:
        return True
    val = candidate.get("search_enabled")
    if val is None:
        return True
    return int(val) != 0


def list_candidates() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM candidates ORDER BY name, id").fetchall()
        return [row_to_dict(r) for r in rows]


def list_searching_candidates() -> list[dict]:
    """Candidates whose prefs participate in crawl union and auto-linking."""
    out: list[dict] = []
    for row in list_candidates():
        if search_enabled_value(row):
            full = get_candidate(row["id"])
            if full:
                out.append(full)
    return out


def count_candidates() -> int:
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0])


def get_candidate(cid: int) -> dict | None:
    with get_conn() as conn:
        cand = row_to_dict(
            conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        )
        if not cand:
            return None
        cand["titles"] = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM candidate_titles WHERE candidate_id=? ORDER BY id",
                (cid,),
            ).fetchall()
        ]
        cand["employers"] = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM candidate_employers WHERE candidate_id=? ORDER BY id",
                (cid,),
            ).fetchall()
        ]
        return cand


def save_candidate(data: dict, titles: list, employers: list, candidate_id: int | None = None) -> int:
    now = _utcnow()
    search_enabled = 0 if data.get("search_enabled", 1) in (0, "0", False, "false") else 1
    remote_ok = 0 if data.get("location_accept_remote", 1) in (0, "0", False, "false") else 1
    recon = data.get("reconstruction_source")
    with get_conn() as conn:
        if candidate_id:
            existing = row_to_dict(
                conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            ) or {}
            if recon is None:
                recon = existing.get("reconstruction_source") or ""
            conn.execute(
                """UPDATE candidates SET name=?, email=?, phone=?, location=?, linkedin=?,
                   github=?, resume_text=?, resume_file=?, min_match_score=?, salary_min=?,
                   salary_max=?, keywords_text=?, hej_category_ids=?,
                   commute_auto_apply_minutes=?, commute_review_minutes=?,
                   search_enabled=?, reconstruction_source=?, location_accept_remote=?,
                   updated_at=?
                   WHERE id=?""",
                (
                    data["name"],
                    data.get("email") or "",
                    data.get("phone") or "",
                    data.get("location") or "",
                    data.get("linkedin") or "",
                    data.get("github") or "",
                    data.get("resume_text") or "",
                    data.get("resume_file") or "",
                    _int_value(data.get("min_match_score"), 65),
                    _int_value(data.get("salary_min"), 0),
                    _int_value(data.get("salary_max"), 0),
                    data.get("keywords_text") or "",
                    data.get("hej_category_ids") or "",
                    _int_value(data.get("commute_auto_apply_minutes"), 30),
                    _int_value(data.get("commute_review_minutes"), 90),
                    search_enabled,
                    recon or "",
                    remote_ok,
                    now,
                    candidate_id,
                ),
            )
            cid = int(candidate_id)
            conn.execute("DELETE FROM candidate_titles WHERE candidate_id=?", (cid,))
            conn.execute("DELETE FROM candidate_employers WHERE candidate_id=?", (cid,))
        else:
            cur = conn.execute(
                """INSERT INTO candidates
                   (name,email,phone,location,linkedin,github,resume_text,resume_file,
                    min_match_score,salary_min,salary_max,keywords_text,hej_category_ids,
                    commute_auto_apply_minutes,commute_review_minutes,
                    search_enabled,reconstruction_source,location_accept_remote,
                    created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data["name"],
                    data.get("email") or "",
                    data.get("phone") or "",
                    data.get("location") or "",
                    data.get("linkedin") or "",
                    data.get("github") or "",
                    data.get("resume_text") or "",
                    data.get("resume_file") or "",
                    _int_value(data.get("min_match_score"), 65),
                    _int_value(data.get("salary_min"), 0),
                    _int_value(data.get("salary_max"), 0),
                    data.get("keywords_text") or "",
                    data.get("hej_category_ids") or "",
                    _int_value(data.get("commute_auto_apply_minutes"), 30),
                    _int_value(data.get("commute_review_minutes"), 90),
                    search_enabled,
                    recon or "",
                    remote_ok,
                    now,
                    now,
                ),
            )
            cid = int(cur.lastrowid)
        for title in [t.strip() for t in titles if str(t).strip()]:
            conn.execute(
                "INSERT INTO candidate_titles (candidate_id, title) VALUES (?, ?)",
                (cid, title),
            )
        for emp in employers:
            if not (emp.get("name") or "").strip():
                continue
            conn.execute(
                """INSERT INTO candidate_employers
                   (candidate_id,name,source_type,careers_url,workday_url,greenhouse_slug,lever_slug)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    cid,
                    emp.get("name", "").strip(),
                    emp.get("source_type") or "workday",
                    emp.get("careers_url") or "",
                    emp.get("workday_url") or "",
                    emp.get("greenhouse_slug") or "",
                    emp.get("lever_slug") or "",
                ),
            )
        return cid


def insert_candidate_with_id(candidate: dict, titles: list, employers: list) -> int:
    """Import a legacy candidate preserving its original id (idempotent upsert)."""
    now = _utcnow()
    cid = int(candidate["id"])
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM candidates WHERE id=?", (cid,)).fetchone()
        fields = (
            candidate.get("name") or "",
            candidate.get("email") or "",
            candidate.get("phone") or "",
            candidate.get("location") or "",
            candidate.get("linkedin") or "",
            candidate.get("github") or "",
            candidate.get("resume_text") or "",
            candidate.get("resume_file") or "",
            _int_value(candidate.get("min_match_score"), 65),
            _int_value(candidate.get("salary_min"), 0),
            _int_value(candidate.get("salary_max"), 0),
            candidate.get("keywords_text") or "",
            candidate.get("hej_category_ids") or "",
            _int_value(candidate.get("commute_auto_apply_minutes"), 30),
            _int_value(candidate.get("commute_review_minutes"), 90),
        )
        if existing:
            conn.execute(
                """UPDATE candidates SET name=?, email=?, phone=?, location=?, linkedin=?,
                   github=?, resume_text=?, resume_file=?, min_match_score=?, salary_min=?,
                   salary_max=?, keywords_text=?, hej_category_ids=?,
                   commute_auto_apply_minutes=?, commute_review_minutes=?, updated_at=?
                   WHERE id=?""",
                fields + (now, cid),
            )
            conn.execute("DELETE FROM candidate_titles WHERE candidate_id=?", (cid,))
            conn.execute("DELETE FROM candidate_employers WHERE candidate_id=?", (cid,))
        else:
            conn.execute(
                """INSERT INTO candidates
                   (id,name,email,phone,location,linkedin,github,resume_text,resume_file,
                    min_match_score,salary_min,salary_max,keywords_text,hej_category_ids,
                    commute_auto_apply_minutes,commute_review_minutes,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid,)
                + fields
                + (candidate.get("created_at") or now, candidate.get("updated_at") or now),
            )
        for title in titles:
            text = title["title"] if isinstance(title, dict) else str(title)
            if text.strip():
                conn.execute(
                    "INSERT INTO candidate_titles (candidate_id, title) VALUES (?, ?)",
                    (cid, text.strip()),
                )
        for emp in employers:
            if not (emp.get("name") or "").strip():
                continue
            conn.execute(
                """INSERT INTO candidate_employers
                   (candidate_id,name,source_type,careers_url,workday_url,greenhouse_slug,lever_slug)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    cid,
                    emp.get("name", "").strip(),
                    emp.get("source_type") or "workday",
                    emp.get("careers_url") or "",
                    emp.get("workday_url") or "",
                    emp.get("greenhouse_slug") or "",
                    emp.get("lever_slug") or "",
                ),
            )
    return cid


def find_candidate_by_email(email: str) -> dict | None:
    needle = (email or "").strip().lower()
    if not needle:
        return None
    for row in list_candidates():
        if (row.get("email") or "").strip().lower() == needle:
            return get_candidate(row["id"])
    return None


def set_search_enabled(candidate_id: int, enabled: bool, *, reconstruction_source: str | None = None) -> None:
    now = _utcnow()
    with get_conn() as conn:
        if reconstruction_source is None:
            conn.execute(
                "UPDATE candidates SET search_enabled=?, updated_at=? WHERE id=?",
                (1 if enabled else 0, now, candidate_id),
            )
        else:
            conn.execute(
                """UPDATE candidates SET search_enabled=?, reconstruction_source=?, updated_at=?
                   WHERE id=?""",
                (1 if enabled else 0, reconstruction_source, now, candidate_id),
            )


def delete_candidate(cid: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM candidates WHERE id=?", (cid,))
        return cur.rowcount > 0


def update_hej_category_ids(candidate_id: int, category_ids_text: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE candidates SET hej_category_ids=?, updated_at=? WHERE id=?",
            (category_ids_text or "", _utcnow(), candidate_id),
        )
