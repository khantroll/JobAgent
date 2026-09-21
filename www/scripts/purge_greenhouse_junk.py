"""Remove Greenhouse jobs that fail the configured title filter."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from agents.sources._common import title_matches
from data import db


def load_config() -> dict:
    config_path = Path(__file__).resolve().parents[1] / "config" / "profile.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def purge_greenhouse_junk(*, dry_run: bool = True) -> tuple[int, int, int]:
    """
    Delete greenhouse rows whose titles do not match search.titles.

    Returns (total_greenhouse, kept, deleted).
    """
    config = load_config()
    db.init_db()

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, company, status FROM jobs WHERE source = 'greenhouse'"
        ).fetchall()

    total = len(rows)
    to_delete: list[str] = []
    kept = 0

    for row in rows:
        if title_matches(row["title"], config, "greenhouse"):
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
    args = parser.parse_args()

    total, kept, deleted = purge_greenhouse_junk(dry_run=not args.apply)

    mode = "DELETED" if args.apply else "would delete"
    print(f"Greenhouse jobs in DB: {total}")
    print(f"Title-matched (kept):  {kept}")
    print(f"Off-title ({mode}):    {deleted}")

    if not args.apply and deleted:
        print("\nRe-run with --apply to purge.")


if __name__ == "__main__":
    main()
