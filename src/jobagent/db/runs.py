"""Run history persistence."""
from __future__ import annotations

from datetime import datetime, timezone

from jobagent.db.connection import get_conn, row_to_dict


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_run(
    *,
    source: str = "all",
    found: int = 0,
    new: int = 0,
    applied: int = 0,
    flagged: int = 0,
    errors: str = "",
    dry_run: bool = True,
    candidate_id: int | None = None,
    summary: str = "",
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO run_history
                (run_at, source, found, new, applied, flagged, errors, dry_run, candidate_id, summary)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _utcnow(),
                source,
                found,
                new,
                applied,
                flagged,
                errors,
                1 if dry_run else 0,
                candidate_id,
                summary,
            ),
        )
        return int(cur.lastrowid)


def list_run_history(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM run_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def count_runs() -> int:
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0])


def save_import_report(source_path: str, started_at: str, finished_at: str, report: dict) -> int:
    import json

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO import_reports (started_at, finished_at, source_path, report_json)
            VALUES (?, ?, ?, ?)
            """,
            (started_at, finished_at, source_path, json.dumps(report, indent=2, default=str)),
        )
        return int(cur.lastrowid)


def latest_import_report() -> dict | None:
    import json

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM import_reports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        data = row_to_dict(row)
        data["report"] = json.loads(data["report_json"])
        return data
