"""Shared helpers for job source crawlers."""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from jobagent.db import jobs as db

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Obvious non-local countries/regions (US territories listed separately).
_FOREIGN_LOCATION_MARKERS = (
    "belgium", "germany", "france", "italy", "spain", "netherlands", "poland",
    "canada", "mexico", "united kingdom", " uk", "ireland", "australia",
    "japan", "korea", "china", "india", "brazil", "singapore", "hawaii only",
)

_US_STATE_NAMES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
    "ca": "california", "co": "colorado", "ct": "connecticut", "de": "delaware",
    "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
    "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
    "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada",
    "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york",
    "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah",
    "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
}

_REMOTE_LOCATION_SIGNALS = (
    "remote", "telework", "work from home", "wfh", "virtual",
    "anywhere in the u.s", "anywhere in us", "nationwide",
)


def source_enabled(config: dict, name: str) -> bool:
    return bool(config.get("sources", {}).get(name, {}).get("enabled", False))


def source_cfg(config: dict, name: str) -> dict:
    return config.get("sources", {}).get(name, {})


def search_queries(config: dict, source_name: str, default_max: int = 5) -> list[str]:
    """Per-source query list, else first N titles from search preferences."""
    cfg = source_cfg(config, source_name)
    if cfg.get("queries"):
        return list(cfg["queries"])
    max_q = cfg.get("max_queries", default_max)
    return list(config.get("search", {}).get("titles", []))[:max_q]


def home_location(config: dict) -> str:
    return config.get("profile", {}).get("location", "") or config.get("search", {}).get(
        "location", ""
    )


def location_filter_enabled(config: dict, source_name: str, default: bool = True) -> bool:
    search = config.get("search", {})
    if "filter_by_location" in search and not search["filter_by_location"]:
        return False
    cfg = source_cfg(config, source_name)
    if "filter_by_location" in cfg:
        return bool(cfg["filter_by_location"])
    return default


def _home_location_tokens(config: dict) -> set[str]:
    home = home_location(config).strip()
    if not home:
        return set()
    tokens: set[str] = set()
    for part in re.split(r"[,/]", home):
        part = part.strip().lower()
        if not part:
            continue
        tokens.add(part)
        if len(part) == 2 and part in _US_STATE_NAMES:
            tokens.add(_US_STATE_NAMES[part])
        for abbr, name in _US_STATE_NAMES.items():
            if part == name:
                tokens.add(abbr)
    return tokens


def location_acceptable(location: str, config: dict, source_name: str = "") -> bool:
    """
    True if a job location is worth keeping for this search profile.
    Rejects obvious overseas postings; accepts remote/nationwide and home-area matches.
    """
    if not location_filter_enabled(config, source_name):
        return True
    loc = (location or "").strip()
    if not loc:
        return True

    hay = f" {loc.lower()} "
    if config.get("search", {}).get("location_accept_remote", True):
        if any(sig in hay for sig in _REMOTE_LOCATION_SIGNALS):
            return True

    if any(marker in hay for marker in _FOREIGN_LOCATION_MARKERS):
        return False

    tokens = _home_location_tokens(config)
    if not tokens:
        return True

    if any(token in hay for token in tokens):
        return True

    return False


@dataclass
class InsertCounters:
    seen: int = 0
    inserted: int = 0
    duplicates: int = 0
    rejected: int = 0


_COUNTERS: ContextVar[InsertCounters | None] = ContextVar("jobagent_insert_counters", default=None)


@contextmanager
def track_inserts() -> Iterator[InsertCounters]:
    counters = InsertCounters()
    token = _COUNTERS.set(counters)
    try:
        yield counters
    finally:
        _COUNTERS.reset(token)


def insert_mapped(mapped: dict | None) -> bool:
    if not mapped:
        counters = _COUNTERS.get()
        if counters is not None:
            counters.seen += 1
            counters.rejected += 1
        return False
    return insert_job(**mapped)


def insert_job(
    *,
    title: str,
    company: str,
    url: str,
    description: str = "",
    location: str = "",
    source: str,
    salary_raw: str = "",
    posted_at: str = "",
) -> bool:
    counters = _COUNTERS.get()
    if counters is not None:
        counters.seen += 1
    if not title or not url:
        if counters is not None:
            counters.rejected += 1
        return False
    # Crawler inserts: shared catalog row + a blank match for every searching candidate.
    inserted = db.upsert_job({
        "title": title.strip(),
        "company": (company or "Unknown").strip(),
        "location": (location or "").strip(),
        "url": url.strip(),
        "description": (description or "")[:8000],
        "source": source,
        "salary_raw": salary_raw or "",
        "posted_at": posted_at or "",
    })
    if counters is not None:
        if inserted:
            counters.inserted += 1
        else:
            counters.duplicates += 1
    return inserted


def api_credentials(config: dict) -> dict:
    return config.get("api", {})


def title_filter_enabled(config: dict, source_name: str, default: bool = True) -> bool:
    cfg = source_cfg(config, source_name)
    if "filter_by_titles" in cfg:
        return bool(cfg["filter_by_titles"])
    return default


def title_matches(title: str, config: dict, source_name: str = "") -> bool:
    """True if the job title matches configured target roles."""
    title_lower = (title or "").lower().strip()
    if not title_lower:
        return False

    queries = (
        search_queries(config, source_name, default_max=20)
        if source_name
        else list(config.get("search", {}).get("titles", []))
    )
    for query in queries:
        q = query.lower().strip()
        if not q:
            continue
        if q in title_lower or title_lower in q:
            return True
        # Allow word-order differences: all significant words from query appear in title
        words = [w for w in re.split(r"[\s/\-]+", q) if len(w) > 2]
        if len(words) >= 2 and all(w in title_lower for w in words):
            return True
    return False
