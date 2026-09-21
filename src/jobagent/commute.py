"""
Commute agent — classifies jobs by work type and checks drive time.
"""
from __future__ import annotations

import re
import time
import logging
import requests

logger = logging.getLogger(__name__)

REMOTE_SIGNALS = [
    "remote", "work from home", "wfh", "fully remote", "100% remote",
    "anywhere in the us", "distributed team",
]
HYBRID_SIGNALS = [
    "hybrid", "partially remote", "flexible work", "2 days", "3 days",
    "in-office", "occasional travel", "on-site as needed",
]

SKIP_LOCATION_RE = re.compile(
    r"\b(remote|work from home|wfh|anywhere|nationwide|distributed|"
    r"united states|usa only|multiple locations)\b",
    re.IGNORECASE,
)

_geocode_cache: dict[str, tuple[float, float] | None] = {}
_last_nominatim_at = 0.0


def detect_work_type(job: dict) -> str:
    """
    Returns 'remote', 'hybrid', or 'onsite' based on job text signals.
    Defaults to 'onsite' if ambiguous (conservative — triggers commute check).
    """
    haystack = " ".join([
        job.get("title", ""),
        job.get("location", ""),
        job.get("description", "") or "",
    ]).lower()

    if any(s in haystack for s in REMOTE_SIGNALS):
        return "remote"
    if any(s in haystack for s in HYBRID_SIGNALS):
        return "hybrid"
    return "onsite"


def _routing_config(config: dict) -> dict:
    defaults = {
        "provider": "osrm",
        "osrm_base_url": "https://router.project-osrm.org",
        "nominatim_base_url": "https://nominatim.openstreetmap.org",
        "user_agent": "job-agent/1.0 (local job search; contact in profile.yaml)",
        "country_hint": "USA",
    }
    merged = {**defaults, **config.get("routing", {})}
    return merged


def _is_geocodable(location: str) -> bool:
    loc = (location or "").strip()
    if len(loc) < 3:
        return False
    if SKIP_LOCATION_RE.search(loc):
        return False
    return True


def _normalize_geocode_query(location: str, country_hint: str) -> str:
    loc = location.strip()
    if country_hint and country_hint.lower() not in loc.lower():
        return f"{loc}, {country_hint}"
    return loc


def _nominatim_throttle():
    global _last_nominatim_at
    elapsed = time.monotonic() - _last_nominatim_at
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_nominatim_at = time.monotonic()


def geocode(location: str, config: dict) -> tuple[float, float] | None:
    """Resolve a place name to (lat, lon) via Nominatim (OpenStreetMap)."""
    if not _is_geocodable(location):
        return None

    routing = _routing_config(config)
    cache_key = location.strip().lower()
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]

    query = _normalize_geocode_query(location, routing["country_hint"])
    base = routing["nominatim_base_url"].rstrip("/")
    headers = {"User-Agent": routing["user_agent"]}

    try:
        _nominatim_throttle()
        resp = requests.get(
            f"{base}/search",
            params={"q": query, "format": "json", "limit": 1},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            logger.warning("Nominatim: no results for '%s'", location)
            _geocode_cache[cache_key] = None
            return None
        lat = float(results[0]["lat"])
        lon = float(results[0]["lon"])
        coords = (lat, lon)
        _geocode_cache[cache_key] = coords
        return coords
    except Exception as e:
        logger.warning("Geocoding failed for '%s': %s", location, e)
        _geocode_cache[cache_key] = None
        return None


def _get_drive_minutes_osrm(
    home_coords: tuple[float, float],
    job_coords: tuple[float, float],
    config: dict,
) -> int | None:
    routing = _routing_config(config)
    base = routing["osrm_base_url"].rstrip("/")
    # OSRM expects lon,lat
    home = f"{home_coords[1]},{home_coords[0]}"
    job = f"{job_coords[1]},{job_coords[0]}"
    url = f"{base}/route/v1/driving/{home};{job}"

    try:
        resp = requests.get(
            url,
            params={"overview": "false"},
            headers={"User-Agent": routing["user_agent"]},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            logger.warning("OSRM status %s for route %s -> %s", data.get("code"), home, job)
            return None
        duration_sec = data["routes"][0]["duration"]
        return int(duration_sec // 60)
    except Exception as e:
        logger.warning("OSRM route failed: %s", e)
        return None


def _get_drive_minutes_google(job_location: str, home_location: str, api_key: str) -> int | None:
    if not api_key or api_key.startswith("YOUR_"):
        return None
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/distancematrix/json",
            params={
                "origins": home_location,
                "destinations": job_location,
                "mode": "driving",
                "key": api_key,
            },
            timeout=10,
        )
        data = resp.json()
        element = data["rows"][0]["elements"][0]
        if element["status"] == "OK":
            return element["duration"]["value"] // 60
        logger.warning("Google Maps status: %s for '%s'", element["status"], job_location)
        return None
    except Exception as e:
        logger.warning("Google drive time lookup failed for '%s': %s", job_location, e)
        return None


def get_drive_minutes(job_location: str, home_location: str, config: dict) -> int | None:
    """
    Returns drive time in minutes between home and job locations.
    Uses OSRM + Nominatim by default (free). Optional Google Maps fallback.
    """
    if not _is_geocodable(job_location):
        return None

    routing = _routing_config(config)
    provider = routing["provider"].lower()

    if provider == "google":
        return _get_drive_minutes_google(
            job_location,
            home_location,
            config.get("api", {}).get("google_maps_key", ""),
        )

    home_coords = geocode(home_location, config)
    job_coords = geocode(job_location, config)
    if not home_coords or not job_coords:
        return None
    return _get_drive_minutes_osrm(home_coords, job_coords, config)


def classify_job(job: dict, config: dict) -> dict:
    """
    Returns a dict with:
      work_type:        'remote' | 'hybrid' | 'onsite'
      commute_minutes:  int or None
      action:           'auto_apply' | 'needs_review' | 'skip'
      commute_note:     human-readable explanation
    """
    search = config["search"]
    home = config["profile"]["location"]
    auto_limit = search.get("commute_auto_apply_minutes", 30)
    review_limit = search.get("commute_review_minutes", 90)

    work_type = detect_work_type(job)
    commute_minutes = None
    action = "auto_apply"
    note = ""

    if work_type == "remote":
        action = "auto_apply"
        note = "Fully remote — no commute"

    elif work_type == "hybrid":
        job_loc = job.get("location", "")
        if job_loc:
            commute_minutes = get_drive_minutes(job_loc, home, config)

        if commute_minutes is None:
            action = "needs_review"
            note = f"Hybrid role in '{job_loc or 'unknown location'}' — drive time unknown, flagged for your review"
        elif commute_minutes <= review_limit:
            action = "needs_review"
            note = f"Hybrid — {commute_minutes} min drive. Flagged for your review (hybrid always requires your call)"
        else:
            action = "skip"
            note = f"Hybrid — {commute_minutes} min drive exceeds {review_limit} min review limit"

    elif work_type == "onsite":
        job_loc = job.get("location", "")
        if job_loc:
            commute_minutes = get_drive_minutes(job_loc, home, config)

        if commute_minutes is None:
            action = "needs_review"
            note = f"On-site in '{job_loc or 'unknown location'}' — drive time unknown, flagged for your review"
        elif commute_minutes <= auto_limit:
            action = "auto_apply"
            note = f"On-site — {commute_minutes} min drive, within {auto_limit} min auto-apply limit"
        elif commute_minutes <= review_limit:
            action = "needs_review"
            note = f"On-site — {commute_minutes} min drive. Outside auto limit ({auto_limit} min) but within review range ({review_limit} min)"
        else:
            action = "skip"
            note = f"On-site — {commute_minutes} min drive exceeds {review_limit} min limit"

    return {
        "work_type": work_type,
        "commute_minutes": commute_minutes,
        "action": action,
        "commute_note": note,
    }
