"""
Workday Jobs API crawler.

Public listings are loaded via:
  POST https://{tenant}.{cluster}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

Board URLs are discovered from employer careers pages when possible, or parsed
from a canonical workday_url / manual tenant+cluster+site config.
"""
from __future__ import annotations

import logging
import random
import re
import time

import requests

from jobagent.sources._common import (
    insert_mapped,
    source_cfg,
    source_enabled,
    title_filter_enabled,
    title_matches,
)
from jobagent.sources.workday_discover import (
    BoardSpec,
    board_page_url,
    job_posting_url,
    jobs_api_url,
    resolve_board_specs,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 20
MAX_PAGES = 5
# Clustered Workday hosts (tenant.wdN.myworkdayjobs.com). Bare hostnames rarely resolve.
COMMON_CLUSTERS = ("wd1", "wd5", "wd3", "wd503")

HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


def _base_url(tenant: str, cluster: str) -> str:
    if not cluster or cluster == "bare":
        return f"https://{tenant}.myworkdayjobs.com"
    return f"https://{tenant}.{cluster}.myworkdayjobs.com"


def _search_payload(offset: int = 0) -> dict:
    return {
        "appliedFacets": {},
        "limit": PAGE_SIZE,
        "offset": offset,
        "searchText": "",
    }


def _csrf_token(session: requests.Session, html: str) -> str | None:
    token = session.cookies.get("CALYPSO_CSRF_TOKEN")
    if token:
        return token
    match = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', html)
    return match.group(1) if match else None


def _bootstrap_session(spec: BoardSpec) -> tuple[requests.Session | None, str | None, str]:
    session = requests.Session()
    page_url = board_page_url(spec.tenant, spec.site, spec.cluster, spec.locale)
    try:
        resp = session.get(page_url, headers=HTML_HEADERS, timeout=30, allow_redirects=True)
    except Exception as e:
        logger.warning(
            "[workday] %s/%s@%s page load failed: %s",
            spec.tenant, spec.site, spec.cluster, e,
        )
        return None, None, page_url

    if resp.status_code == 404:
        return None, None, resp.url
    if resp.status_code >= 500:
        logger.warning(
            "[workday] %s/%s@%s careers page HTTP %s (bot/WAF - Playwright fallback if enabled)",
            spec.tenant, spec.site, spec.cluster, resp.status_code,
        )
        return None, None, resp.url

    csrf = _csrf_token(session, resp.text)
    if not csrf:
        logger.warning(
            "[workday] %s/%s@%s no CSRF token",
            spec.tenant, spec.site, spec.cluster,
        )
        return None, None, resp.url

    return session, csrf, resp.url


def _post_jobs(
    session: requests.Session,
    spec: BoardSpec,
    csrf: str,
    referer: str,
    offset: int,
) -> requests.Response:
    headers = {
        "User-Agent": HTML_HEADERS["User-Agent"],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": _base_url(spec.tenant, spec.cluster),
        "Referer": referer,
        "Accept-Language": "en-US,en;q=0.9",
        "X-Calypso-Csrf-Token": csrf,
    }
    return session.post(
        jobs_api_url(spec.tenant, spec.site, spec.cluster),
        json=_search_payload(offset),
        headers=headers,
        timeout=30,
    )


def _parse_jobs(data: dict, spec: BoardSpec, company_name: str) -> list[dict]:
    results = []
    for item in data.get("jobPostings", []):
        title = item.get("title", "").strip()
        if not title:
            continue

        external_path = item.get("externalPath", "")
        if not external_path:
            continue

        locations = item.get("locationsText", "") or ""
        if isinstance(locations, list):
            locations = ", ".join(locations)

        snippet = item.get("jobDescription", "") or item.get("briefDescription", "") or ""
        results.append({
            "title": title,
            "company": company_name,
            "location": locations,
            "url": job_posting_url(
                spec.tenant, spec.site, spec.cluster, external_path, spec.locale,
            ),
            "description": snippet,
            "posted_at": item.get("postedOn", "") or "",
        })
    return results


def _clusters_to_try(spec: BoardSpec, *, for_playwright: bool = False) -> list[str]:
    """
    Build cluster try-order for a board.

    HTTP bootstrap may try bare only when the board is explicitly configured as bare.
    Playwright never guesses bare hostnames — they usually do not exist (DNS fail).
    """
    if spec.cluster == "bare":
        return ["bare"]

    ordered: list[str] = []
    if spec.cluster:
        ordered.append(spec.cluster)
    for cluster in COMMON_CLUSTERS:
        if cluster not in ordered:
            ordered.append(cluster)

    if not for_playwright:
        return ordered

    return ordered


def _fetch_via_playwright(
    spec: BoardSpec,
    max_pages: int,
    browser=None,
) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("[workday] Playwright not installed - run: playwright install chromium")
        return []

    page_url = board_page_url(spec.tenant, spec.site, spec.cluster, spec.locale)
    collected: list[dict] = []
    owns_browser = browser is None

    try:
        if owns_browser:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
        else:
            pw = None

        page = browser.new_page()

        def on_response(response):
            if (
                "wday/cxs" in response.url
                and response.url.endswith("/jobs")
                and response.request.method == "POST"
            ):
                try:
                    collected.extend(response.json().get("jobPostings", []))
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        # CXS /jobs POST often fires after DOM load; give it time before closing.
        page.wait_for_timeout(5000)
        page.close()

        if owns_browser:
            browser.close()
            pw.stop()
    except Exception as e:
        logger.warning(
            "[workday] Playwright failed %s/%s@%s (%s): %s",
            spec.tenant, spec.site, spec.cluster, page_url, e,
        )
        return []

    seen: set[str] = set()
    jobs: list[dict] = []
    for item in collected[: max_pages * PAGE_SIZE]:
        path = item.get("externalPath", "")
        if not path or path in seen:
            continue
        seen.add(path)
        parsed = _parse_jobs({"jobPostings": [item]}, spec, "")
        if parsed:
            jobs.append(parsed[0])
    return jobs


def _crawl_board(
    spec: BoardSpec,
    company_name: str,
    max_pages: int,
    config: dict,
    use_playwright: bool,
    playwright_browser=None,
) -> int:
    filter_titles = title_filter_enabled(config, "workday", default=True)
    clusters_to_try = _clusters_to_try(spec)

    session = None
    csrf = None
    referer = board_page_url(spec.tenant, spec.site, spec.cluster, spec.locale)
    active = spec

    for try_cluster in clusters_to_try:
        trial = BoardSpec(
            tenant=spec.tenant,
            cluster=try_cluster,
            site=spec.site,
            locale=spec.locale,
            source_url=spec.source_url,
        )
        session, csrf, referer = _bootstrap_session(trial)
        if session and csrf:
            active = trial
            break
    else:
        session, csrf = None, None

    raw_jobs: list[dict] = []

    if session and csrf:
        for page in range(max_pages):
            offset = page * PAGE_SIZE
            try:
                resp = _post_jobs(session, active, csrf, referer, offset)
            except Exception as e:
                logger.warning("[workday] %s page %s failed: %s", company_name, page + 1, e)
                break

            if resp.status_code == 404:
                logger.warning(
                    "[workday] 404 for %s/%s@%s - check tenant/cluster/site",
                    active.tenant, active.site, active.cluster,
                )
                break
            if resp.status_code == 401:
                logger.warning("[workday] %s requires auth - skipping", company_name)
                break
            if not resp.ok:
                logger.warning(
                    "[workday] %s page %s HTTP %s: %s",
                    company_name, page + 1, resp.status_code, resp.text[:120],
                )
                break

            jobs = _parse_jobs(resp.json(), active, company_name)
            if not jobs:
                break
            raw_jobs.extend(jobs)
            if len(jobs) < PAGE_SIZE:
                break
            time.sleep(random.uniform(1.0, 2.0))
    elif use_playwright:
        logger.info("[workday] %s - trying Playwright fallback", company_name)
        pw_clusters = _clusters_to_try(spec, for_playwright=True)
        for try_cluster in pw_clusters:
            trial = BoardSpec(
                tenant=spec.tenant,
                cluster=try_cluster,
                site=spec.site,
                locale=spec.locale,
                source_url=spec.source_url,
            )
            pw_url = board_page_url(trial.tenant, trial.site, trial.cluster, trial.locale)
            logger.info("[workday] %s - Playwright @%s -> %s", company_name, try_cluster, pw_url)
            pw_jobs = _fetch_via_playwright(trial, max_pages, browser=playwright_browser)
            if pw_jobs:
                active = trial
                for job in pw_jobs:
                    job["company"] = company_name
                raw_jobs = pw_jobs
                break
            logger.info("[workday] %s - Playwright @%s returned no listings", company_name, try_cluster)

    skipped = 0
    total_inserted = 0
    for job in raw_jobs:
        if filter_titles and not title_matches(job["title"], config, "workday"):
            skipped += 1
            continue
        if insert_mapped(
            {
                "title": job["title"],
                "company": job["company"],
                "location": job["location"],
                "url": job["url"],
                "description": job["description"],
                "source": "workday",
                "salary_raw": "",
                "posted_at": job["posted_at"],
            }
        ):
            total_inserted += 1

    if skipped:
        logger.info("[workday] %s - skipped %s off-title listings", company_name, skipped)
    logger.info(
        "[workday] %s (%s) - %s matched, %s new | %s",
        company_name,
        board_page_url(active.tenant, active.site, active.cluster, active.locale),
        len(raw_jobs) - skipped,
        total_inserted,
        active.cluster,
    )
    return total_inserted


def crawl(config: dict) -> int:
    if not source_enabled(config, "workday"):
        return 0

    cfg = source_cfg(config, "workday")
    companies = cfg.get("companies") or []
    if not companies:
        logger.info("[workday] no companies configured")
        return 0

    max_pages = int(cfg.get("max_pages", MAX_PAGES))
    use_playwright = bool(cfg.get("use_playwright_fallback", True))
    total = 0
    playwright_cm = None
    playwright_browser = None

    if use_playwright:
        try:
            from playwright.sync_api import sync_playwright

            playwright_cm = sync_playwright().start()
            playwright_browser = playwright_cm.chromium.launch(headless=True)
        except ImportError:
            logger.warning("[workday] Playwright not installed - run: playwright install chromium")
            use_playwright = False
        except Exception as e:
            logger.warning("[workday] Playwright startup failed - HTTP-only mode: %s", e)
            use_playwright = False

    try:
        for entry in companies:
            if not isinstance(entry, dict):
                logger.warning("[workday] invalid company entry: %s", entry)
                continue

            name = entry.get("name") or entry.get("tenant", "Unknown")
            specs = resolve_board_specs(entry, cfg)

            if not specs:
                logger.warning("[workday] could not resolve board for %s", name)
                continue

            for spec in specs:
                logger.info(
                    "[workday] crawling %s -> %s",
                    name,
                    board_page_url(spec.tenant, spec.site, spec.cluster, spec.locale),
                )
                try:
                    total += _crawl_board(
                        spec, name, max_pages, config, use_playwright, playwright_browser,
                    )
                except Exception as e:
                    logger.error("[workday] %s failed: %s", name, e)

            time.sleep(random.uniform(2.0, 4.0))
    finally:
        if playwright_browser:
            try:
                playwright_browser.close()
            except Exception:
                pass
        if playwright_cm:
            try:
                playwright_cm.stop()
            except Exception:
                pass

    logger.info("[workday] done - %s new jobs total", total)
    return total
