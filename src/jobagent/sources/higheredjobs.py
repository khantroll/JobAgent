"""
HigherEdJobs — category RSS feeds (no public search API).

Feed format: https://www.higheredjobs.com/rss/categoryFeed.cfm?catID={id}
Category list: https://www.higheredjobs.com/rss/
"""
import logging
import re
import xml.etree.ElementTree as ET

import requests

from jobagent.sources._common import (
    BROWSER_HEADERS,
    insert_mapped,
    source_cfg,
    source_enabled,
    title_filter_enabled,
    title_matches,
)
from jobagent.sources.higheredjobs_catalog import parse_category_ids_text
from jobagent.sources.normalize import from_higheredjobs_rss

logger = logging.getLogger(__name__)

FEED_URL = "https://www.higheredjobs.com/rss/categoryFeed.cfm?catID={cat_id}"

# IT-related HigherEdJobs category IDs (see higheredjobs.com/rss/)
DEFAULT_CATEGORY_IDS = [
    144,  # Information Systems and Technology
    161,  # IT Support and Training
    162,  # Other IT
    173,  # Network/System Administrator
]


def _headers() -> dict:
    return {
        **BROWSER_HEADERS,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }


def _parse_company_location(description: str) -> tuple[str, str]:
    """e.g. 'State University (Little Rock, AR)' -> company, location."""
    text = (description or "").strip()
    if text.endswith(")") and "(" in text:
        idx = text.rfind("(")
        company = text[:idx].strip()
        location = text[idx + 1 : -1].strip()
        return company or "Unknown", location
    return text or "Unknown", ""


def _item_text(item: ET.Element, tag: str, ns: dict) -> str:
    node = item.find(tag)
    if node is None:
        node = item.find(f"atom:{tag}", ns)
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _item_link(item: ET.Element, ns: dict) -> str:
    link = item.find("link")
    if link is not None and link.text:
        return link.text.strip()
    guid = item.find("guid")
    if guid is not None and guid.text:
        return guid.text.strip()
    atom = item.find("atom:link", ns)
    if atom is not None and atom.get("href"):
        return atom.get("href", "").strip()
    return ""


def _crawl_feed(feed_url: str, config: dict, filter_titles: bool) -> int:
    try:
        resp = requests.get(feed_url, headers=_headers(), timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("[higheredjobs] fetch failed %s: %s", feed_url, e)
        return 0

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        logger.warning("[higheredjobs] parse error %s: %s", feed_url, e)
        return 0

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)
    count = 0

    for item in items:
        url = _item_link(item, ns)
        title = _item_text(item, "title", ns)
        if not url or not title:
            continue

        if filter_titles and not title_matches(title, config, "higheredjobs"):
            continue

        desc = _item_text(item, "description", ns) or _item_text(item, "summary", ns)
        posted = _item_text(item, "pubDate", ns)
        mapped = from_higheredjobs_rss(
            title=title, url=url, description=desc[:8000], posted_at=posted
        )
        if insert_mapped(mapped):
            count += 1

    return count


def _category_ids_for_crawl(config: dict) -> list[int]:
    """Union of candidate HEJ IDs from crawl config, then settings, then IT defaults."""
    cfg = source_cfg(config, "higheredjobs")
    from_candidates = parse_category_ids_text(config.get("_hej_category_ids") or "")
    if from_candidates:
        return from_candidates
    yaml_ids = cfg.get("category_ids") or []
    if yaml_ids:
        return [int(x) for x in yaml_ids]
    return list(DEFAULT_CATEGORY_IDS)


def crawl(config: dict) -> int:
    if not source_enabled(config, "higheredjobs"):
        return 0

    cfg = source_cfg(config, "higheredjobs")
    filter_titles = title_filter_enabled(config, "higheredjobs", default=True)
    category_ids = _category_ids_for_crawl(config)

    feed_urls: list[str] = []
    for raw in cfg.get("feeds") or []:
        url = str(raw).strip()
        if url:
            feed_urls.append(url)

    if not feed_urls:
        for cat_id in category_ids:
            feed_urls.append(FEED_URL.format(cat_id=int(cat_id)))
    logger.info("[higheredjobs] crawling %s category feed(s)", len(feed_urls))

    seen: set[str] = set()
    total = 0
    for feed_url in feed_urls:
        if feed_url in seen:
            continue
        seen.add(feed_url)
        added = _crawl_feed(feed_url, config, filter_titles)
        total += added
        logger.info("[higheredjobs] %s — %s new jobs", feed_url, added)

    logger.info("[higheredjobs] %s new jobs total", total)
    return total
