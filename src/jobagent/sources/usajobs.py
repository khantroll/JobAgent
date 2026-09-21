"""USAJOBS Search API — https://developer.usajobs.gov"""
import logging

import requests

from jobagent.sources._common import (
    api_credentials,
    home_location,
    insert_mapped,
    location_acceptable,
    search_queries,
    source_cfg,
    source_enabled,
)
from jobagent.sources.normalize import from_usajobs

logger = logging.getLogger(__name__)

SEARCH_URL = "https://data.usajobs.gov/api/search"


def crawl(config: dict) -> int:
    if not source_enabled(config, "usajobs"):
        return 0

    api = api_credentials(config)
    auth_key = api.get("usajobs_api_key", "")
    user_agent = api.get("usajobs_user_agent", "") or config.get("profile", {}).get("email", "")
    if not auth_key or str(auth_key).startswith("YOUR_"):
        logger.warning("[usajobs] skipped — set api.usajobs_api_key and api.usajobs_user_agent")
        return 0
    if not user_agent:
        logger.warning("[usajobs] skipped — usajobs_user_agent (your registration email) required")
        return 0

    cfg = source_cfg(config, "usajobs")
    location = cfg.get("location") or home_location(config)
    radius = int(cfg.get("radius", 75))
    results_per_page = min(int(cfg.get("results_per_page", 50)), 500)
    max_pages = int(cfg.get("max_pages", 2))

    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": user_agent,
        "Authorization-Key": auth_key,
    }

    total = 0
    for keyword in search_queries(config, "usajobs"):
        for page in range(1, max_pages + 1):
            params = {
                "Keyword": keyword,
                "LocationName": location,
                "Radius": radius,
                "ResultsPerPage": results_per_page,
                "Page": page,
            }
            if cfg.get("job_category_code"):
                params["JobCategoryCode"] = cfg["job_category_code"]

            try:
                resp = requests.get(SEARCH_URL, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as e:
                logger.warning("[usajobs] %s page %s failed: %s", keyword, page, e)
                break

            items = payload.get("SearchResult", {}).get("SearchResultItems", []) or []
            if not items:
                break

            for item in items:
                mapped = from_usajobs(item)
                if not mapped:
                    continue
                if not location_acceptable(mapped["location"], config, "usajobs"):
                    continue
                if insert_mapped(mapped):
                    total += 1

            logger.info("[usajobs] %s page %s — %s results", keyword, page, len(items))

    logger.info("[usajobs] %s new jobs", total)
    return total
