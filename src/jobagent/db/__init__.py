from __future__ import annotations

from jobagent.db.connection import get_conn, row_to_dict, set_database_path
from jobagent.db.migrate import apply_migrations


def init_db(path=None) -> list[str]:
    if path is not None:
        set_database_path(path)
    return apply_migrations(path)
