"""Dry-run orchestration: crawl shared jobs, then rank and commute per candidate.

Search preferences are unioned across candidates for crawling. Newly discovered
jobs enter the shared catalog; ``upsert_job`` then creates a blank match row for
every **search-enabled** candidate. Ranking and commute run independently per
candidate afterward.

Legacy import does *not* use this linking path — it never guesses historical
ownership. Auto-apply, browser automation, and form submission stay disabled.
"""
from __future__ import annotations

import json
import logging

from jobagent import AUTO_APPLY_ENABLED
from jobagent.config import candidate_runtime_config, crawl_config_for_candidates, load_settings
from jobagent.crawlers import run_all_crawlers
from jobagent.commute import classify_job
from jobagent.db import candidates as cand_repo
from jobagent.db import matches as match_repo
from jobagent.db import runs as run_repo
from jobagent.db import init_db
from jobagent.ranking import run_ranking

logger = logging.getLogger("jobagent.pipeline")


def run_candidate_cycle(candidate: dict, base_config: dict) -> dict:
    cid = candidate["id"]
    name = candidate.get("name") or f"candidate {cid}"
    config = candidate_runtime_config(candidate, base_config)
    scheduler = config.get("scheduler") or {}
    search = config.get("search") or {}
    min_score = int(search.get("min_match_score") or 65)
    max_rank = int(scheduler.get("max_rank_per_run") or 50)

    logger.info("  -- %s (id=%s) --", name, cid)

    unscored = match_repo.get_unscored_matches(cid)
    ranked = 0
    if unscored:
        to_rank = unscored[:max_rank]
        logger.info("[%s] Ranking %s/%s unscored matches", name, len(to_rank), len(unscored))
        run_ranking(to_rank, config, candidate_id=cid)
        ranked = len(to_rank)
    else:
        logger.info("[%s] No unscored matches", name)

    needs_commute = match_repo.get_matches_needing_commute(cid)
    skipped_commute = 0
    reviewed = 0
    eligible = 0
    for job in needs_commute:
        jid = match_repo.catalog_job_id(job)
        match = match_repo.get_match(cid, jid)
        effective_score = (match or {}).get("score")
        if effective_score is None:
            continue
        if int(effective_score) < min_score:
            match_repo.update_match_commute(
                cid,
                jid,
                work_type="unknown",
                commute_minutes=None,
                commute_note="Score below threshold",
                commute_result="skip",
            )
            skipped_commute += 1
            continue
        result = classify_job(job, config)
        match_repo.update_match_commute(
            cid,
            jid,
            work_type=result["work_type"],
            commute_minutes=result["commute_minutes"],
            commute_note=result["commute_note"],
            commute_result=result["action"],
        )
        if result["action"] == "skip":
            skipped_commute += 1
        elif result["action"] == "needs_review":
            reviewed += 1
        elif result["action"] == "auto_apply":
            eligible += 1
        logger.info(
            "[%s] Commute [%s] %s @ %s (%s, %s)",
            name,
            result["action"],
            job["title"],
            job["company"],
            result["work_type"],
            result["commute_note"],
        )

    if AUTO_APPLY_ENABLED:
        raise RuntimeError("Auto-apply must not run in 2.0.0-alpha.2")
    logger.info(
        "[%s] Auto-apply disabled (alpha.2). %s match(es) marked eligible but not submitted.",
        name,
        eligible,
    )

    return {
        "ranked": ranked,
        "flagged_review": reviewed,
        "skipped_commute": skipped_commute,
        "auto_apply_eligible": eligible,
        "applied": 0,
    }


def run_rank(*, candidate_id: int | None = None) -> dict:
    """Rank unscored matches per searching candidate. Does not crawl or apply."""
    logger.info("Starting ranking-only pass (auto-apply disabled)")
    init_db()
    base_config = load_settings()
    if candidate_id is not None:
        person = cand_repo.get_candidate(candidate_id)
        if not person:
            raise ValueError(f"No candidate with id={candidate_id}")
        people = [person]
    else:
        people = cand_repo.list_searching_candidates()
    totals = {"ranked": 0, "candidates": len(people), "applied": 0, "dry_run": True}
    for candidate in people:
        result = run_candidate_cycle(candidate, base_config)
        totals["ranked"] += result["ranked"]
    logger.info("Ranking complete — %s", totals)
    return totals


def run_cycle(*, candidate_id: int | None = None) -> dict:
    logger.info("=" * 60)
    logger.info("Starting dry-run job search cycle (auto-apply disabled)")
    init_db()
    base_config = load_settings()
    if candidate_id is not None:
        person = cand_repo.get_candidate(candidate_id)
        if not person:
            raise ValueError(f"No candidate with id={candidate_id}")
        people = [person]
        crawl_people = [person]
    else:
        people = cand_repo.list_searching_candidates()
        crawl_people = people

    crawl_cfg = crawl_config_for_candidates(crawl_people, base_config)
    logger.info("Running crawler agents (shared catalog)...")
    new_count = run_all_crawlers(crawl_cfg)
    logger.info("%s new listings added to catalog", new_count)

    total_flagged = 0
    total_skipped = 0
    total_ranked = 0
    total_eligible = 0
    errors: list[str] = []

    if not people:
        logger.info("No candidates in DB — catalog crawl only")
    for candidate in people:
        try:
            result = run_candidate_cycle(candidate, base_config)
            total_ranked += result["ranked"]
            total_flagged += result["flagged_review"]
            total_skipped += result["skipped_commute"]
            total_eligible += result["auto_apply_eligible"]
        except Exception as e:
            name = candidate.get("name") or f"id={candidate.get('id')}"
            logger.error("Candidate cycle failed for %s: %s", name, e)
            errors.append(f"{name}: {e}")

    summary = {
        "new_listings": new_count,
        "ranked": total_ranked,
        "applied": 0,
        "flagged_review": total_flagged,
        "skipped_commute": total_skipped,
        "auto_apply_eligible": total_eligible,
        "dry_run": True,
        "auto_apply_enabled": AUTO_APPLY_ENABLED,
        "candidates": len(people),
    }
    run_repo.log_run(
        source="all",
        found=new_count,
        new=new_count,
        applied=0,
        flagged=total_flagged,
        errors="; ".join(errors),
        dry_run=True,
        candidate_id=candidate_id,
        summary=json.dumps(summary),
    )
    logger.info("Cycle complete — %s", summary)
    logger.info("=" * 60)
    return summary
