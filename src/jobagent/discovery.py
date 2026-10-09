"""Dry-run job discovery: crawl enabled sources into the shared catalog.

Never submits applications. A single source failure does not abort the crawl.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any

from jobagent import AUTO_APPLY_ENABLED
from jobagent.config import (
    crawl_config_for_candidates,
    load_settings,
    scheduler_auto_apply,
    scheduler_dry_run,
    submission_allowed,
)
from jobagent.db import candidates as cand_repo
from jobagent.db import init_db
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo
from jobagent.db import runs as run_repo
from jobagent.sources import SOURCES, annotate_source_result, source_skip_reason
from jobagent.sources._common import source_enabled, track_inserts

logger = logging.getLogger("jobagent.discovery")


@dataclass
class SourceRun:
    name: str
    attempted: bool
    ok: bool
    inserted: int = 0
    seen: int = 0
    duplicates: int = 0
    skipped_reason: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0
    status: str = ""
    status_detail: str = ""


def _source_run_from_record(record: dict) -> SourceRun:
    fields = {key: record.get(key) for key in SourceRun.__dataclass_fields__}
    return SourceRun(**fields)


def _run_one_source(name: str, crawl_fn, config: dict) -> SourceRun:
    skip = source_skip_reason(name, config)
    if skip:
        return _source_run_from_record(
            annotate_source_result(
                name=name, attempted=False, ok=True, skipped_reason=skip
            )
        )
    started = time.perf_counter()
    try:
        with track_inserts() as counters:
            crawl_fn(config)
        return _source_run_from_record(
            annotate_source_result(
                name=name,
                attempted=True,
                ok=True,
                inserted=counters.inserted,
                seen=counters.seen,
                duplicates=counters.duplicates,
                blocked=counters.blocked,
                block_reason=counters.block_reason,
                elapsed_seconds=round(time.perf_counter() - started, 3),
            )
        )
    except Exception as exc:
        logger.error("[%s] crawler error: %s", name, exc)
        return _source_run_from_record(
            annotate_source_result(
                name=name,
                attempted=True,
                ok=False,
                error=str(exc),
                elapsed_seconds=round(time.perf_counter() - started, 3),
            )
        )


def run_discovery(*, dry_run: bool | None = None) -> dict[str, Any]:
    """Crawl using the union of search-enabled candidate preferences.

    dry_run records how the run is labeled. None uses scheduler.dry_run.
    Discovery never submits applications.
    """
    started = time.perf_counter()
    init_db()
    before_jobs = job_repo.count_jobs()
    before_matches = match_repo.count_all_matches()
    people = cand_repo.list_searching_candidates()
    base = load_settings()
    effective_dry_run = scheduler_dry_run(base) if dry_run is None else bool(dry_run)
    if submission_allowed(base):
        raise RuntimeError("Auto-apply submission is not implemented; refusing to send applications.")
    config = crawl_config_for_candidates(people, base)

    results: list[SourceRun] = []
    tasks: list[tuple[str, object]] = []
    for module, name in SOURCES:
        if not source_enabled(config, name):
            results.append(
                _source_run_from_record(
                    annotate_source_result(
                        name=name,
                        attempted=False,
                        ok=True,
                        skipped_reason="disabled in settings",
                    )
                )
            )
            continue
        tasks.append((name, module.crawl))
    if not tasks:
        logger.warning("No job sources enabled")
    else:
        with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
            futures = {
                pool.submit(_run_one_source, name, fn, config): name
                for name, fn in tasks
            }
            for future in as_completed(futures):
                results.append(future.result())

    rss_feeds = (config.get("search") or {}).get("rss_feeds") or []
    if rss_feeds:
        from jobagent.crawlers import crawl_rss

        def _rss(_config=None):
            for url in rss_feeds:
                crawl_rss(url)
            return 0

        results.append(_run_one_source("rss", _rss, config))
    else:
        results.append(
            _source_run_from_record(
                annotate_source_result(
                    name="rss",
                    attempted=False,
                    ok=True,
                    skipped_reason="no rss_feeds configured",
                )
            )
        )

    results.sort(key=lambda r: r.name)
    after_jobs = job_repo.count_jobs()
    after_matches = match_repo.count_all_matches()
    elapsed = time.perf_counter() - started
    inserted = after_jobs - before_jobs
    matches_created = after_matches - before_matches
    failures = [r for r in results if not r.ok]
    summary = {
        "dry_run": effective_dry_run,
        "auto_apply_opt_in": scheduler_auto_apply(base),
        "auto_apply_enabled": AUTO_APPLY_ENABLED,
        "submitted": False,
        "candidates_in_union": [
            {"id": p["id"], "name": p.get("name")} for p in people
        ],
        "sources": [asdict(r) for r in results],
        "sources_attempted": [r.name for r in results if r.attempted],
        "sources_succeeded": [
            r.name for r in results if r.attempted and r.ok and r.status != "blocked"
        ],
        "sources_failed": [r.name for r in results if not r.ok],
        "sources_skipped": [r.name for r in results if r.skipped_reason],
        "jobs_fetched": sum(r.seen for r in results),
        "jobs_newly_inserted": inserted,
        "duplicates": sum(r.duplicates for r in results),
        "matches_created": matches_created,
        "catalog_jobs_before": before_jobs,
        "catalog_jobs_after": after_jobs,
        "elapsed_seconds": round(elapsed, 3),
        "ok": not failures,
    }
    run_repo.log_run(
        source="crawl",
        found=summary["jobs_fetched"],
        new=inserted,
        applied=0,
        flagged=len(failures),
        errors="; ".join(f"{r.name}: {r.error}" for r in failures),
        dry_run=effective_dry_run,
        summary=json.dumps(summary, default=str),
    )
    logger.info("Discovery complete — %s", {k: summary[k] for k in (
        "jobs_newly_inserted", "duplicates", "matches_created", "elapsed_seconds"
    )})
    return summary
