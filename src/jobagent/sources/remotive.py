"""Remotive public API — https://remotive.com/remote-jobs/api"""
import logging
import re

import requests

from jobagent.sources._common import (
    BROWSER_HEADERS,
    insert_mapped,
    search_queries,
    source_cfg,
    source_enabled,
)
from jobagent.sources.normalize import from_remotive

logger = logging.getLogger(__name__)

API_URL = "https://remotive.com/api/remote-jobs"


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def crawl(config: dict) -> int:
    if not source_enabled(config, "remotive"):
        return 0

    cfg = source_cfg(config, "remotive")
    total = 0
    categories = cfg.get("categories") or ["devops"]
    if cfg.get("search_queries") is not None:
        searches = list(cfg["search_queries"])
    else:
        searches = search_queries(config, "remotive", default_max=10)

    seen_urls: set[str] = set()

    for category in categories:
        for search in searches:
            params: dict = {"limit": int(cfg.get("limit", 100))}
            if category:
                params["category"] = category
            if search:
                params["search"] = search

            try:
                resp = requests.get(API_URL, params=params, headers=BROWSER_HEADERS, timeout=30)
                resp.raise_for_status()
                jobs = resp.json().get("jobs", [])
            except Exception as e:
                logger.warning("[remotive] fetch failed (%s): %s", params, e)
                continue

            for job in jobs:
                url = job.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                mapped = from_remotive(job)
                if insert_mapped(mapped):
                    total += 1

            logger.info("[remotive] category=%s search=%s — %s jobs", category, search, len(jobs))

    logger.info("[remotive] %s new jobs", total)
    return total
