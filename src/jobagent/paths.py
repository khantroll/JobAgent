"""Filesystem locations for JobAgent 2.0."""
from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Directory that contains pyproject.toml (repository root)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return here.parents[2]


def data_dir() -> Path:
    override = os.environ.get("JOBAGENT_DATA_DIR", "").strip()
    path = Path(override) if override else project_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    override = os.environ.get("JOBAGENT_CONFIG_DIR", "").strip()
    return Path(override) if override else project_root() / "config"


def default_database_path() -> Path:
    override = os.environ.get("JOBAGENT_DATABASE_PATH", "").strip()
    return Path(override) if override else data_dir() / "jobagent.db"


def settings_path() -> Path:
    override = os.environ.get("JOBAGENT_SETTINGS_PATH", "").strip()
    if override:
        return Path(override)
    configured = config_dir() / "settings.yaml"
    if configured.is_file():
        return configured
    return config_dir() / "settings.example.yaml"


def output_dir() -> Path:
    override = os.environ.get("JOBAGENT_OUTPUT_DIR", "").strip()
    path = Path(override) if override else project_root() / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_dir() -> Path:
    path = project_root() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path
