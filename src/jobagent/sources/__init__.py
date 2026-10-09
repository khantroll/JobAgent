"""Job board source crawlers."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import (
    adzuna,
    ashby,
    greenhouse,
    higheredjobs,
    higheredjobs_mail,
    jsearch,
    lever,
    remotive,
    smartrecruiters,
    themuse,
    usajobs,
    workday,
)

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "ok": "ok",
    "missing_key": "missing key",
    "blocked": "blocked",
    "no_results": "no results",
    "disabled": "disabled",
    "error": "error",
    "not_configured": "not configured",
    "no_new_alerts": "no new alerts",
}


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
        from jobagent.employers.boards import slugs_for

        if not slugs_for("greenhouse", cfg.get("companies") or (config.get("search") or {}).get("greenhouse_companies")):
            return "no greenhouse companies configured"
    elif name == "lever":
        from jobagent.employers.boards import slugs_for

        if not slugs_for("lever", cfg.get("companies") or (config.get("search") or {}).get("lever_companies")):
            return "no lever companies configured"
    elif name == "workday":
        from jobagent.employers.boards import workday_entries

        if not workday_entries(cfg.get("companies") or []):
            return "no workday companies configured"
    elif name == "ashby":
        from jobagent.employers.boards import slugs_for

        if not slugs_for("ashby", cfg.get("companies") or []):
            return "no ashby companies configured"
    elif name == "smartrecruiters":
        from jobagent.employers.boards import slugs_for

        if not slugs_for("smartrecruiters", cfg.get("companies") or []):
            return "no smartrecruiters companies configured"
    elif name == "higheredjobs_mail":
        from jobagent.config import imap_configured

        if not imap_configured():
            return "not configured"
    return None


def classify_skip(reason: str | None) -> str:
    """Map a skip reason onto a status the Settings page can show."""
    text = (reason or "").strip().lower()
    if text.startswith("missing "):
        return "missing_key"
    if "not configured" in text:
        return "not_configured"
    if "disabled" in text:
        return "disabled"
    return "blocked"


def annotate_source_result(
    *,
    name: str,
    attempted: bool,
    ok: bool,
    inserted: int = 0,
    seen: int = 0,
    duplicates: int = 0,
    skipped_reason: str | None = None,
    error: str | None = None,
    blocked: bool = False,
    block_reason: str = "",
    elapsed_seconds: float = 0.0,
    status_override: str = "",
    status_detail: str = "",
) -> dict:
    """One log line and a status record. Never includes credential values."""
    if error:
        status = "error"
        detail = error
    elif skipped_reason and not attempted:
        status = classify_skip(skipped_reason)
        detail = skipped_reason
    elif status_override:
        status = status_override
        detail = status_detail or status_override
    elif blocked and inserted == 0 and seen == 0:
        status = "blocked"
        detail = block_reason or "blocked"
        skipped_reason = skipped_reason or detail
    elif attempted and inserted == 0 and seen == 0:
        status = "no_results"
        detail = "crawl finished with no listings"
    else:
        status = "ok"
        detail = f"{inserted} new, {seen} seen"
    logger.info("[%s] status=%s — %s", name, status, detail)
    return {
        "name": name,
        "attempted": attempted,
        "ok": ok,
        "inserted": inserted,
        "seen": seen,
        "duplicates": duplicates,
        "skipped_reason": skipped_reason,
        "error": error,
        "elapsed_seconds": elapsed_seconds,
        "status": status,
        "status_detail": detail,
    }


def source_status_rows(config: dict, last_runs: dict | None = None) -> list[dict]:
    """Per-source status. A live missing key or disabled flag wins over the last crawl."""
    from jobagent.sources._common import source_enabled

    previous = last_runs or {}
    names = [name for _module, name in SOURCES]
    if "rss" not in names:
        names.append("rss")
    rows = []
    for name in names:
        skip = source_skip_reason(name, config)
        live = classify_skip(skip) if skip else None
        if name != "rss" and not source_enabled(config, name):
            live = "disabled"
            skip = skip or "disabled in settings"
        last = previous.get(name) or {}
        if live in {"missing_key", "disabled", "not_configured"}:
            status = live
            detail = skip or STATUS_LABELS[live]
        elif live == "blocked" and not last:
            status = "blocked"
            detail = skip or "blocked"
        elif last:
            status = str(last.get("status") or "")
            detail = str(last.get("status_detail") or last.get("skipped_reason") or last.get("error") or "")
            if not status:
                if last.get("error"):
                    status, detail = "error", str(last.get("error"))
                elif last.get("skipped_reason"):
                    status = classify_skip(str(last.get("skipped_reason")))
                    detail = str(last.get("skipped_reason"))
                elif last.get("attempted") and not last.get("inserted") and not last.get("seen"):
                    status, detail = "no_results", "crawl finished with no listings"
                elif last.get("attempted"):
                    status = "ok"
                    detail = detail or f"{last.get('inserted') or 0} new, {last.get('seen') or 0} seen"
                else:
                    status, detail = "ok", "ready — not crawled yet"
            if live == "blocked":
                status = "blocked"
                detail = skip or detail
        else:
            status, detail = "ok", "ready — not crawled yet"
        rows.append(
            {
                "name": name,
                "status": status,
                "status_label": STATUS_LABELS.get(status, status),
                "detail": detail,
            }
        )
    return rows

# (module, log name)
SOURCES = [
    (adzuna, "adzuna"),
    (remotive, "remotive"),
    (usajobs, "usajobs"),
    (jsearch, "jsearch"),
    (themuse, "themuse"),
    (higheredjobs, "higheredjobs"),
    (higheredjobs_mail, "higheredjobs_mail"),
    (greenhouse, "greenhouse"),
    (lever, "lever"),
    (ashby, "ashby"),
    (smartrecruiters, "smartrecruiters"),
    (workday, "workday"),
]


def _execute_source(name: str, crawl_fn, config: dict) -> dict:
    """Run one crawler. Records status and never raises."""
    from jobagent.sources._common import track_inserts

    skip = source_skip_reason(name, config)
    if skip:
        return annotate_source_result(
            name=name, attempted=False, ok=True, skipped_reason=skip
        )
    started = time.perf_counter()
    try:
        with track_inserts() as counters:
            returned = crawl_fn(config)
        record = annotate_source_result(
            name=name,
            attempted=True,
            ok=True,
            inserted=counters.inserted,
            seen=counters.seen,
            duplicates=counters.duplicates,
            blocked=counters.blocked,
            block_reason=counters.block_reason,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            status_override=counters.status_override,
            status_detail=counters.status_detail,
        )
        record["returned"] = int(returned) if isinstance(returned, int) else counters.inserted
        return record
    except Exception as exc:
        logger.error("[%s] crawler error: %s", name, exc)
        record = annotate_source_result(
            name=name,
            attempted=True,
            ok=False,
            error=str(exc),
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
        record["returned"] = 0
        return record


def run_all_sources(config: dict) -> int:
    """Run all enabled source crawlers. Returns count of newly inserted jobs."""
    from jobagent.sources._common import source_enabled

    runs: list[dict] = []
    tasks = []
    for module, name in SOURCES:
        if source_enabled(config, name):
            tasks.append((module.crawl, name))
        else:
            runs.append(
                annotate_source_result(
                    name=name,
                    attempted=False,
                    ok=True,
                    skipped_reason="disabled in settings",
                )
            )

    if not tasks:
        logger.warning("No job sources enabled in config sources.*")
        config["_source_runs"] = runs
        return 0

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(_execute_source, name, fn, config): name for fn, name in tasks}
        for future in as_completed(futures):
            name = futures[future]
            try:
                runs.append(future.result())
            except Exception as exc:
                logger.error("[%s] crawler error: %s", name, exc)
                failed = annotate_source_result(
                    name=name, attempted=True, ok=False, error=str(exc)
                )
                failed["returned"] = 0
                runs.append(failed)
    total = sum(int(row.pop("returned", 0)) for row in runs)
    config["_source_runs"] = runs
    logger.info("[sources] done - %s new jobs total", total)
    return total
