"""JSearch via RapidAPI — https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch"""
import logging

import requests

from jobagent.sources._common import (
    api_credentials,
    home_location,
    insert_mapped,
    search_queries,
    source_cfg,
    source_enabled,
    title_filter_enabled,
    title_matches,
)
from jobagent.sources.normalize import from_jsearch

logger = logging.getLogger(__name__)

SEARCH_URL = "https://jsearch.p.rapidapi.com/search"


def crawl(config: dict) -> int:
    if not source_enabled(config, "jsearch"):
        return 0

    api = api_credentials(config)
    rapidapi_key = api.get("rapidapi_key", "")
    if not rapidapi_key or str(rapidapi_key).startswith("YOUR_"):
        logger.warning("[jsearch] skipped - set api.rapidapi_key (RapidAPI)")
        return 0

    cfg = source_cfg(config, "jsearch")
    filter_titles = title_filter_enabled(config, "jsearch", default=True)
    location = cfg.get("location") or home_location(config)
    num_pages = int(cfg.get("num_pages", 2))
    country = cfg.get("country", "us")
    date_posted = cfg.get("date_posted", "month")

    headers = {
        "x-rapidapi-key": rapidapi_key,
        "x-rapidapi-host": "jsearch.p.rapidapi.com",
    }

    total = 0
    for title in search_queries(config, "jsearch"):
        query = f"{title} in {location}" if location else title
        params = {
            "query": query,
            "page": 1,
            "num_pages": num_pages,
            "country": country,
            "date_posted": date_posted,
            "employment_types": "FULLTIME",
        }
        if cfg.get("remote_only"):
            params["work_from_home"] = "true"

        try:
            resp = requests.get(SEARCH_URL, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            jobs = resp.json().get("data", [])
        except Exception as e:
            logger.warning("[jsearch] %s failed: %s", query, e)
            continue

        skipped = 0
        matched = 0
        for job in jobs:
            job_title = job.get("job_title", "")
            if filter_titles and not title_matches(job_title, config, "jsearch"):
                skipped += 1
                continue

            mapped = from_jsearch(job)
            if insert_mapped(mapped):
                total += 1
                matched += 1

        logger.info(
            "[jsearch] %s - %s total, %s title-matched (%s new, %s filtered)",
            query, len(jobs), len(jobs) - skipped, matched, skipped,
        )

    logger.info("[jsearch] %s new jobs", total)
    return total
