"""JobAgent 2.0 command-line entrypoint."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from jobagent import __version__


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

    cycle = sub.add_parser("run-cycle", help="Run one dry-run crawl/rank/commute cycle")
    cycle.add_argument("--candidate-id", type=int, default=None)

    crawl = sub.add_parser("crawl", help="Dry-run discovery into the shared catalog (no apply)")
    crawl.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Accepted for clarity; discovery never submits applications",
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

        summary = run_cycle(candidate_id=args.candidate_id)
        print(json.dumps(summary, indent=2, default=str))
        return 0

    if args.command == "crawl":
        from jobagent.discovery import run_discovery

        summary = run_discovery(dry_run=True)
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

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
