"""
Orchestrator — coordinates all agents, checkpoints to DB.
Pipeline per cycle:
  1. Crawl all boards in parallel
  2. Rank new jobs with Claude
  3. Commute check — classify as auto_apply / needs_review / skip
  4. Generate docs + apply for auto_apply jobs
  5. Pre-generate docs for needs_review jobs (so they're ready if you decide to apply)
  6. Send applied digest + review digest emails
"""
import sys
import logging
import yaml
import schedule
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Windows consoles often default to cp1252; force UTF-8 so log messages don't crash.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from data import db as database
from agents.crawlers import run_all_crawlers
from agents.ranker import run_ranking
from agents.commute import classify_job
from agents.doc_gen import generate_docs
from agents.applier import apply_to_job
from agents.notifier import send_applied_notification, send_review_notification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("job_agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("orchestrator")


def load_config() -> dict:
    config_path = Path(__file__).parent / "config" / "profile.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def build_candidate_config(candidate: dict, base_config: dict) -> dict:
    """
    Return a deep-merged config where top-level ``profile`` and any relevant
    ``search`` overrides come from the candidate's database record.

    ``base_config`` (from profile.yaml) supplies scheduler, API keys, and
    anything not stored in the DB.  Candidate DB fields always win for profile
    data so each person's commute origin, salary expectations, etc. are used
    without bleeding across profiles.
    """
    cfg = {**base_config}

    cfg["profile"] = {
        "name":     candidate.get("name", ""),
        "email":    candidate.get("email", ""),
        "phone":    candidate.get("phone", ""),
        "location": candidate.get("location", ""),
        "linkedin": candidate.get("linkedin", ""),
        "github":   candidate.get("github", ""),
    }

    if candidate.get("resume_text"):
        cfg["resume_text"] = candidate["resume_text"]

    search = dict(cfg.get("search") or {})
    if candidate.get("min_match_score") is not None:
        search["min_match_score"] = int(candidate["min_match_score"])
    if candidate.get("salary_min") is not None:
        search["salary_min"] = int(candidate["salary_min"])
    if candidate.get("salary_max") is not None:
        search["salary_max"] = int(candidate["salary_max"])
    keywords_text = candidate.get("keywords_text") or ""
    if keywords_text.strip():
        search["keywords"] = [k.strip() for k in keywords_text.splitlines() if k.strip()]
    cfg["search"] = search

    return cfg


def run_candidate_cycle(candidate: dict, base_config: dict) -> dict:
    """
    Run the full rank → commute → apply → notify pipeline for one candidate.

    The candidate's geographic location is used for commute scoring so that
    different people's home addresses never bleed into each other's results.
    Returns a per-candidate summary dict.
    """
    cid = candidate["id"]
    name = candidate.get("name") or f"candidate {cid}"
    config = build_candidate_config(candidate, base_config)
    scheduler = config.get("scheduler") or {}
    search = config.get("search") or {}
    min_score = search.get("min_match_score", 65)
    max_rank = scheduler.get("max_rank_per_run", 50)
    max_applies = scheduler.get("max_applies_per_run", 5)

    logger.info(f"  ── {name} (id={cid}) ──")

    # ── 2a. Rank unscored matches for this candidate ──────────────
    unscored = database.get_unscored_matches_for_candidate(cid)
    if unscored:
        to_rank = unscored[:max_rank]
        if len(unscored) > max_rank:
            logger.info(f"[{name}] Ranking {len(to_rank)}/{len(unscored)} unscored matches")
        else:
            logger.info(f"[{name}] Ranking {len(to_rank)} unscored matches")
        run_ranking(to_rank, config, database, candidate_id=cid)
    else:
        logger.info(f"[{name}] No unscored matches")

    # ── 2b. Commute check — uses this candidate's home location ───
    with database.get_conn() as conn:
        needs_commute_check = conn.execute(
            """
            SELECT j.*
            FROM candidate_job_matches m
            JOIN jobs j ON j.id = m.job_id
            WHERE m.candidate_id = ?
              AND m.score IS NOT NULL
              AND j.work_type IS NULL
              AND m.status = 'new'
            """,
            (cid,),
        ).fetchall()

    skipped_commute = 0
    for job in needs_commute_check:
        job = dict(job)
        match_score = database.get_candidate_match(cid, job["id"])
        effective_score = (
            match_score.get("score") if match_score else None
        ) if match_score else job.get("score") or 0

        if (effective_score or 0) < min_score:
            database.update_commute(
                job["id"], "unknown", None, "Score below threshold", "skip"
            )
            skipped_commute += 1
            continue

        result = classify_job(job, config)
        database.update_commute(
            job["id"],
            result["work_type"],
            result["commute_minutes"],
            result["commute_note"],
            result["action"],
        )
        logger.info(
            f"[{name}] Commute [{result['action']}] {job['title']} @ {job['company']} "
            f"({result['work_type']}, {result['commute_note']})"
        )

    # ── 3. Auto-apply to approved matches ─────────────────────────
    approved = database.get_approved_jobs(min_score)[:max_applies]
    logger.info(f"[{name}] {len(approved)} jobs approved for auto-apply")

    applied_jobs = []
    for job in approved:
        job = dict(job)
        try:
            resume_path, cover_path = generate_docs(job, config)
            success = apply_to_job(job, resume_path, cover_path, config)
            if success:
                database.mark_applied(job["id"], resume_path, cover_path)
                applied_jobs.append(job)
                logger.info(f"[{name}] Applied: {job['title']} @ {job['company']}")
        except Exception as e:
            logger.error(f"[{name}] Failed on {job.get('title')}: {e}")

    # ── 4. Pre-generate docs for review jobs ──────────────────────
    review_jobs = database.get_review_jobs(min_score)
    logger.info(f"[{name}] {len(review_jobs)} jobs flagged for review")

    for job in review_jobs:
        job = dict(job)
        try:
            if not job.get("resume_path"):
                resume_path, cover_path = generate_docs(job, config)
                with database.get_conn() as conn:
                    conn.execute(
                        "UPDATE jobs SET resume_path=?, cover_path=? WHERE id=?",
                        (resume_path, cover_path, job["id"]),
                    )
                logger.info(f"[{name}] Pre-generated docs: {job['title']} @ {job['company']}")
        except Exception as e:
            logger.error(f"[{name}] Doc gen failed for {job.get('title')}: {e}")

    # ── 5. Send notifications ──────────────────────────────────────
    pending_applied = database.get_unapplied_notifications()
    if pending_applied:
        if send_applied_notification(pending_applied, config):
            for job in pending_applied:
                database.mark_notified(job["id"])

    if review_jobs:
        if send_review_notification(review_jobs, config):
            for job in review_jobs:
                database.mark_notified(job["id"])

    return {
        "applied": len(applied_jobs),
        "flagged_review": len(review_jobs),
        "skipped_commute": skipped_commute,
    }


def run_cycle() -> dict:
    """
    Run one full pipeline cycle across all registered candidates.
    Returns an aggregate summary dict for API/UI consumption.
    """
    logger.info("=" * 60)
    logger.info("Starting job search cycle")
    base_config = load_config()
    database.init_db()

    # ── 1. Crawl all boards (shared across candidates) ────────────
    logger.info("Running crawler agents...")
    new_count = run_all_crawlers(base_config)
    logger.info(f"{new_count} new listings added to database")

    # ── 2-5. Per-candidate: rank, commute, apply, notify ──────────
    candidates = database.list_active_candidates()
    if not candidates:
        # Fall back to single-profile mode using base_config directly
        logger.info("No candidates in DB — running in single-profile mode")
        active_id = database.get_active_candidate_id()
        synthetic = {
            "id": active_id or 0,
            "name": (base_config.get("profile") or {}).get("name", "default"),
        }
        candidates = [synthetic]

    total_applied = 0
    total_flagged = 0
    total_skipped = 0

    for candidate in candidates:
        try:
            result = run_candidate_cycle(candidate, base_config)
            total_applied += result["applied"]
            total_flagged += result["flagged_review"]
            total_skipped += result["skipped_commute"]
        except Exception as e:
            name = candidate.get("name") or f"id={candidate.get('id')}"
            logger.error(f"Candidate cycle failed for {name}: {e}")

    database.log_run(
        source="all",
        found=new_count,
        new=new_count,
        applied=total_applied,
        flagged=total_flagged,
        errors="",
    )
    summary = {
        "new_listings": new_count,
        "applied": total_applied,
        "flagged_review": total_flagged,
        "skipped_commute": total_skipped,
        "dry_run": (base_config.get("scheduler") or {}).get("dry_run", True),
    }
    logger.info(
        f"Cycle complete — applied: {total_applied}, "
        f"flagged for review: {total_flagged}, "
        f"new listings: {new_count}"
    )
    logger.info("=" * 60)
    return summary


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Autonomous job search agent")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle and exit (default without --once: loop every N hours)",
    )
    args = parser.parse_args()

    config = load_config()
    hours = config["scheduler"]["run_every_hours"]
    logger.info(f"Job agent starting. Dry run: {config['scheduler']['dry_run']}")

    run_cycle()
    if args.once:
        logger.info("Single cycle complete (--once). Exiting.")
        return

    logger.info(f"Scheduling every {hours} hour(s). Ctrl+C to stop.")
    schedule.every(hours).hours.do(run_cycle)
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
