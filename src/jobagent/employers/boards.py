"""Gitignored discovered-boards file. Crawlers read the enabled rows."""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from jobagent.paths import config_dir


def discovered_boards_path() -> Path:
    override = os.environ.get("JOBAGENT_DISCOVERED_BOARDS_PATH", "").strip()
    if override:
        return Path(override)
    return config_dir() / "discovered_boards.yaml"


def employer_names_path() -> Path:
    override = os.environ.get("JOBAGENT_EMPLOYER_NAMES_PATH", "").strip()
    if override:
        return Path(override)
    return config_dir() / "employer_names.txt"


def _empty() -> dict[str, Any]:
    return {"boards": [], "unmatched": []}


def load_store(path: Path | None = None) -> dict[str, Any]:
    target = path or discovered_boards_path()
    if not target.is_file():
        return _empty()
    try:
        loaded = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception:
        return _empty()
    if not isinstance(loaded, dict):
        return _empty()
    boards = loaded.get("boards") if isinstance(loaded.get("boards"), list) else []
    unmatched = loaded.get("unmatched") if isinstance(loaded.get("unmatched"), list) else []
    return {"boards": boards, "unmatched": unmatched}


def save_store(store: dict[str, Any], path: Path | None = None) -> Path:
    target = path or discovered_boards_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "boards": list(store.get("boards") or []),
        "unmatched": list(store.get("unmatched") or []),
    }
    header = (
        "# Confirmed public job boards. Gitignored. Deploys must not replace this file.\n"
        "# Disable a board from Settings or set enabled: false. Discovery does not submit applications.\n"
    )
    target.write_text(
        header + yaml.safe_dump(body, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return target


def parse_company_names(text: str) -> list[str]:
    """One name per line, or the first CSV column. Blank lines and # comments are skipped."""
    names: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "," in line:
            try:
                row = next(csv.reader([line]))
            except csv.Error:
                row = [line]
            line = (row[0] if row else "").strip()
        key = line.casefold()
        if not line or key in seen:
            continue
        seen.add(key)
        names.append(line)
    return names


def read_employer_names(path: Path | None = None) -> str:
    target = path or employer_names_path()
    if not target.is_file():
        return ""
    return target.read_text(encoding="utf-8")


def write_employer_names(text: str, path: Path | None = None) -> Path:
    target = path or employer_names_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text if text.endswith("\n") or text == "" else text + "\n", encoding="utf-8")
    return target


def enabled_boards(ats: str, path: Path | None = None) -> list[dict[str, Any]]:
    rows = []
    for board in load_store(path).get("boards") or []:
        if not isinstance(board, dict):
            continue
        if str(board.get("ats") or "") != ats:
            continue
        if board.get("enabled") is False:
            continue
        rows.append(board)
    return rows


def slugs_for(ats: str, manual: list | None, path: Path | None = None) -> list[str]:
    values: list[str] = []
    for item in manual or []:
        text = str(item or "").strip()
        if text and text not in values:
            values.append(text)
    for board in enabled_boards(ats, path):
        slug = str(board.get("slug") or "").strip()
        if slug and slug not in values:
            values.append(slug)
    return values


def workday_entries(manual: list | None, path: Path | None = None) -> list[dict]:
    entries: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in manual or []:
        if isinstance(item, dict):
            entries.append(item)
            key = (
                str(item.get("tenant") or "").lower(),
                str(item.get("cluster") or "").lower(),
                str(item.get("site") or ""),
            )
            if key[0] and key[2]:
                seen.add(key)
    for board in enabled_boards("workday", path):
        key = (
            str(board.get("tenant") or "").lower(),
            str(board.get("cluster") or "").lower(),
            str(board.get("site") or ""),
        )
        if not key[0] or not key[2] or key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "name": board.get("name") or key[0],
                "tenant": key[0],
                "cluster": key[1] or "wd1",
                "site": key[2],
            }
        )
    return entries


def upsert_confirmed(board: dict[str, Any], path: Path | None = None) -> None:
    store = load_store(path)
    boards = [row for row in store["boards"] if isinstance(row, dict)]
    replaced = False
    for index, row in enumerate(boards):
        if row.get("id") == board.get("id"):
            board["enabled"] = row.get("enabled", True)
            boards[index] = board
            replaced = True
            break
    if not replaced:
        board.setdefault("enabled", True)
        boards.append(board)
    name_key = str(board.get("name") or "").casefold()
    unmatched = [
        row
        for row in store["unmatched"]
        if isinstance(row, dict) and str(row.get("name") or "").casefold() != name_key
    ]
    save_store({"boards": boards, "unmatched": unmatched}, path)


def record_unmatched(name: str, path: Path | None = None) -> None:
    store = load_store(path)
    key = name.casefold()
    for row in store["boards"]:
        if isinstance(row, dict) and str(row.get("name") or "").casefold() == key:
            return
    unmatched = [row for row in store["unmatched"] if isinstance(row, dict)]
    now = datetime.now(timezone.utc).isoformat()
    for row in unmatched:
        if str(row.get("name") or "").casefold() == name.casefold():
            row["checked_at"] = now
            save_store({"boards": store["boards"], "unmatched": unmatched}, path)
            return
    unmatched.append({"name": name, "checked_at": now})
    save_store({"boards": store["boards"], "unmatched": unmatched}, path)


def set_enabled(enabled_ids: set[str], known_ids: list[str], path: Path | None = None) -> None:
    store = load_store(path)
    known = set(known_ids)
    for row in store["boards"]:
        if not isinstance(row, dict):
            continue
        if row.get("id") not in known:
            continue
        row["enabled"] = row.get("id") in enabled_ids
    save_store(store, path)
