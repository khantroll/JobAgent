"""Apply numbered SQL migration files in order. Versions are stored in schema_migrations.

Released files are immutable: never edit 001_initial.sql (or any already-shipped
migration) to change the schema. Add 002_*.sql, 003_*.sql, and so on.
Re-running apply_migrations is idempotent; applied versions are skipped.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jobagent.db.connection import get_conn

SQL_DIR = Path(__file__).resolve().parent / "sql"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def available_migrations() -> list[Path]:
    return sorted(p for p in SQL_DIR.glob("*.sql") if p.name[:3].isdigit())


def applied_versions(conn) -> set[str]:
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    except Exception:
        return set()
    return {row[0] for row in rows}


def apply_migrations(db_path=None) -> list[str]:
    applied: list[str] = []
    with get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )
        already = applied_versions(conn)
        for path in available_migrations():
            version = path.stem
            if version in already:
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, _utcnow()),
            )
            applied.append(version)
    return applied
