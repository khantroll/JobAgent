"""Load global settings from YAML and overlay secrets from the environment."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from jobagent.paths import project_root, settings_path

_ENV_API_KEYS = {
    "anthropic_key": "ANTHROPIC_API_KEY",
    "mistral_key": "MISTRAL_API_KEY",
    "adzuna_app_id": "ADZUNA_APP_ID",
    "adzuna_app_key": "ADZUNA_APP_KEY",
    "usajobs_api_key": "USAJOBS_API_KEY",
    "usajobs_user_agent": "USAJOBS_USER_AGENT",
    "rapidapi_key": "RAPIDAPI_KEY",
    "themuse_api_key": "THEMUSE_API_KEY",
    "google_maps_key": "GOOGLE_MAPS_KEY",
}


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """Return global config. Candidate identity is never read from this file."""
    load_dotenv(project_root() / ".env")
    cfg_path = path or settings_path()
    data: dict[str, Any] = {}
    if cfg_path.is_file():
        with open(cfg_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    cfg = copy.deepcopy(data)
    api = dict(cfg.get("api") or {})
    for dest, env_name in _ENV_API_KEYS.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            api[dest] = value
    cfg["api"] = api
    scheduler = dict(cfg.get("scheduler") or {})
    scheduler["dry_run"] = True
    cfg["scheduler"] = scheduler
    return cfg


def candidate_runtime_config(candidate: dict, base: dict | None = None) -> dict[str, Any]:
    """Merge a candidate DB record into a crawl/rank/commute config.

    Global scheduler, sources, routing, and API keys come from settings.
    Profile identity and search prefs come only from the candidate row.
    """
    cfg = copy.deepcopy(base or load_settings())
    cfg["profile"] = {
        "name": candidate.get("name") or "",
        "email": candidate.get("email") or "",
        "phone": candidate.get("phone") or "",
        "location": candidate.get("location") or "",
        "linkedin": candidate.get("linkedin") or "",
        "github": candidate.get("github") or "",
    }
    if candidate.get("resume_text"):
        cfg["resume_text"] = candidate["resume_text"]
    else:
        cfg["resume_text"] = cfg.get("resume_text") or ""

    search = dict(cfg.get("search") or {})
    titles = [t["title"] if isinstance(t, dict) else str(t) for t in (candidate.get("titles") or [])]
    titles = [t.strip() for t in titles if str(t).strip()]
    if titles:
        search["titles"] = titles
    if candidate.get("min_match_score") is not None:
        search["min_match_score"] = int(candidate["min_match_score"])
    if candidate.get("salary_min") is not None:
        search["salary_min"] = int(candidate["salary_min"])
    if candidate.get("salary_max") is not None:
        search["salary_max"] = int(candidate["salary_max"])
    keywords_text = candidate.get("keywords_text") or ""
    if str(keywords_text).strip():
        search["keywords"] = [k.strip() for k in str(keywords_text).splitlines() if k.strip()]
    if candidate.get("location"):
        search["location"] = candidate["location"]
    if candidate.get("commute_auto_apply_minutes") is not None:
        search["commute_auto_apply_minutes"] = int(candidate["commute_auto_apply_minutes"])
    if candidate.get("commute_review_minutes") is not None:
        search["commute_review_minutes"] = int(candidate["commute_review_minutes"])
    if candidate.get("location_accept_remote") is not None:
        search["location_accept_remote"] = bool(int(candidate["location_accept_remote"]))
    cfg["search"] = search

    hej = candidate.get("hej_category_ids") or ""
    if str(hej).strip():
        cfg["_hej_category_ids"] = str(hej)
    return cfg


def crawl_config_for_candidates(candidates: list[dict], base: dict | None = None) -> dict[str, Any]:
    """Build a single crawl config whose titles/HEJ IDs are the union of all people."""
    cfg = copy.deepcopy(base or load_settings())
    titles: list[str] = []
    seen_titles: set[str] = set()
    hej_ids: list[str] = []
    locations: list[str] = []
    greenhouse: list[str] = []
    seen_gh: set[str] = set()
    lever: list[str] = []
    seen_lv: set[str] = set()
    workday: list[dict] = []
    seen_wd: set[str] = set()
    for cand in candidates:
        for raw in cand.get("titles") or []:
            title = (raw["title"] if isinstance(raw, dict) else str(raw)).strip()
            key = title.lower()
            if title and key not in seen_titles:
                seen_titles.add(key)
                titles.append(title)
        hej = str(cand.get("hej_category_ids") or "").strip()
        if hej:
            hej_ids.append(hej)
        loc = str(cand.get("location") or "").strip()
        if loc:
            locations.append(loc)
        for emp in cand.get("employers") or []:
            gh = str(emp.get("greenhouse_slug") or "").strip()
            if gh and gh.lower() not in seen_gh:
                seen_gh.add(gh.lower())
                greenhouse.append(gh)
            lv = str(emp.get("lever_slug") or "").strip()
            if lv and lv.lower() not in seen_lv:
                seen_lv.add(lv.lower())
                lever.append(lv)
            wd_url = str(emp.get("workday_url") or "").strip()
            careers = str(emp.get("careers_url") or "").strip()
            source_type = str(emp.get("source_type") or "").strip().lower()
            if wd_url or (source_type == "workday" and careers):
                key = (wd_url or careers).lower()
                if key and key not in seen_wd:
                    seen_wd.add(key)
                    workday.append(
                        {
                            "name": emp.get("name") or "Unknown",
                            "careers_url": careers,
                            "workday_url": wd_url,
                        }
                    )
    search = dict(cfg.get("search") or {})
    if titles:
        search["titles"] = titles
    if locations and not search.get("location"):
        search["location"] = locations[0]
    cfg["search"] = search
    if hej_ids:
        cfg["_hej_category_ids"] = "\n".join(hej_ids)
    sources = cfg.get("sources") or {}
    adzuna = dict(sources.get("adzuna") or {})
    if locations and not adzuna.get("where"):
        adzuna["where"] = locations[0]
        sources["adzuna"] = adzuna
        cfg["sources"] = sources
    jsearch = dict(sources.get("jsearch") or {})
    if locations and not jsearch.get("location"):
        jsearch["location"] = locations[0]
        sources["jsearch"] = jsearch
        cfg["sources"] = sources
    usajobs = dict(sources.get("usajobs") or {})
    if locations and not usajobs.get("location"):
        usajobs["location"] = locations[0]
        sources["usajobs"] = usajobs
        cfg["sources"] = sources
    if greenhouse:
        gh_cfg = dict(sources.get("greenhouse") or {})
        existing = [str(x) for x in (gh_cfg.get("companies") or [])]
        merged = list(dict.fromkeys(existing + greenhouse))
        gh_cfg["companies"] = merged
        sources["greenhouse"] = gh_cfg
        cfg["sources"] = sources
    if lever:
        lv_cfg = dict(sources.get("lever") or {})
        existing = [str(x) for x in (lv_cfg.get("companies") or [])]
        merged = list(dict.fromkeys(existing + lever))
        lv_cfg["companies"] = merged
        sources["lever"] = lv_cfg
        cfg["sources"] = sources
    if workday:
        wd_cfg = dict(sources.get("workday") or {})
        existing = list(wd_cfg.get("companies") or [])
        wd_cfg["companies"] = list(existing) + workday
        sources["workday"] = wd_cfg
        cfg["sources"] = sources
    return cfg
