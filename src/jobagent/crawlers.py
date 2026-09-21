"""
Crawler agents — parallel job discovery from configured sources.
"""
import logging
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from jobagent.sources import run_all_sources
from jobagent.sources._common import BROWSER_HEADERS, insert_mapped
from jobagent.sources.normalize import from_rss

logger = logging.getLogger(__name__)


def _item_link(item, ns: dict) -> str:
    link = item.find("link")
    if link is not None:
        href = link.get("href")
        if href:
            return href.strip()
        if link.text:
            return link.text.strip()
    atom_link = item.find("atom:link", ns)
    if atom_link is not None and atom_link.get("href"):
        return atom_link.get("href").strip()
    node = item.find("id") or item.find("atom:id", ns)
    return (node.text or "").strip() if node is not None else ""


def crawl_rss(feed_url: str) -> int:
    logger.info("[rss] crawling %s", feed_url)
    try:
        r = requests.get(feed_url, timeout=20, headers=BROWSER_HEADERS)
        r.raise_for_status()
    except Exception as e:
        logger.warning("[rss] fetch error %s: %s", feed_url, e)
        return 0

    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        logger.warning("[rss] parse error for %s: %s", feed_url, e)
        return 0

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)
    count = 0

    for item in items:
        def t(tag):
            node = item.find(tag)
            if node is None:
                node = item.find(f"atom:{tag}", ns)
            if node is None:
                return ""
            return "".join(node.itertext()).strip()

        url = _item_link(item, ns)
        title = t("title")
        desc = re.sub(r"<[^>]+>", " ", t("description") or t("summary") or t("content")).strip()

        if not url or not title:
            continue

        mapped = from_rss(
            title=title,
            url=url,
            description=desc[:8000],
            location=t("location") or t("region") or "",
        )
        if insert_mapped(mapped):
            count += 1

    logger.info("[rss] %s new jobs from %s", count, feed_url)
    return count


def run_all_crawlers(config: dict) -> int:
    """Crawl all configured sources. Returns count of newly inserted jobs."""
    total = run_all_sources(config)

    rss_feeds = config.get("search", {}).get("rss_feeds", [])
    if rss_feeds:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(crawl_rss, url): url for url in rss_feeds}
            for future in as_completed(futures):
                try:
                    total += future.result()
                except Exception as e:
                    logger.error("[rss] error: %s", e)

    logger.info("[crawlers] done - %s new jobs total", total)
    return total
