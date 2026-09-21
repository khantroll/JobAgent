"""Deterministic mapping from source payloads to shared catalog fields.

Crawlers fetch; these functions only normalize. They never write to the database
and never include credentials.
"""
from __future__ import annotations

import re
from typing import Any


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def catalog_job(
    *,
    title: str,
    url: str,
    company: str = "",
    location: str = "",
    description: str = "",
    source: str,
    salary_raw: str = "",
    posted_at: str = "",
) -> dict[str, str] | None:
    title = (title or "").strip()
    url = (url or "").strip()
    if not title or not url:
        return None
    return {
        "title": title,
        "company": (company or "Unknown").strip() or "Unknown",
        "location": (location or "").strip(),
        "url": url,
        "description": (description or "")[:8000],
        "source": source,
        "salary_raw": salary_raw or "",
        "posted_at": posted_at or "",
    }


def from_adzuna(job: dict[str, Any]) -> dict[str, str] | None:
    loc = ""
    location = job.get("location") or {}
    if isinstance(location, dict):
        loc = location.get("display_name") or ""
    company = "Unknown"
    company_raw = job.get("company") or {}
    if isinstance(company_raw, dict):
        company = company_raw.get("display_name") or "Unknown"
    salary = ""
    if job.get("salary_min") or job.get("salary_max"):
        salary = f"${job.get('salary_min', '?')} - ${job.get('salary_max', '?')}"
    return catalog_job(
        title=job.get("title") or "",
        company=company,
        location=loc,
        url=job.get("redirect_url") or job.get("url") or "",
        description=job.get("description") or "",
        source="adzuna",
        salary_raw=salary,
        posted_at=job.get("created") or "",
    )


def from_jsearch(job: dict[str, Any]) -> dict[str, str] | None:
    loc_parts = [
        job.get("job_city"),
        job.get("job_state"),
        job.get("job_country"),
    ]
    if job.get("job_is_remote"):
        loc_parts.insert(0, "Remote")
    return catalog_job(
        title=job.get("job_title") or "",
        company=job.get("employer_name") or "Unknown",
        location=", ".join(p for p in loc_parts if p),
        url=job.get("job_apply_link") or job.get("job_google_link") or "",
        description=job.get("job_description") or "",
        source="jsearch",
        salary_raw=job.get("job_salary") or "",
        posted_at=job.get("job_posted_at_datetime_utc") or "",
    )


def from_remotive(job: dict[str, Any]) -> dict[str, str] | None:
    return catalog_job(
        title=job.get("title") or "",
        company=job.get("company_name") or "Unknown",
        location=job.get("candidate_required_location") or "Remote",
        url=job.get("url") or "",
        description=_clean_html(job.get("description") or ""),
        source="remotive",
        salary_raw=job.get("salary") or "",
        posted_at=job.get("publication_date") or "",
    )


def from_greenhouse(job: dict[str, Any], slug: str) -> dict[str, str] | None:
    location = ""
    offices = job.get("offices") or []
    if offices and isinstance(offices[0], dict):
        location = offices[0].get("name") or ""
    return catalog_job(
        title=job.get("title") or "",
        company=(slug or "Unknown").replace("-", " ").title(),
        location=location,
        url=job.get("absolute_url") or "",
        description=_clean_html(job.get("content") or ""),
        source="greenhouse",
    )


def from_lever(job: dict[str, Any], slug: str) -> dict[str, str] | None:
    categories = job.get("categories") or {}
    location = categories.get("location") or categories.get("allLocations") or ""
    if not isinstance(location, str):
        location = str(location)
    desc = job.get("descriptionPlain") or job.get("description") or ""
    return catalog_job(
        title=job.get("text") or "",
        company=(slug or "Unknown").replace("-", " ").title(),
        location=location,
        url=job.get("hostedUrl") or job.get("applyUrl") or "",
        description=str(desc)[:8000],
        source="lever",
    )


def from_usajobs(item: dict[str, Any]) -> dict[str, str] | None:
    md = item.get("MatchedObjectDescriptor") or item
    locations = md.get("PositionLocation") or []
    loc = ""
    if locations and isinstance(locations[0], dict):
        loc = locations[0].get("LocationName") or ""
    rem = ((md.get("UserArea") or {}).get("Details") or {}).get("Remuneration") or {}
    salary = ""
    if rem:
        salary = f"${rem.get('MinimumRange', '')} - ${rem.get('MaximumRange', '')}"
    apply_url = md.get("PositionURI") or md.get("ApplyURI") or ""
    if isinstance(apply_url, list):
        apply_url = apply_url[0] if apply_url else ""
    duties = ((md.get("UserArea") or {}).get("Details") or {}).get("MajorDuties") or [""]
    duty = duties[0] if duties else ""
    return catalog_job(
        title=md.get("PositionTitle") or "",
        company=md.get("OrganizationName") or "US Federal",
        location=loc,
        url=apply_url or "",
        description=(md.get("QualificationSummary") or "") + "\n" + (duty or ""),
        source="usajobs",
        salary_raw=salary,
        posted_at=md.get("PublicationStartDate") or "",
    )


def from_themuse(job: dict[str, Any]) -> dict[str, str] | None:
    company = job.get("company") or {}
    company_name = company.get("name") if isinstance(company, dict) else str(company or "Unknown")
    refs = job.get("refs") or {}
    locs = job.get("locations") or []
    names = [loc.get("name", "") for loc in locs if isinstance(loc, dict)]
    contents = job.get("contents") or ""
    if isinstance(contents, list):
        desc = _clean_html(" ".join(str(c) for c in contents))
    else:
        desc = _clean_html(str(contents))
    return catalog_job(
        title=job.get("name") or "",
        company=company_name or "Unknown",
        location=", ".join(n for n in names if n),
        url=refs.get("landing_page") or "",
        description=desc,
        source="themuse",
        posted_at=job.get("publication_date") or "",
    )


def parse_higheredjobs_company_location(description: str) -> tuple[str, str]:
    """e.g. 'State University (Little Rock, AR)' -> company, location."""
    text = (description or "").strip()
    if text.endswith(")") and "(" in text:
        idx = text.rfind("(")
        company = text[:idx].strip()
        location = text[idx + 1 : -1].strip()
        return company or "Unknown", location
    return text or "Unknown", ""


def from_higheredjobs_rss(
    *,
    title: str,
    url: str,
    description: str = "",
    posted_at: str = "",
) -> dict[str, str] | None:
    company, location = parse_higheredjobs_company_location(description)
    return catalog_job(
        title=title,
        company=company,
        location=location,
        url=url,
        description=description,
        source="higheredjobs",
        posted_at=posted_at,
    )


def from_rss(
    *,
    title: str,
    url: str,
    description: str = "",
    location: str = "",
) -> dict[str, str] | None:
    company = "Unknown"
    if " at " in (title or ""):
        company = title.split(" at ", 1)[-1].strip() or "Unknown"
    return catalog_job(
        title=title,
        company=company,
        location=location,
        url=url,
        description=_clean_html(description),
        source="rss",
    )


def from_workday_posting(item: dict[str, Any], *, company: str, url: str) -> dict[str, str] | None:
    locations = item.get("locationsText") or ""
    if isinstance(locations, list):
        locations = ", ".join(str(x) for x in locations)
    snippet = item.get("jobDescription") or item.get("briefDescription") or ""
    return catalog_job(
        title=item.get("title") or "",
        company=company,
        location=str(locations),
        url=url,
        description=str(snippet),
        source="workday",
        posted_at=item.get("postedOn") or "",
    )
