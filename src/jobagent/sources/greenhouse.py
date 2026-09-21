"""Greenhouse Job Board API — https://boards-api.greenhouse.io"""
import logging
import re

import requests

from jobagent.sources._common import (
    BROWSER_HEADERS,
    insert_mapped,
    source_cfg,
    source_enabled,
    title_filter_enabled,
    title_matches,
)
from jobagent.sources.normalize import from_greenhouse

logger = logging.getLogger(__name__)


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def crawl(config: dict) -> int:
    if not source_enabled(config, "greenhouse"):
        return 0

    cfg = source_cfg(config, "greenhouse")
    companies = cfg.get("companies") or config.get("search", {}).get("greenhouse_companies", [])
    if not companies:
        logger.info("[greenhouse] no companies configured")
        return 0

    filter_titles = title_filter_enabled(config, "greenhouse", default=True)
    total = 0

    for slug in companies:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        logger.info("[greenhouse] crawling %s", slug)
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
            if resp.status_code == 404:
                logger.warning("[greenhouse] unknown board: %s", slug)
                continue
            resp.raise_for_status()
            jobs = resp.json().get("jobs", [])
        except Exception as e:
            logger.warning("[greenhouse] %s failed: %s", slug, e)
            continue

        matched = 0
        skipped = 0
        for job in jobs:
            title = job.get("title", "")
            if filter_titles and not title_matches(title, config, "greenhouse"):
                skipped += 1
                continue

            mapped = from_greenhouse(job, slug)
            if insert_mapped(mapped):
                total += 1
                matched += 1

        logger.info(
            "[greenhouse] %s — %s total, %s title-matched (%s new overall, %s filtered out)",
            slug, len(jobs), matched, total, skipped,
        )

    return total
