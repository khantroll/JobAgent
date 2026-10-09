"""Find public job-board endpoints for company names.

Probes are low-rate GET/POST requests against official board APIs. robots.txt
disallow rules are honored. Confirmed boards and negative results are cached.
Nothing in this module submits an application or reads a logged-in session.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from jobagent.employers.boards import (
    discovered_boards_path,
    employer_names_path,
    parse_company_names,
    read_employer_names,
    record_unmatched,
    upsert_confirmed,
)
from jobagent.employers.robots import disallow_rules, origin_of, path_allowed, request_path
from jobagent.employers.slugs import BoardProbe, all_pattern_ids, candidate_probes, response_is_board

USER_AGENT = "JobAgent/2.0 (public job-board discovery; +https://github.com/khantroll/JobAgent)"
CACHE_TTL = timedelta(days=14)
WORKDAY_BODY = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}


def cache_path() -> Path:
    override = os.environ.get("JOBAGENT_EMPLOYER_CACHE_PATH", "").strip()
    if override:
        return Path(override)
    from jobagent.paths import data_dir

    return data_dir() / "employer_probe_cache.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_cache(path: Path | None = None) -> dict[str, Any]:
    target = path or cache_path()
    if not target.is_file():
        return {"entries": {}}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": {}}
    if not isinstance(loaded, dict) or not isinstance(loaded.get("entries"), dict):
        return {"entries": {}}
    return loaded


def save_cache(cache: dict[str, Any], path: Path | None = None) -> None:
    target = path or cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _fresh(entry: dict, now: datetime, ttl: timedelta) -> bool:
    try:
        checked = datetime.fromisoformat(str(entry.get("at") or ""))
    except ValueError:
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return now - checked < ttl


class Prober:
    """One request at a time. Cached negatives are not requested again until they expire."""

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        delay: float = 1.0,
        cache: dict[str, Any] | None = None,
        cache_file: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
        ttl: timedelta = CACHE_TTL,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.session.headers.setdefault("Accept", "application/json, text/plain, */*")
        self.delay = max(0.0, float(delay))
        self.cache = cache if cache is not None else load_cache(cache_file)
        self.cache.setdefault("entries", {})
        self.cache_file = cache_file
        self.sleep = sleep
        self.now = now or _now
        self.ttl = ttl
        self._last_request = 0.0
        self._robots: dict[str, list[str]] = {}

    def _pace(self) -> None:
        if self.delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < self.delay:
            self.sleep(self.delay - elapsed)

    def _mark_request(self) -> None:
        self._last_request = time.monotonic()

    def _cache_get(self, key: str) -> dict | None:
        entry = self.cache["entries"].get(key)
        if isinstance(entry, dict) and _fresh(entry, self.now(), self.ttl):
            return entry
        return None

    def _cache_put(self, key: str, entry: dict) -> None:
        self.cache["entries"][key] = entry
        if self.cache_file is not None:
            save_cache(self.cache, self.cache_file)

    def robots_for(self, url: str) -> list[str]:
        origin = origin_of(url)
        if origin in self._robots:
            return self._robots[origin]
        robots_url = origin + "/robots.txt"
        cached = self._cache_get("ROBOTS " + robots_url)
        if cached is not None:
            rules = list(cached.get("rules") or [])
            self._robots[origin] = rules
            return rules
        self._pace()
        try:
            response = self.session.get(robots_url, timeout=20)
            self._mark_request()
            rules = disallow_rules(response.text) if response.status_code == 200 else []
        except requests.RequestException:
            self._mark_request()
            rules = []
        self._robots[origin] = rules
        self._cache_put(
            "ROBOTS " + robots_url,
            {"at": self.now().isoformat(), "rules": rules},
        )
        return rules

    def probe(self, item: BoardProbe) -> dict[str, Any]:
        key = f"{item.method} {item.url}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        rules = self.robots_for(item.url)
        if not path_allowed(rules, request_path(item.url)):
            entry = {"at": self.now().isoformat(), "ok": False, "reason": "robots", "status": None}
            self._cache_put(key, entry)
            return entry
        self._pace()
        try:
            if item.method == "POST":
                response = self.session.post(item.url, json=WORKDAY_BODY, timeout=20)
            else:
                response = self.session.get(item.url, timeout=20)
            self._mark_request()
        except requests.RequestException:
            self._mark_request()
            entry = {"at": self.now().isoformat(), "ok": False, "reason": "error", "status": None}
            self._cache_put(key, entry)
            return entry
        ok = False
        reason = f"http {response.status_code}"
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = None
                reason = "not-json"
            if payload is not None and response_is_board(item.ats, payload):
                ok = True
                reason = "ok"
            elif payload is not None:
                reason = "shape"
        entry = {
            "at": self.now().isoformat(),
            "ok": ok,
            "reason": reason,
            "status": response.status_code,
        }
        self._cache_put(key, entry)
        return entry


def board_record(item: BoardProbe) -> dict[str, Any]:
    if item.ats == "workday":
        board_id = f"workday:{item.tenant}:{item.cluster}:{item.site}"
    else:
        board_id = f"{item.ats}:{item.slug}"
    return {
        "id": board_id,
        "ats": item.ats,
        "name": item.company,
        "pattern": item.pattern,
        "url": item.url,
        "slug": item.slug,
        "tenant": item.tenant,
        "cluster": item.cluster,
        "site": item.site,
        "enabled": True,
    }


def discover_name(name: str, prober: Prober, *, max_probes: int = 28, boards_file: Path | None = None) -> dict | None:
    """Return the first confirmed board, or None. Stops early to stay polite."""
    checked = 0
    for item in candidate_probes(name):
        if checked >= max_probes:
            break
        checked += 1
        result = prober.probe(item)
        if result.get("ok"):
            record = board_record(item)
            upsert_confirmed(record, boards_file)
            return record
    record_unmatched(name, boards_file)
    return None


def catalog_company_names(limit: int = 200) -> list[str]:
    from jobagent.db.connection import get_conn

    with get_conn() as conn:
        job_rows = conn.execute(
            """
            SELECT DISTINCT company FROM jobs
            WHERE trim(company) != '' AND company != 'Unknown'
            ORDER BY company LIMIT ?
            """,
            (limit,),
        ).fetchall()
        employer_rows = conn.execute(
            """
            SELECT DISTINCT name FROM candidate_employers
            WHERE trim(name) != ''
            ORDER BY name LIMIT ?
            """,
            (limit,),
        ).fetchall()
    names = [row[0] for row in list(job_rows) + list(employer_rows) if row[0]]
    return parse_company_names("\n".join(names))


def names_near_home(config: dict, session: requests.Session, *, limit: int = 25) -> list[str]:
    """One OpenStreetMap Overpass query around the configured home location.

    Optional. OpenStreetMap data is open; this sends a single small request with
    a identifying User-Agent and does not page through tiles.
    """
    from jobagent.commute import geocode

    location = str((config.get("search") or {}).get("location") or "").strip()
    if not location:
        return []
    point = geocode(location, config)
    if not point:
        return []
    lat, lon = point
    query = (
        f"[out:json][timeout:20];\n"
        f"(node[\"office\"][\"name\"](around:20000,{lat},{lon});\n"
        f"way[\"office\"][\"name\"](around:20000,{lat},{lon}););\n"
        f"out tags {int(limit)};"
    )
    response = session.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        timeout=30,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    names: list[str] = []
    for element in payload.get("elements") or []:
        tags = element.get("tags") or {}
        label = str(tags.get("name") or "").strip()
        if len(label) >= 3:
            names.append(label)
        if len(names) >= limit:
            break
    return parse_company_names("\n".join(names))


def collect_names(
    *,
    include_file: bool = True,
    include_catalog: bool = True,
    near_home: bool = False,
    config: dict | None = None,
    session: requests.Session | None = None,
    names_file: Path | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    chunks: list[str] = []
    if extra:
        chunks.extend(extra)
    if include_file:
        chunks.extend(parse_company_names(read_employer_names(names_file or employer_names_path())))
    if include_catalog:
        try:
            chunks.extend(catalog_company_names())
        except Exception:
            pass
    if near_home and config is not None:
        chunks.extend(names_near_home(config, session or requests.Session()))
    return parse_company_names("\n".join(chunks))


def discover_employers(
    names: list[str],
    *,
    delay: float = 1.0,
    max_probes: int = 28,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
    boards_file: Path | None = None,
    cache_file: Path | None = None,
) -> dict[str, Any]:
    """Probe each name. Writes confirmed and unmatched rows. Does not submit applications."""
    prober = Prober(session=session, delay=delay, cache_file=cache_file or cache_path(), sleep=sleep)
    confirmed: list[dict] = []
    unmatched: list[str] = []
    for name in names:
        found = discover_name(name, prober, max_probes=max_probes, boards_file=boards_file or discovered_boards_path())
        if found:
            confirmed.append(found)
        else:
            unmatched.append(name)
    if prober.cache_file is not None:
        save_cache(prober.cache, prober.cache_file)
    return {
        "names": len(names),
        "confirmed": confirmed,
        "unmatched": unmatched,
        "patterns": len(all_pattern_ids()),
        "boards_path": str(boards_file or discovered_boards_path()),
    }


def host_of(url: str) -> str:
    return urlparse(url).netloc
