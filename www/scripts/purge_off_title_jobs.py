"""Remove jobs that fail the configured title filter for one or more sources."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from agents.sources._common import title_matches
from data import db

DEFAULT_SOURCES = ("greenhouse", "adzuna", "jsearch", "lever", "workday")


def load_config() -> dict:
    config_path = Path(__file__).resolve().parents[1] / "config" / "profile.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def purge_source(source: str, config: dict, *, dry_run: bool = True) -> tuple[int, int, int]:
    """Delete rows for source whose titles do not match search.titles."""
    db.init_db()

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title FROM jobs WHERE source = ?",
            (source,),
        ).fetchall()

    total = len(rows)
    to_delete: list[str] = []
    kept = 0

    for row in rows:
        if title_matches(row["title"], config, source):
            kept += 1
        else:
            to_delete.append(row["id"])

    if not dry_run and to_delete:
        with db.get_conn() as conn:
            conn.executemany(
                "DELETE FROM jobs WHERE id = ?",
                [(jid,) for jid in to_delete],
            )

    return total, kept, len(to_delete)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete rows (default is dry-run preview only)",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help=f"Source to purge (repeatable). Default: {', '.join(DEFAULT_SOURCES)}",
    )
    args = parser.parse_args()

    config = load_config()
    sources = args.sources or list(DEFAULT_SOURCES)
    mode = "DELETED" if args.apply else "would delete"
    grand_deleted = 0

    for source in sources:
        total, kept, deleted = purge_source(source, config, dry_run=not args.apply)
        print(f"{source:12} in DB: {total:5}  kept: {kept:5}  off-title ({mode}): {deleted}")
        grand_deleted += deleted

    if not args.apply and grand_deleted:
        print("\nRe-run with --apply to purge.")


if __name__ == "__main__":
    main()
