"""Durable per-candidate application CRM records."""
from __future__ import annotations

from datetime import datetime, timezone

from jobagent.db.connection import get_conn, row_to_dict


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_event(candidate_id: int, job_id: str, event_type: str, *, from_stage: str = "", to_stage: str = "", note: str = "", occurred_at: str | None = None) -> int:
    now = _utcnow()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO application_events
               (candidate_id, job_id, event_type, from_stage, to_stage, note, occurred_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, job_id, event_type, from_stage, to_stage, note.strip(), occurred_at or now, now),
        )
        return int(cur.lastrowid)


def list_events(candidate_id: int, job_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM application_events WHERE candidate_id=? AND job_id=? ORDER BY occurred_at DESC, id DESC",
            (candidate_id, job_id),
        ).fetchall()
        return [row_to_dict(row) for row in rows]


def add_contact(candidate_id: int, job_id: str, *, name: str, role: str = "", email: str = "", phone: str = "", linkedin: str = "", notes: str = "") -> int:
    if not name.strip():
        raise ValueError("contact name is required")
    now = _utcnow()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO application_contacts
               (candidate_id, job_id, name, role, email, phone, linkedin, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, job_id, name.strip(), role.strip(), email.strip(), phone.strip(), linkedin.strip(), notes.strip(), now, now),
        )
        return int(cur.lastrowid)


def list_contacts(candidate_id: int, job_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM application_contacts WHERE candidate_id=? AND job_id=? ORDER BY name, id",
            (candidate_id, job_id),
        ).fetchall()
        return [row_to_dict(row) for row in rows]


def delete_contact(candidate_id: int, job_id: str, contact_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM application_contacts WHERE id=? AND candidate_id=? AND job_id=?",
            (contact_id, candidate_id, job_id),
        )
        return cur.rowcount > 0
