"""JobAgent 2.0 command-line entrypoint."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from jobagent import __version__


def _accept_deprecated_api_token(value: str | None) -> None:
    """Honor a legacy flag without printing the token. Prefer the environment or secrets file."""
    text = str(value or "").strip()
    if not text:
        return
    logging.getLogger("jobagent.cli").warning(
        "run-cycle was passed --api-token/--token. That flag is deprecated because the token "
        "is visible in process listings. Remove it from the systemd unit. Set JOB_AGENT_API_TOKEN "
        "or api_token in config/secrets.yaml."
    )
    from jobagent.config import load_api_token

    if load_api_token():
        return
    os.environ["JOB_AGENT_API_TOKEN"] = text


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobagent", description="JobAgent 2.0.0-alpha.2")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI / Jinja UI")
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Default 127.0.0.1 (local only). Do not expose the UI on a public interface.",
    )
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--reload", action="store_true")

    migrate = sub.add_parser("migrate-legacy", help="Import recovered www/data/jobs.db (read-only source)")
    migrate.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Path to the recovered jobs.db (default: www/data/jobs.db)",
    )
    migrate.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Directory for JSON migration reports",
    )

    cycle = sub.add_parser("run-cycle", help="Run one crawl/rank/commute cycle (respects scheduler.dry_run; does not submit)")
    cycle.add_argument("--candidate-id", type=int, default=None)
    cycle.add_argument(
        "--api-token",
        "--token",
        dest="api_token",
        default=None,
        help=(
            "Deprecated. The value is visible in process listings. "
            "Set JOB_AGENT_API_TOKEN or api_token in config/secrets.yaml instead."
        ),
    )

    crawl = sub.add_parser("crawl", help="Discover jobs into the shared catalog (never submits)")
    crawl.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Record this crawl as a dry run. Defaults to scheduler.dry_run. Never submits applications.",
    )

    rank = sub.add_parser("rank", help="Rank unscored matches per searching candidate")
    rank.add_argument("--candidate-id", type=int, default=None)

    reconstruct = sub.add_parser(
        "reconstruct-candidates",
        help="Fill candidate records from frozen legacy profiles (never writes www/)",
    )
    reconstruct.add_argument("--report-dir", type=Path, default=None)

    evaluate = sub.add_parser("evaluate", help="Read-only ranking quality report")
    evaluate.add_argument("--json", action="store_true")
    evaluate.add_argument("--top", type=int, default=10)

    init = sub.add_parser("init-db", help="Create/upgrade the SQLite schema")

    doctor = sub.add_parser("doctor", help="Non-destructive environment and database checks")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    discover = sub.add_parser(
        "discover-employers",
        help="Find public job-board URLs for company names. Does not submit applications.",
    )
    discover.add_argument("--names-file", type=Path, default=None, help="CSV or text file of company names")
    discover.add_argument(
        "--include-catalog",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also use company names already stored on collected jobs",
    )
    discover.add_argument(
        "--near-home",
        action="store_true",
        help="Add one OpenStreetMap query around search.location",
    )
    discover.add_argument("--delay", type=float, default=1.0, help="Seconds between HTTP requests")
    discover.add_argument("--limit", type=int, default=100, help="Maximum company names to probe")

    args = parser.parse_args(argv)
    _configure_logging()

    if args.command == "serve":
        import uvicorn

        host = args.host
        if host not in {"127.0.0.1", "localhost", "::1"}:
            logging.getLogger("jobagent.cli").warning(
                "Binding to %s exposes the JobAgent UI. Alpha.2 is not hardened for public "
                "access. Prefer 127.0.0.1 and set JOB_AGENT_API_TOKEN.",
                host,
            )
        uvicorn.run(
            "jobagent.web.app:app",
            host=host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    if args.command == "init-db":
        from jobagent.db import init_db
        from jobagent.paths import default_database_path

        applied = init_db()
        print(f"Database: {default_database_path()}")
        print("Applied migrations:", ", ".join(applied) or "(already up to date)")
        return 0

    if args.command == "migrate-legacy":
        from jobagent.migrate_legacy import import_legacy_database

        report = import_legacy_database(args.source, report_dir=args.report_dir)
        print(json.dumps(report, indent=2, default=str))
        return 0

    if args.command == "run-cycle":
        from jobagent.pipeline import run_cycle

        _accept_deprecated_api_token(getattr(args, "api_token", None))
        summary = run_cycle(candidate_id=args.candidate_id)
        print(json.dumps(summary, indent=2, default=str))
        return 0

    if args.command == "crawl":
        from jobagent.discovery import run_discovery

        summary = run_discovery(dry_run=args.dry_run)
        print(json.dumps(summary, indent=2, default=str))
        return 0 if summary.get("ok", True) else 1

    if args.command == "rank":
        from jobagent.pipeline import run_rank

        summary = run_rank(candidate_id=args.candidate_id)
        print(json.dumps(summary, indent=2, default=str))
        return 0

    if args.command == "reconstruct-candidates":
        from jobagent.reconstruct import reconstruct_candidates

        report = reconstruct_candidates(report_dir=args.report_dir)
        print(json.dumps(report, indent=2, default=str))
        return 0

    if args.command == "evaluate":
        from jobagent.evaluate import evaluate_database, format_evaluation

        report = evaluate_database(top_n=args.top)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print(format_evaluation(report))
        return 0

    if args.command == "doctor":
        from jobagent.doctor import run_doctor

        code, text = run_doctor(as_json=args.json)
        print(text)
        return code

    if args.command == "discover-employers":
        from jobagent.config import load_settings
        from jobagent.employers.discover import collect_names, discover_employers

        names = collect_names(
            include_file=True,
            include_catalog=args.include_catalog,
            near_home=args.near_home,
            config=load_settings(),
            names_file=args.names_file,
        )[: max(0, args.limit)]
        summary = discover_employers(names, delay=args.delay)
        print(json.dumps(summary, indent=2, default=str))
        return 0

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
