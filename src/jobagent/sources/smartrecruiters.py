"""SmartRecruiters public postings API."""
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
from jobagent.sources.normalize import from_smartrecruiters

logger = logging.getLogger(__name__)


def crawl(config: dict) -> int:
    if not source_enabled(config, "smartrecruiters"):
        return 0
    from jobagent.employers.boards import slugs_for

    cfg = source_cfg(config, "smartrecruiters")
    companies = slugs_for("smartrecruiters", cfg.get("companies") or [])
    if not companies:
        logger.info("[smartrecruiters] no companies configured")
        return 0
    filter_titles = title_filter_enabled(config, "smartrecruiters", default=True)
    total = 0
    for slug in companies:
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        logger.info("[smartrecruiters] crawling %s", slug)
        try:
            response = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
            if response.status_code == 404:
                logger.warning("[smartrecruiters] unknown board: %s", slug)
                continue
            response.raise_for_status()
            jobs = response.json().get("content") or []
        except Exception as exc:
            logger.warning("[smartrecruiters] %s failed: %s", slug, exc)
            continue
        for job in jobs:
            title = job.get("name") or ""
            if filter_titles and not title_matches(title, config, "smartrecruiters"):
                continue
            if insert_mapped(from_smartrecruiters(job, slug)):
                total += 1
    logger.info("[smartrecruiters] %s new jobs", total)
    return total
