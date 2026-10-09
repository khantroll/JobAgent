from __future__ import annotations

from pathlib import Path

import pytest

from jobagent.db import init_db
from jobagent.db.connection import set_database_path


@pytest.fixture(autouse=True)
def isolate_secrets_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep tests from reading or writing the repo's gitignored secrets file."""
    path = tmp_path / "secrets.yaml"
    monkeypatch.setenv("JOBAGENT_SECRETS_PATH", str(path))
    monkeypatch.setenv("JOBAGENT_DISCOVERED_BOARDS_PATH", str(tmp_path / "discovered_boards.yaml"))
    monkeypatch.setenv("JOBAGENT_EMPLOYER_NAMES_PATH", str(tmp_path / "employer_names.txt"))
    monkeypatch.setenv("JOBAGENT_EMPLOYER_CACHE_PATH", str(tmp_path / "employer_cache.json"))
    return path


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "jobagent.db"
    set_database_path(path)
    monkeypatch.setenv("JOBAGENT_DATABASE_PATH", str(path))
    init_db(path)
    return path
