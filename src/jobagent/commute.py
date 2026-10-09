"""
Commute agent — classifies jobs by work type and checks drive time.
"""
from __future__ import annotations

import math
import re
import time
import logging
import requests

logger = logging.getLogger(__name__)

# Straight-line fallback when the router is unavailable.
# 2 minutes per mile is about 30 mph effective, so a job past the review
# window is still excluded instead of being treated as "distance unknown".
STRAIGHT_LINE_MINUTES_PER_MILE = 2.0

_REMOTE_WORD = re.compile(
    r"\b(?:remote|work from home|wfh|anywhere|nationwide|distributed|telework|telecommute)\b",
    re.IGNORECASE,
)
_NEGATED_REMOTE = re.compile(
    r"\b(?:not|no|non-?|isn['’]t|is not|isnt)\s+(?:a\s+|an\s+)?(?:fully\s+|100%\s*)?remote\b",
    re.IGNORECASE,
)
_INCIDENTAL_REMOTE = re.compile(
    r"\bremote\s+(?:access|desktop|support|login|tools?|into|workstation)\b",
    re.IGNORECASE,
)
_EXPLICIT_REMOTE = re.compile(
    r"\b(?:fully\s+remote|100%\s*remote|work\s+from\s+home|\bwfh\b|remote[- ]only)\b",
    re.IGNORECASE,
)
_HYBRID = re.compile(
    r"\b(?:hybrid|partially\s+remote|flexible\s+work)\b|\b[23]\s+days?(?:\s+(?:a|per)\s+week|\s*/\s*week)\b",
    re.IGNORECASE,
)
_COUNTRY_ONLY = re.compile(
    r"^(?:united states(?: of america)?|u\.s\.a\.?|usa|u\.s\.|us)$",
    re.IGNORECASE,
)
_COUNTRY_SUFFIX = re.compile(
    r"(?:,|\s)+\b(?:united states(?: of america)?|u\.s\.a\.?|usa|u\.s\.)\b\.?\s*$",
    re.IGNORECASE,
)
_NON_PLACE = re.compile(
    r"\b(?:anywhere|nationwide|distributed|multiple locations|various locations|usa only)\b",
    re.IGNORECASE,
)

_geocode_cache: dict[str, tuple[float, float] | None] = {}
_last_nominatim_at = 0.0


def clear_geocode_cache() -> None:
    """Drop cached geocodes. Tests and a fresh cycle can call this."""
    global _last_nominatim_at
    _geocode_cache.clear()
    _last_nominatim_at = 0.0


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def _limit_minutes(value, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _location_is_remote_label(location: str) -> bool:
    """True when the location is a remote/nationwide label rather than a place."""
    loc = (location or "").strip()
    if not loc or not _REMOTE_WORD.search(loc):
        return False
    stripped = _REMOTE_WORD.sub(" ", loc)
    stripped = _COUNTRY_SUFFIX.sub("", stripped)
    stripped = re.sub(
        r"\b(?:united states(?: of america)?|usa|u\.s\.a\.?|u\.s\.|us|only|job|jobs|position|role|the)\b",
        " ",
        stripped,
        flags=re.IGNORECASE,
    )
    words = [w for w in re.sub(r"[^A-Za-z]", " ", stripped).split() if len(w) > 1]
    return not words


def prepare_geocode_query(location: str) -> str | None:
    """Return a place string Nominatim can resolve, or None when it is not a place.

    A trailing country name is removed so "Little Rock, AR, United States" still
    geocodes. Country-only and remote labels do not.
    """
    loc = (location or "").strip()
    if len(loc) < 3:
        return None
    if _location_is_remote_label(loc):
        return None
    cleaned = re.sub(r"\([^)]*\b(?:remote|hybrid)\b[^)]*\)", " ", loc, flags=re.IGNORECASE)
    cleaned = _COUNTRY_SUFFIX.sub("", cleaned).strip(" ,")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    if len(cleaned) < 3 or _COUNTRY_ONLY.match(cleaned):
        return None
    if _NON_PLACE.search(cleaned) and _location_is_remote_label(cleaned):
        return None
    if _NON_PLACE.search(cleaned) and not re.search(r"[A-Za-z]{3,}", _NON_PLACE.sub(" ", cleaned)):
        return None
    return cleaned


def detect_work_type(job: dict) -> str:
    """
    Returns 'remote', 'hybrid', or 'onsite'.

    A stray "remote" in the description (remote desktop, "not remote") does not
    skip the commute check. Ambiguous jobs stay onsite so distance still applies.
    """
    location = str(job.get("location") or "")
    title = str(job.get("title") or "")
    description = str(job.get("description") or "")
    text = f"{title}\n{description}"
    cleaned = _NEGATED_REMOTE.sub(" ", text)
    cleaned = _INCIDENTAL_REMOTE.sub(" ", cleaned)

    if _location_is_remote_label(location):
        return "remote"
    if _EXPLICIT_REMOTE.search(cleaned):
        return "remote"
    if _HYBRID.search(f"{location}\n{cleaned}"):
        return "hybrid"
    if _REMOTE_WORD.search(location) and not _location_is_remote_label(location):
        return "hybrid"
    if re.search(r"\bremote\b", title, re.IGNORECASE) and location.strip():
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
    query_loc = prepare_geocode_query(location)
    if not query_loc:
        return None

    routing = _routing_config(config)
    cache_key = query_loc.strip().lower()
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]

    query = _normalize_geocode_query(query_loc, routing["country_hint"])
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
        # Do not cache transport failures. A later job in the same city must retry.
        logger.warning("Geocoding failed for '%s': %s", location, e)
        return None


def haversine_miles(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    """Great-circle distance in miles between two (lat, lon) points."""
    lat1, lon1 = origin
    lat2, lon2 = destination
    radius = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


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


def resolve_drive_minutes(job_location: str, home_location: str, config: dict) -> tuple[int | None, bool]:
    """Return (drive minutes, estimated).

    estimated is True when the router failed and minutes were derived from
    straight-line distance. Lookups that fail do not count as "within radius".
    """
    if not prepare_geocode_query(job_location):
        return None, False

    routing = _routing_config(config)
    provider = str(routing.get("provider") or "osrm").lower()

    if provider == "google":
        google_minutes = _get_drive_minutes_google(
            job_location,
            home_location,
            config.get("api", {}).get("google_maps_key", ""),
        )
        if google_minutes is not None:
            return google_minutes, False

    home_coords = geocode(home_location, config)
    job_coords = geocode(job_location, config)
    if not home_coords or not job_coords:
        return None, False
    routed = _get_drive_minutes_osrm(home_coords, job_coords, config)
    if routed is not None:
        return routed, False
    miles = haversine_miles(home_coords, job_coords)
    estimated = max(1, int(round(miles * STRAIGHT_LINE_MINUTES_PER_MILE)))
    logger.info(
        "Routing unavailable; estimated %s min from %.1f straight-line miles (%s -> %s)",
        estimated,
        miles,
        home_location,
        job_location,
    )
    return estimated, True


def get_drive_minutes(job_location: str, home_location: str, config: dict) -> int | None:
    """Drive time in minutes, or None when the locations cannot be measured."""
    minutes, _estimated = resolve_drive_minutes(job_location, home_location, config)
    return minutes


def _result(work_type: str, commute_minutes, action: str, note: str) -> dict:
    return {
        "work_type": work_type,
        "commute_minutes": commute_minutes,
        "action": action,
        "commute_note": note,
    }


def _location_filter_on(config: dict) -> bool:
    from jobagent.sources._common import location_filter_enabled

    return location_filter_enabled(config, "")


def classify_job(job: dict, config: dict) -> dict:
    """
    Returns a dict with:
      work_type:        'remote' | 'hybrid' | 'onsite'
      commute_minutes:  int or None
      action:           'auto_apply' | 'needs_review' | 'skip'
      commute_note:     human-readable explanation

    auto_apply here is eligibility only. It does not submit an application.
    """
    search = config.get("search") or {}
    home = (config.get("profile") or {}).get("location") or search.get("location") or ""
    auto_limit = _limit_minutes(search.get("commute_auto_apply_minutes"), 30)
    review_limit = _limit_minutes(search.get("commute_review_minutes"), 90)
    if review_limit < auto_limit:
        review_limit = auto_limit
    accept_remote = _as_bool(search.get("location_accept_remote", True), True)
    filter_location = _location_filter_on(config)

    work_type = detect_work_type(job)
    job_loc = (job.get("location") or "").strip()
    label = "Hybrid" if work_type == "hybrid" else "On-site"

    if work_type == "remote":
        if not accept_remote:
            return _result(
                work_type,
                None,
                "skip",
                "Remote role excluded — this person is not accepting remote jobs",
            )
        return _result(work_type, None, "auto_apply", "Fully remote — no commute")

    if not job_loc:
        if filter_location:
            return _result(
                work_type,
                None,
                "skip",
                "No job location — excluded by the commute radius filter",
            )
        return _result(
            work_type,
            None,
            "needs_review",
            "No job location — drive time unknown, flagged for review",
        )

    if not str(home).strip():
        return _result(
            work_type,
            None,
            "needs_review",
            f"{label} in '{job_loc}' — no home location configured, so the radius was not applied",
        )

    commute_minutes, estimated = resolve_drive_minutes(job_loc, home, config)
    estimate_note = " estimated" if estimated else ""

    if commute_minutes is None:
        return _result(
            work_type,
            None,
            "needs_review",
            f"{label} in '{job_loc}' — drive time unknown (geocoding or routing failed); not treated as within radius",
        )

    if not filter_location:
        return _result(
            work_type,
            commute_minutes,
            "needs_review",
            f"{label} — {commute_minutes} min{estimate_note} drive. Location filter is off, so it was not excluded",
        )

    if work_type == "hybrid":
        if commute_minutes <= review_limit:
            return _result(
                work_type,
                commute_minutes,
                "needs_review",
                f"Hybrid — {commute_minutes} min{estimate_note} drive. Flagged for review (hybrid always requires your call)",
            )
        return _result(
            work_type,
            commute_minutes,
            "skip",
            f"Hybrid — {commute_minutes} min{estimate_note} drive exceeds {review_limit} min review limit",
        )

    if commute_minutes <= auto_limit:
        return _result(
            work_type,
            commute_minutes,
            "auto_apply",
            f"On-site — {commute_minutes} min{estimate_note} drive, within {auto_limit} min auto-apply limit",
        )
    if commute_minutes <= review_limit:
        return _result(
            work_type,
            commute_minutes,
            "needs_review",
            f"On-site — {commute_minutes} min{estimate_note} drive. Outside auto limit ({auto_limit} min) but within review range ({review_limit} min)",
        )
    return _result(
        work_type,
        commute_minutes,
        "skip",
        f"On-site — {commute_minutes} min{estimate_note} drive exceeds {review_limit} min limit",
    )
