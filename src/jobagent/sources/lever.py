"""Lever Postings API — https://github.com/lever/postings-api"""
import logging

import requests

from jobagent.sources._common import (
    BROWSER_HEADERS,
    insert_mapped,
    source_cfg,
    source_enabled,
    title_filter_enabled,
    title_matches,
)
from jobagent.sources.normalize import from_lever

logger = logging.getLogger(__name__)


def crawl(config: dict) -> int:
    if not source_enabled(config, "lever"):
        return 0

    cfg = source_cfg(config, "lever")
    companies = cfg.get("companies") or config.get("search", {}).get("lever_companies", [])
    instance = cfg.get("instance", "global")
    base = (
        "https://api.eu.lever.co/v0/postings"
        if instance == "eu"
        else "https://api.lever.co/v0/postings"
    )

    if not companies:
        logger.info("[lever] no companies configured")
        return 0

    filter_titles = title_filter_enabled(config, "lever", default=True)
    total = 0

    for slug in companies:
        url = f"{base}/{slug}?mode=json"
        logger.info("[lever] crawling %s", slug)
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
            if resp.status_code == 404:
                logger.warning("[lever] unknown board: %s", slug)
                continue
            resp.raise_for_status()
            jobs = resp.json()
        except Exception as e:
            logger.warning("[lever] %s failed: %s", slug, e)
            continue

        if not isinstance(jobs, list):
            continue

        matched = 0
        skipped = 0
        for job in jobs:
            title = job.get("text", "")
            if filter_titles and not title_matches(title, config, "lever"):
                skipped += 1
                continue

            mapped = from_lever(job, slug)
            if insert_mapped(mapped):
                total += 1
                matched += 1

        logger.info(
            "[lever] %s - %s total, %s title-matched (%s new overall, %s filtered out)",
            slug, len(jobs), matched, total, skipped,
        )

    logger.info("[lever] %s new jobs", total)
    return total
