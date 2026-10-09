"""Candidate ATS URLs for a company name.

The repo already parses Workday careers URLs in ``workday_discover``. This module
adds the slug and tenant patterns used to look up a name on public job-board APIs.
There is no earlier company-list scanner in git history.

Greenhouse, Lever, Ashby, and SmartRecruiters publish a JSON API per board token.
They do not publish a terms-cleared index of every customer, so discovery does not
download third-party board dumps. It only requests the official per-board URLs below.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from jobagent.sources.workday_discover import jobs_api_url

_SUFFIXES = {
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "co",
    "company",
    "plc",
    "pllc",
    "pc",
    "lp",
    "llp",
    "group",
    "holdings",
    "holding",
}
_STOPWORDS = {
    "the",
    "of",
    "and",
    "for",
    "at",
    "university",
    "college",
    "state",
    "city",
    "county",
    "system",
    "office",
}

SLUG_PATTERNS = ("compact", "hyphen", "underscore", "first_token")
ATS_APIS = ("greenhouse", "lever", "ashby", "smartrecruiters")
WD_CLUSTERS = ("wd1", "wd5", "wd3")
WD_SITES = ("compact", "External", "Careers", "pascal")


def all_pattern_ids() -> list[str]:
    """Every candidate-matching pattern this module can emit."""
    ids = [f"{ats}:{slug}" for slug in SLUG_PATTERNS for ats in ATS_APIS]
    ids.extend(f"workday:{cluster}:{site}" for cluster in WD_CLUSTERS for site in WD_SITES)
    return ids


def name_tokens(name: str) -> list[str]:
    text = (name or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return [token for token in text.split() if token and token not in _SUFFIXES and token not in _STOPWORDS]


def slug_variants(name: str) -> list[tuple[str, str]]:
    """(pattern, slug) pairs. Duplicate slug strings are omitted."""
    tokens = name_tokens(name)
    if not tokens:
        return []
    ordered = [
        ("compact", "".join(tokens)),
        ("hyphen", "-".join(tokens)),
        ("underscore", "_".join(tokens)),
    ]
    if len(tokens[0]) >= 3 and len(tokens) >= 2:
        ordered.append(("first_token", tokens[0]))
    seen: set[str] = set()
    variants: list[tuple[str, str]] = []
    for pattern, slug in ordered:
        if not slug or slug in seen:
            continue
        seen.add(slug)
        variants.append((pattern, slug))
    return variants


@dataclass(frozen=True)
class BoardProbe:
    ats: str
    pattern: str
    method: str
    url: str
    slug: str = ""
    tenant: str = ""
    cluster: str = ""
    site: str = ""
    company: str = ""


def _pascal(tokens: list[str]) -> str:
    return "".join(token[:1].upper() + token[1:] for token in tokens)


def _site_for(pattern: str, tokens: list[str], compact: str) -> str:
    if pattern == "compact":
        return compact
    if pattern == "pascal":
        return _pascal(tokens) or compact
    return pattern


def _api_url(ats: str, slug: str) -> str:
    if ats == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    if ats == "lever":
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"
    if ats == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    if ats == "smartrecruiters":
        return f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    raise ValueError(ats)


def candidate_probes(name: str) -> list[BoardProbe]:
    """Public JSON endpoints to try, most likely first. Does not perform I/O."""
    tokens = name_tokens(name)
    variants = slug_variants(name)
    if not tokens or not variants:
        return []
    compact = "".join(tokens)
    probes: list[BoardProbe] = []
    for pattern, slug in variants:
        for ats in ATS_APIS:
            probes.append(
                BoardProbe(
                    ats=ats,
                    pattern=f"{ats}:{pattern}",
                    method="GET",
                    url=_api_url(ats, slug),
                    slug=slug,
                    company=name,
                )
            )
    for cluster in WD_CLUSTERS:
        for site_pattern in WD_SITES:
            site = _site_for(site_pattern, tokens, compact)
            probes.append(
                BoardProbe(
                    ats="workday",
                    pattern=f"workday:{cluster}:{site_pattern}",
                    method="POST",
                    url=jobs_api_url(compact, site, cluster),
                    tenant=compact,
                    cluster=cluster,
                    site=site,
                    company=name,
                )
            )
    return probes


def response_is_board(ats: str, payload: object) -> bool:
    """True when a public API body has the shape of a job board, including an empty list."""
    if ats == "greenhouse":
        return isinstance(payload, dict) and isinstance(payload.get("jobs"), list)
    if ats == "lever":
        return isinstance(payload, list)
    if ats == "ashby":
        return isinstance(payload, dict) and isinstance(payload.get("jobs"), list)
    if ats == "smartrecruiters":
        return isinstance(payload, dict) and isinstance(payload.get("content"), list)
    if ats == "workday":
        return isinstance(payload, dict) and (
            isinstance(payload.get("jobPostings"), list) or "total" in payload
        )
    return False
