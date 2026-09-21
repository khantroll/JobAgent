"""Reconstruct candidate records from frozen legacy evidence.

Never writes to www/. Never copies API keys, passwords, or notification secrets.
Does not merge DB stub "Jeff" with profile "Jeffrey Bowers".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from jobagent.db import candidates as cand_repo
from jobagent.db import init_db
from jobagent.paths import data_dir, project_root

_DROP_SECTIONS = frozenset({"api", "notifications"})
_SECRET_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "_key",
    "app_password",
)

JEFFREY_PROFILE = Path("www/config/profile.yaml")
TAMI_PROFILE = Path("www/config/backups/profile-20260610-113809.yaml")
JEFFREY_ALT_TITLES = Path("www/config/backups/profile-20260605-102749.yaml")
JEFFREY_ZERO_SALARY = Path("www/config/backups/profile-20260604-131209.yaml")
TAMI_CONTAMINATED_KEYWORDS = Path("www/config/backups/profile-20260604-123634.yaml")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_secret_key(name: str) -> bool:
    lower = name.lower()
    return any(frag in lower for frag in _SECRET_KEY_FRAGMENTS)


def load_legacy_yaml(path: Path) -> dict[str, Any]:
    """Load a legacy profile, stripping secret sections and keys."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if key in _DROP_SECTIONS or _is_secret_key(str(key)):
            continue
        if isinstance(value, dict):
            cleaned[key] = {
                k: v for k, v in value.items() if not _is_secret_key(str(k))
            }
        else:
            cleaned[key] = value
    return cleaned


def _profile_identity(data: dict) -> dict[str, str]:
    profile = data.get("profile") or {}
    return {
        "name": str(profile.get("name") or "").strip(),
        "email": str(profile.get("email") or "").strip(),
        "phone": str(profile.get("phone") or "").strip(),
        "location": str(profile.get("location") or data.get("search", {}).get("location") or "").strip(),
        "linkedin": str(profile.get("linkedin") or "").strip(),
        "github": str(profile.get("github") or "").strip(),
    }


def _keywords_text(search: dict) -> str:
    raw = search.get("keywords") or []
    if isinstance(raw, str):
        return raw
    return "\n".join(str(k).strip() for k in raw if str(k).strip())


def _hej_text(data: dict) -> str:
    ids = ((data.get("sources") or {}).get("higheredjobs") or {}).get("category_ids") or []
    return "\n".join(str(int(x)) for x in ids)


def _employers_from_sources(data: dict) -> list[dict]:
    sources = data.get("sources") or {}
    out: list[dict] = []
    for slug in sources.get("greenhouse", {}).get("companies") or []:
        slug = str(slug).strip()
        if slug:
            out.append(
                {
                    "name": slug.replace("-", " ").title(),
                    "source_type": "greenhouse",
                    "greenhouse_slug": slug,
                    "careers_url": "",
                    "workday_url": "",
                    "lever_slug": "",
                }
            )
    for slug in sources.get("lever", {}).get("companies") or []:
        slug = str(slug).strip()
        if slug:
            out.append(
                {
                    "name": slug.replace("-", " ").title(),
                    "source_type": "lever",
                    "lever_slug": slug,
                    "greenhouse_slug": "",
                    "careers_url": "",
                    "workday_url": "",
                }
            )
    for entry in sources.get("workday", {}).get("companies") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("tenant") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "source_type": "workday",
                "careers_url": str(entry.get("careers_url") or "").strip(),
                "workday_url": str(entry.get("workday_url") or "").strip(),
                "greenhouse_slug": "",
                "lever_slug": "",
            }
        )
    return out


def _candidate_payload(data: dict, *, reconstruction_source: str) -> tuple[dict, list[str], list[dict]]:
    ident = _profile_identity(data)
    search = data.get("search") or {}
    titles = [str(t).strip() for t in (search.get("titles") or []) if str(t).strip()]
    payload = {
        **ident,
        "resume_text": str(data.get("resume_text") or ""),
        "resume_file": "",
        "min_match_score": int(search.get("min_match_score") or 65),
        "salary_min": int(search.get("salary_min") or 0),
        "salary_max": int(search.get("salary_max") or 0),
        "keywords_text": _keywords_text(search),
        "hej_category_ids": _hej_text(data),
        "commute_auto_apply_minutes": int(search.get("commute_auto_apply_minutes") or 30),
        "commute_review_minutes": int(search.get("commute_review_minutes") or 90),
        "search_enabled": 1,
        "reconstruction_source": reconstruction_source,
        "location_accept_remote": 1 if search.get("location_accept_remote", True) else 0,
    }
    return payload, titles, _employers_from_sources(data)


def _field_report(payload: dict, titles: list[str], employers: list[dict], source: str) -> dict:
    return {
        "name": {"value": payload["name"], "source": source},
        "email": {"value": payload["email"], "source": source},
        "phone": {"value": payload["phone"], "source": source},
        "location": {"value": payload["location"], "source": source},
        "linkedin": {"value": payload["linkedin"], "source": source},
        "github": {"value": payload["github"] or None, "source": source},
        "titles": {"value": titles, "source": source},
        "keywords": {"value": payload["keywords_text"].splitlines(), "source": source},
        "salary_min": {"value": payload["salary_min"], "source": source},
        "salary_max": {"value": payload["salary_max"], "source": source},
        "min_match_score": {"value": payload["min_match_score"], "source": source},
        "hej_category_ids": {
            "value": [x for x in payload["hej_category_ids"].splitlines() if x],
            "source": source,
        },
        "commute_auto_apply_minutes": {
            "value": payload["commute_auto_apply_minutes"],
            "source": "profile search / schema default 30",
        },
        "commute_review_minutes": {
            "value": payload["commute_review_minutes"],
            "source": "profile search / schema default 90",
        },
        "resume_text_chars": {"value": len(payload["resume_text"] or ""), "source": source},
        "employers": {"value": len(employers), "source": source},
        "search_enabled": {"value": bool(payload["search_enabled"]), "source": "alpha.2 policy"},
    }


def reconstruct_candidates(*, report_dir: Path | None = None) -> dict[str, Any]:
    """Apply explicit reconstruction choices into the 2.0 candidate table."""
    init_db()
    root = project_root()
    jeffrey_path = root / JEFFREY_PROFILE
    tami_path = root / TAMI_PROFILE
    report: dict[str, Any] = {
        "started_at": _utcnow(),
        "candidates": [],
        "conflicts": [],
        "unresolved": [],
        "defaults_applied": [
            "commute_auto_apply_minutes=30 when absent",
            "commute_review_minutes=90 when absent",
            "min_match_score=65 when absent",
            "search_enabled=false for DB stubs Test User and Jeff",
            "search_enabled=true for reconstructed Jeffrey Bowers and Tami Wood",
            "Jeffrey titles/salary/HEJ taken from current www/config/profile.yaml (not alternate backups)",
            "Tami keywords taken from latest Tami backup (empty), not early contaminated IT keywords",
        ],
    }

    # --- Stubs from recovered DB (already imported) ---
    test_user = cand_repo.get_candidate(1)
    jeff_stub = cand_repo.get_candidate(2)
    if test_user and test_user.get("name") == "Test User":
        cand_repo.set_search_enabled(
            1, False, reconstruction_source="www/data/jobs.db candidates.id=1 (stub)"
        )
        report["candidates"].append(
            {
                "id": 1,
                "name": "Test User",
                "action": "preserved_stub",
                "search_enabled": False,
                "fields": {
                    "name": {"value": "Test User", "source": "www/data/jobs.db"},
                    "email": {"value": test_user.get("email"), "source": "www/data/jobs.db"},
                    "titles": {
                        "value": [t["title"] for t in test_user.get("titles") or []],
                        "source": "www/data/jobs.db candidate_titles",
                    },
                    "keywords": {"value": test_user.get("keywords_text"), "source": "www/data/jobs.db"},
                },
                "conflicts": [],
                "unresolved": ["Not a production identity. Disabled for crawl/rank union."],
                "defaults_applied": ["search_enabled=false"],
            }
        )
    else:
        report["unresolved"].append("Legacy candidate id=1 (Test User) not present; run migrate-legacy first.")

    if jeff_stub and (jeff_stub.get("name") or "").strip() == "Jeff":
        cand_repo.set_search_enabled(
            2, False, reconstruction_source="www/data/jobs.db candidates.id=2 (stub; not Jeffrey Bowers)"
        )
        report["candidates"].append(
            {
                "id": 2,
                "name": "Jeff",
                "action": "preserved_stub_not_merged",
                "search_enabled": False,
                "fields": {
                    "name": {"value": "Jeff", "source": "www/data/jobs.db"},
                    "email": {"value": jeff_stub.get("email"), "source": "www/data/jobs.db"},
                    "titles": {
                        "value": [t["title"] for t in jeff_stub.get("titles") or []],
                        "source": "www/data/jobs.db",
                    },
                },
                "conflicts": [
                    "DB Jeff (a@b.com, title IT) is not Jeffrey Bowers (khantroll@gmail.com). Not merged."
                ],
                "unresolved": ["Abandoned placeholder. Disabled for crawl/rank union."],
                "defaults_applied": ["search_enabled=false"],
            }
        )
        report["conflicts"].append(
            "Did not upsert Jeffrey Bowers onto candidates.id=2 Jeff stub (email/title/resume disagree)."
        )
    else:
        report["unresolved"].append("Legacy candidate id=2 (Jeff stub) not present; run migrate-legacy first.")

    jeffrey_conflicts = [
        {
            "field": "titles",
            "chosen": "current profile.yaml 10-title set",
            "rejected": str(JEFFREY_ALT_TITLES),
            "reason": "Engineer-heavy 16-title backup is an alternate, not silently applied.",
        },
        {
            "field": "salary_min/max",
            "chosen": "80000–150000 from current profile.yaml",
            "rejected": str(JEFFREY_ZERO_SALARY) + " and similar 0–0 backups",
            "reason": "Zero salary in some backups looks like a UI wipe, not a real preference.",
        },
        {
            "field": "hej_category_ids",
            "chosen": "144,161,162,173 from current profile.yaml",
            "rejected": "171,68,278,144,46,173,162,172 in profile-20260605-102749.yaml",
            "reason": "Current file is the recovered live profile.",
        },
        {
            "field": "resume",
            "chosen": "resume_text from current profile.yaml",
            "rejected": "www/uploads/*.docx not imported (binary; optional operator attach)",
            "reason": "Text is what ranking uses; docx path left unresolved.",
        },
    ]

    if jeffrey_path.is_file():
        data = load_legacy_yaml(jeffrey_path)
        payload, titles, employers = _candidate_payload(
            data, reconstruction_source=str(JEFFREY_PROFILE)
        )
        existing = cand_repo.find_candidate_by_email(payload["email"])
        cid = cand_repo.save_candidate(
            payload,
            titles,
            employers,
            candidate_id=existing["id"] if existing else None,
        )
        report["candidates"].append(
            {
                "id": cid,
                "name": payload["name"],
                "action": "updated" if existing else "created",
                "search_enabled": True,
                "fields": _field_report(payload, titles, employers, str(JEFFREY_PROFILE)),
                "conflicts": jeffrey_conflicts,
                "unresolved": [
                    "docx resume in www/uploads not copied into 2.0 uploads/",
                    "per-candidate source enable flags stay global in settings.yaml",
                    "exclude_keywords remain global in settings.yaml (were profile-level)",
                ],
                "defaults_applied": ["search_enabled=true", "location_accept_remote=true"],
            }
        )
        report["conflicts"].extend(
            f"Jeffrey {c['field']}: chose {c['chosen']}; did not apply {c['rejected']}"
            for c in jeffrey_conflicts
        )
        report["unresolved"].append("Jeffrey Bowers docx resume not imported.")
    else:
        report["unresolved"].append(f"Missing {JEFFREY_PROFILE}")

    tami_conflicts = [
        {
            "field": "keywords",
            "chosen": "empty list from latest Tami backup",
            "rejected": str(TAMI_CONTAMINATED_KEYWORDS),
            "reason": "Early Tami backups reused Jeffrey's IT keywords.",
        },
        {
            "field": "employers",
            "chosen": "none (Tami backups have no ATS company list)",
            "rejected": "Jeffrey's greenhouse/lever/workday boards",
            "reason": "Do not attach Jeffrey's employer list to Tami.",
        },
        {
            "field": "phone",
            "chosen": "value from Tami backup (same number also on Jeffrey profiles)",
            "rejected": "using phone as a unique person key",
            "reason": "Shared phone is not a discriminator.",
        },
    ]

    if tami_path.is_file():
        data = load_legacy_yaml(tami_path)
        payload, titles, employers = _candidate_payload(
            data, reconstruction_source=str(TAMI_PROFILE)
        )
        existing = cand_repo.find_candidate_by_email(payload["email"])
        cid = cand_repo.save_candidate(
            payload,
            titles,
            employers,
            candidate_id=existing["id"] if existing else None,
        )
        report["candidates"].append(
            {
                "id": cid,
                "name": payload["name"],
                "action": "updated" if existing else "created",
                "search_enabled": True,
                "fields": _field_report(payload, titles, employers, str(TAMI_PROFILE)),
                "conflicts": tami_conflicts,
                "unresolved": [
                    "Tami resume.docx in www/uploads not imported",
                    "Tami backup HEJ 66/46/278/279 applied; not mixed with Jeffrey IDs",
                ],
                "defaults_applied": ["search_enabled=true", "keywords empty by choice"],
            }
        )
        report["conflicts"].extend(
            f"Tami {c['field']}: chose {c['chosen']}; did not apply {c['rejected']}"
            for c in tami_conflicts
        )
    else:
        report["unresolved"].append(f"Missing {TAMI_PROFILE}")

    report["finished_at"] = _utcnow()
    dest_dir = report_dir or (data_dir() / "reconstruction_reports")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = dest_dir / f"candidates-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["report_path"] = str(path)
    return report
