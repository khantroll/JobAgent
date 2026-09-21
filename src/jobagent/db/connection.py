"""SQLite connection helpers. Foreign keys are always enabled."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from jobagent.paths import default_database_path

_db_path: Path | None = None


def set_database_path(path: Path | str | None) -> None:
    global _db_path
    _db_path = Path(path) if path else None


def database_path() -> Path:
    return _db_path or default_database_path()


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


@contextmanager
def get_conn(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)
