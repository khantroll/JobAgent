"""Ashby public job-board API — https://api.ashbyhq.com/posting-api/job-board/{slug}"""
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
from jobagent.sources.normalize import from_ashby

logger = logging.getLogger(__name__)


def crawl(config: dict) -> int:
    if not source_enabled(config, "ashby"):
        return 0
    from jobagent.employers.boards import slugs_for

    cfg = source_cfg(config, "ashby")
    companies = slugs_for("ashby", cfg.get("companies") or [])
    if not companies:
        logger.info("[ashby] no companies configured")
        return 0
    filter_titles = title_filter_enabled(config, "ashby", default=True)
    total = 0
    for slug in companies:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        logger.info("[ashby] crawling %s", slug)
        try:
            response = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
            if response.status_code == 404:
                logger.warning("[ashby] unknown board: %s", slug)
                continue
            response.raise_for_status()
            jobs = response.json().get("jobs") or []
        except Exception as exc:
            logger.warning("[ashby] %s failed: %s", slug, exc)
            continue
        for job in jobs:
            title = job.get("title") or ""
            if filter_titles and not title_matches(title, config, "ashby"):
                continue
            if insert_mapped(from_ashby(job, slug)):
                total += 1
    logger.info("[ashby] %s new jobs", total)
    return total
