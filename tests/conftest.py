from __future__ import annotations

from pathlib import Path

import pytest

from jobagent.db import init_db
from jobagent.db.connection import set_database_path


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "jobagent.db"
    set_database_path(path)
    monkeypatch.setenv("JOBAGENT_DATABASE_PATH", str(path))
    init_db(path)
    return path
