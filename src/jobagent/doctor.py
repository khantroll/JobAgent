"""Non-destructive environment and database checks for JobAgent 2.0."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from jobagent import AUTO_APPLY_ENABLED, __version__
from jobagent.config import _ENV_API_KEYS, load_settings
from jobagent.paths import project_root, settings_path
from jobagent.web.auth import api_token_configured


def configured_database_path() -> Path:
    """Resolve the DB path without creating directories or opening the file."""
    override = os.environ.get("JOBAGENT_DATABASE_PATH", "").strip()
    if override:
        return Path(override)
    data_override = os.environ.get("JOBAGENT_DATA_DIR", "").strip()
    root = Path(data_override) if data_override else project_root() / "data"
    return root / "jobagent.db"


def _count(conn: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return None


def collect_report() -> dict[str, Any]:
    db_path = configured_database_path()
    warnings: list[str] = []
    errors: list[str] = []

    report: dict[str, Any] = {
        "ok": True,
        "version": __version__,
        "database_path": str(db_path),
        "database_exists": db_path.is_file(),
        "integrity": None,
        "migrations": [],
        "counts": {
            "jobs": None,
            "candidates": None,
            "matches": None,
            "run_history": None,
        },
        "auto_apply_enabled": AUTO_APPLY_ENABLED,
        "auth_configured": api_token_configured(),
        "ui_bind_default": "127.0.0.1",
        "settings_path": None,
        "settings_ok": False,
        "enabled_sources": [],
        "configured_sources": [],
        "api_keys_present": {},
        "warnings": warnings,
        "errors": errors,
    }

    if AUTO_APPLY_ENABLED:
        errors.append("AUTO_APPLY_ENABLED is True; alpha.2 must keep auto-apply disabled.")

    cfg_path = settings_path()
    report["settings_path"] = str(cfg_path)
    try:
        cfg = load_settings(cfg_path)
        report["settings_ok"] = True
        sources = cfg.get("sources") or {}
        report["configured_sources"] = sorted(sources.keys())
        report["enabled_sources"] = sorted(
            name for name, spec in sources.items() if isinstance(spec, dict) and spec.get("enabled")
        )
        api = cfg.get("api") or {}
        keys_present = {}
        for dest, env_name in _ENV_API_KEYS.items():
            value = str(api.get(dest) or os.environ.get(env_name) or "").strip()
            keys_present[env_name] = bool(value) and not value.startswith("YOUR_")
        report["api_keys_present"] = keys_present
    except Exception as exc:
        report["settings_ok"] = False
        errors.append(f"Could not parse settings: {exc}")

    if not db_path.is_file():
        warnings.append("Database file does not exist. Run `jobagent init-db` (and migrate-legacy if needed).")
        report["ok"] = not errors
        return report

    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("PRAGMA query_only = ON;")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        report["integrity"] = integrity
        if integrity != "ok":
            errors.append(f"SQLite integrity_check returned {integrity!r}")
        try:
            report["migrations"] = [
                row[0]
                for row in conn.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
        except sqlite3.Error as exc:
            errors.append(f"schema_migrations unreadable: {exc}")
        report["counts"]["jobs"] = _count(conn, "jobs")
        report["counts"]["candidates"] = _count(conn, "candidates")
        report["counts"]["matches"] = _count(conn, "candidate_job_matches")
        report["counts"]["run_history"] = _count(conn, "run_history")
    except sqlite3.Error as exc:
        errors.append(f"Could not inspect database: {exc}")
        report["ok"] = False
    finally:
        conn.close()

    report["ok"] = not errors
    return report


def format_report(report: dict[str, Any]) -> str:
    keys = report.get("api_keys_present") or {}
    present = [name for name, ok in keys.items() if ok]
    missing = [name for name, ok in keys.items() if not ok]
    lines = [
        f"JobAgent {report['version']} doctor",
        f"  database:     {report['database_path']}",
        f"  exists:       {report['database_exists']}",
        f"  integrity:    {report['integrity']}",
        f"  migrations:   {', '.join(report['migrations']) or '(none)'}",
        f"  jobs:         {report['counts']['jobs']}",
        f"  candidates:   {report['counts']['candidates']}",
        f"  matches:      {report['counts']['matches']}",
        f"  run_history:  {report['counts']['run_history']}",
        f"  auto-apply:   {'ENABLED (invalid for alpha.2)' if report['auto_apply_enabled'] else 'disabled'}",
        f"  UI/API auth:  {'configured' if report['auth_configured'] else 'not configured (local open)'}",
        f"  UI bind:      {report.get('ui_bind_default', '127.0.0.1')} default — do not expose publicly",
        f"  settings:     {report['settings_path']} ({'ok' if report['settings_ok'] else 'ERROR'})",
        f"  sources on:   {', '.join(report['enabled_sources']) or '(none)'}",
        f"  keys present: {', '.join(present) or '(none)'}",
        f"  keys missing: {', '.join(missing) or '(none)'}",
        f"  status:       {'ok' if report['ok'] else 'PROBLEMS FOUND'}",
    ]
    for warning in report.get("warnings") or []:
        lines.append(f"  warning:      {warning}")
    for error in report.get("errors") or []:
        lines.append(f"  error:        {error}")
    return "\n".join(lines)


def run_doctor(*, as_json: bool = False) -> tuple[int, str]:
    report = collect_report()
    if as_json:
        text = json.dumps(report, indent=2, default=str)
    else:
        text = format_report(report)
    return (0 if report["ok"] else 1), text
