"""Adzuna job search API — https://developer.adzuna.com"""
import logging

import requests

from jobagent.sources._common import (
    BROWSER_HEADERS,
    api_credentials,
    home_location,
    insert_mapped,
    search_queries,
    source_cfg,
    source_enabled,
    title_filter_enabled,
    title_matches,
)
from jobagent.sources.normalize import from_adzuna

logger = logging.getLogger(__name__)

BASE = "https://api.adzuna.com/v1/api/jobs"


def crawl(config: dict) -> int:
    if not source_enabled(config, "adzuna"):
        return 0

    api = api_credentials(config)
    app_id = api.get("adzuna_app_id", "")
    app_key = api.get("adzuna_app_key", "")
    if not app_id or not app_key or str(app_id).startswith("YOUR_"):
        logger.warning("[adzuna] skipped - set api.adzuna_app_id and api.adzuna_app_key")
        return 0

    cfg = source_cfg(config, "adzuna")
    filter_titles = title_filter_enabled(config, "adzuna", default=True)
    country = cfg.get("country", "us")
    where = cfg.get("where") or home_location(config)
    distance = cfg.get("distance_miles", 50)
    max_pages = int(cfg.get("max_pages", 2))
    results_per_page = min(int(cfg.get("results_per_page", 50)), 50)
    salary_min = config.get("search", {}).get("salary_min")

    total = 0
    for query in search_queries(config, "adzuna"):
        for page in range(1, max_pages + 1):
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": results_per_page,
                "what": query,
                "where": where,
                "distance": distance,
                "max_days_old": cfg.get("max_days_old", 30),
                "sort_by": "date",
            }
            if salary_min:
                params["salary_min"] = salary_min

            url = f"{BASE}/{country}/search/{page}"
            try:
                resp = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning("[adzuna] %s page %s failed: %s", query, page, e)
                break

            results = data.get("results", [])
            if not results:
                break

            skipped = 0
            page_new = 0
            for job in results:
                title = job.get("title", "")
                if filter_titles and not title_matches(title, config, "adzuna"):
                    skipped += 1
                    continue

                mapped = from_adzuna(job)
                if insert_mapped(mapped):
                    total += 1
                    page_new += 1

            matched = len(results) - skipped
            logger.info(
                "[adzuna] %s page %s - %s total, %s title-matched (%s new, %s filtered)",
                query, page, len(results), matched, page_new, skipped,
            )

    logger.info("[adzuna] %s new jobs", total)
    return total
