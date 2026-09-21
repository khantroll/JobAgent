"""Job board source crawlers."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import (
    adzuna,
    greenhouse,
    higheredjobs,
    jsearch,
    lever,
    remotive,
    themuse,
    usajobs,
    workday,
)

def source_skip_reason(name: str, config: dict) -> str | None:
    """Return a human reason if this source will no-op, else None."""
    from jobagent.sources._common import api_credentials, source_cfg, source_enabled

    if name == "rss":
        feeds = (config.get("search") or {}).get("rss_feeds") or []
        if not feeds:
            return "no rss_feeds configured"
        return None
    if not source_enabled(config, name):
        return "disabled in settings"
    api = api_credentials(config)
    cfg = source_cfg(config, name)
    if name == "adzuna":
        app_id = str(api.get("adzuna_app_id") or "")
        app_key = str(api.get("adzuna_app_key") or "")
        if not app_id or not app_key or app_id.startswith("YOUR_"):
            return "missing ADZUNA_APP_ID / ADZUNA_APP_KEY"
    elif name == "jsearch":
        key = str(api.get("rapidapi_key") or "")
        if not key or key.startswith("YOUR_"):
            return "missing RAPIDAPI_KEY"
    elif name == "usajobs":
        key = str(api.get("usajobs_api_key") or "")
        agent = str(api.get("usajobs_user_agent") or "") or str(
            (config.get("profile") or {}).get("email") or ""
        )
        if not key or key.startswith("YOUR_"):
            return "missing USAJOBS_API_KEY"
        if not agent:
            return "missing USAJOBS_USER_AGENT"
    elif name == "greenhouse":
        if not (cfg.get("companies") or (config.get("search") or {}).get("greenhouse_companies")):
            return "no greenhouse companies configured"
    elif name == "lever":
        if not (cfg.get("companies") or (config.get("search") or {}).get("lever_companies")):
            return "no lever companies configured"
    elif name == "workday":
        if not (cfg.get("companies") or []):
            return "no workday companies configured"
    return None

# (module, log name)
SOURCES = [
    (adzuna, "adzuna"),
    (remotive, "remotive"),
    (usajobs, "usajobs"),
    (jsearch, "jsearch"),
    (themuse, "themuse"),
    (higheredjobs, "higheredjobs"),
    (greenhouse, "greenhouse"),
    (lever, "lever"),
    (workday, "workday"),
]


def run_all_sources(config: dict) -> int:
    """Run all enabled source crawlers. Returns count of newly inserted jobs."""
    tasks = []
    for module, name in SOURCES:
        from jobagent.sources._common import source_enabled
        if source_enabled(config, name):
            tasks.append((module.crawl, name))

    if not tasks:
        logger.warning("No job sources enabled in config sources.*")
        return 0

    total = 0
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn, config): name for fn, name in tasks}
        for future in as_completed(futures):
            name = futures[future]
            try:
                count = future.result()
                total += count
            except Exception as e:
                logger.error("[%s] crawler error: %s", name, e)

    logger.info("[sources] done - %s new jobs total", total)
    return total
