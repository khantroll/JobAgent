"""Sync config/profile.yaml with the People (candidates) table."""

from pathlib import Path

import yaml

from simple_ui import db

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "profile.yaml"


def load_profile_config() -> dict | None:
    if not PROFILE.is_file():
        return None
    with open(PROFILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _employers_from_sources(sources: dict) -> list[dict]:
    employers = []
    gh = sources.get("greenhouse") or {}
    for slug in gh.get("companies") or []:
        slug = (slug or "").strip()
        if slug:
            employers.append(
                {"name": slug, "source_type": "greenhouse", "greenhouse_slug": slug}
            )
    lev = sources.get("lever") or {}
    for slug in lev.get("companies") or []:
        slug = (slug or "").strip()
        if slug:
            employers.append(
                {"name": slug, "source_type": "lever", "lever_slug": slug}
            )
    wd = sources.get("workday") or {}
    for item in wd.get("companies") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        employers.append(
            {
                "name": name,
                "source_type": "workday",
                "careers_url": (item.get("careers_url") or "").strip(),
                "workday_url": (item.get("workday_url") or "").strip(),
            }
        )
    return employers


def profile_person_in_db() -> bool:
    cfg = load_profile_config()
    if not cfg:
        return False
    prof = cfg.get("profile") or {}
    return find_matching_candidate_id(
        (prof.get("name") or "").strip(),
        (prof.get("email") or "").strip(),
    ) is not None


def find_matching_candidate_id(name: str, email: str) -> int | None:
    email = (email or "").strip().lower()
    name = (name or "").strip().lower()
    if email:
        row = db.row(
            "SELECT id FROM candidates WHERE lower(trim(email))=?",
            (email,),
        )
        if row:
            return row["id"]
    if name:
        row = db.row(
            "SELECT id FROM candidates WHERE lower(trim(name))=?",
            (name,),
        )
        if row:
            return row["id"]
    return None


def _keywords_text_from_search(search: dict) -> str:
    keywords = search.get("keywords") or []
    if isinstance(keywords, list):
        return "\n".join(str(k) for k in keywords if k)
    return str(keywords or "")


def payload_from_config(cfg: dict) -> tuple[dict, list[str], list[dict]]:
    prof = cfg.get("profile") or {}
    search = cfg.get("search") or {}
    titles = [t.strip() for t in (search.get("titles") or []) if (t or "").strip()]
    data = {
        "name": (prof.get("name") or "").strip(),
        "email": prof.get("email", ""),
        "phone": prof.get("phone", ""),
        "location": prof.get("location", "") or search.get("location", ""),
        "linkedin": prof.get("linkedin", ""),
        "github": prof.get("github", ""),
        "resume_text": cfg.get("resume_text", "") or "",
        "resume_file": "",
        "min_match_score": int(search.get("min_match_score") or 65),
        "salary_min": int(search.get("salary_min") or 0),
        "salary_max": int(search.get("salary_max") or 0),
        "keywords_text": _keywords_text_from_search(search),
        "hej_category_ids": _hej_ids_from_config(cfg),
    }
    employers = _employers_from_sources(cfg.get("sources") or {})
    return data, titles, employers


def _hej_ids_from_config(cfg: dict) -> str:
    ids = cfg.get("sources", {}).get("higheredjobs", {}).get("category_ids") or []
    if not ids:
        return ""
    return "\n".join(str(int(x)) for x in ids)


def sync_from_profile_yaml(
    *,
    candidate_id: int | None = None,
    set_active: bool = True,
) -> tuple[int | None, str]:
    """
    Create or update a People row from profile.yaml.
    Returns (candidate_id, action) where action is created|updated|skipped.
    """
    db.init()
    cfg = load_profile_config()
    if not cfg:
        return None, "skipped"
    data, titles, employers = payload_from_config(cfg)
    if not data["name"]:
        return None, "skipped"

    match_id = candidate_id or find_matching_candidate_id(
        data["name"], data.get("email", "")
    )
    if match_id:
        cid = db.save_candidate(data, titles, employers, match_id)
        action = "updated"
    else:
        cid = db.save_candidate(data, titles, employers)
        action = "created"
    if set_active:
        db.set_active(cid)
    return cid, action


def import_from_profile_yaml() -> int | None:
    cid, action = sync_from_profile_yaml()
    return cid if action != "skipped" else None


def ensure_profile_imported() -> int | None:
    """On startup: add profile.yaml person if they are not already in People."""
    if profile_person_in_db():
        return None
    cid, action = sync_from_profile_yaml()
    return cid if action != "skipped" else None


def search_defaults_for_candidate(cand: dict | None) -> dict:
    """Per-person search prefs from the database (not global profile.yaml)."""
    if not cand:
        return {
            "min_score": 65,
            "salary_min": 0,
            "salary_max": 0,
            "keywords": "",
        }
    return {
        "min_score": int(cand.get("min_match_score") or 65),
        "salary_min": int(cand.get("salary_min") or 0),
        "salary_max": int(cand.get("salary_max") or 0),
        "keywords": cand.get("keywords_text") or "",
    }
