"""The Muse public jobs API — https://www.themuse.com/developers/api/v2"""
import logging
import re

import requests

from jobagent.sources._common import (
    BROWSER_HEADERS,
    api_credentials,
    insert_mapped,
    search_queries,
    source_cfg,
    source_enabled,
)
from jobagent.sources.normalize import from_themuse

logger = logging.getLogger(__name__)

# v2 needs API key; legacy public endpoint works without one
PUBLIC_JOBS_URL = "https://www.themuse.com/api/public/jobs"


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def _location_str(job: dict) -> str:
    locs = job.get("locations") or []
    names = [loc.get("name", "") for loc in locs if isinstance(loc, dict)]
    return ", ".join(n for n in names if n)


def crawl(config: dict) -> int:
    if not source_enabled(config, "themuse"):
        return 0

    cfg = source_cfg(config, "themuse")
    api = api_credentials(config)
    api_key = api.get("themuse_api_key", "")

    headers = dict(BROWSER_HEADERS)
    if api_key and not str(api_key).startswith("YOUR_"):
        headers["X-Muse-Api-Key"] = api_key

    max_pages = int(cfg.get("max_pages", 3))
    location = cfg.get("location", "")
    categories = cfg.get("categories") or []
    levels = cfg.get("levels") or ["Senior Level", "Mid Level"]

    total = 0
    filter_titles = cfg.get("filter_by_titles", False)
    title_keywords = search_queries(config, "themuse", default_max=8) if filter_titles else []

    for page in range(1, max_pages + 1):
        params: dict = {"page": page, "descending": "true"}
        if location:
            params["location"] = location
        if levels:
            params["level"] = levels[0] if len(levels) == 1 else None
        for cat in categories[:1] or [None]:
            if cat:
                params["category"] = cat
            try:
                resp = requests.get(PUBLIC_JOBS_URL, params=params, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning("[themuse] page %s failed: %s", page, e)
                break

            results = data.get("results", [])
            if not results:
                break

            for job in results:
                title = job.get("name", "")
                if title_keywords and not any(
                    q.lower() in title.lower() for q in title_keywords
                ):
                    continue

                mapped = from_themuse(job)
                if insert_mapped(mapped):
                    total += 1

            logger.info("[themuse] page %s — %s results", page, len(results))

    logger.info("[themuse] %s new jobs", total)
    return total
