"""Ranking agent — scores each job against one candidate. Writes match rows only."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from jobagent.llm import MissingLLMKey, complete_json, llm_settings, provider_has_key

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a job fit analyst. Given a candidate profile and a job listing,
score how well the job matches the candidate on a scale of 0-100.

Scoring guide:
  90-100: Near-perfect fit.
  70-89:  Good fit, minor gaps.
  50-69:  Moderate fit, notable gaps.
  0-49:   Poor fit.

Respond ONLY with valid JSON:
{"score": <integer 0-100>, "reason": "<one sentence>"}"""


@dataclass
class RankResult:
    score: int
    reason: str
    provider: str
    model: str


def heuristic_score(job: dict, config: dict) -> RankResult:
    """Deterministic fallback when no LLM key is configured or the provider fails."""
    search = config.get("search") or {}
    titles = [str(t).lower() for t in (search.get("titles") or [])]
    keywords = [str(k).lower() for k in (search.get("keywords") or [])]
    hay = " ".join(
        [
            str(job.get("title") or ""),
            str(job.get("company") or ""),
            str(job.get("description") or "")[:2000],
        ]
    ).lower()
    title_hits = 0
    for title in titles:
        words = [w for w in title.replace("/", " ").split() if len(w) > 2]
        if title and (title in hay or (len(words) >= 2 and all(w in hay for w in words))):
            title_hits += 1
    kw_hits = sum(1 for k in keywords if k and k in hay)
    score = min(100, 18 + title_hits * 16 + kw_hits * 7)
    reason = (
        f"Heuristic fallback ({title_hits} title hit(s), {kw_hits} keyword hit(s)); "
        "no live LLM call."
    )
    return RankResult(score=score, reason=reason, provider="fallback", model="heuristic")


def score_job(job: dict, config: dict) -> RankResult:
    provider, model = llm_settings(config)
    if provider != "mock" and not provider_has_key(config, provider):
        return heuristic_score(job, config)

    profile = config.get("profile") or {}
    search = config.get("search") or {}
    titles = search.get("titles") or []
    keywords = search.get("keywords") or []
    salary_min = search.get("salary_min", 80000)
    salary_max = search.get("salary_max", 150000)
    resume_text = config.get("resume_text") or ""
    user_message = (
        "CANDIDATE PROFILE:\n"
        f"Name: {profile.get('name', '')}\n"
        f"Location: {profile.get('location', '')}\n"
        f"Target titles: {', '.join(titles)}\n"
        f"Key skills: {', '.join(keywords)}\n"
        f"Salary range: ${salary_min:,} - ${salary_max:,}\n"
        f"Resume:\n{resume_text[:1500]}\n\n"
        "JOB LISTING:\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Salary: {job.get('salary_raw', 'not listed')}\n"
        f"Description:\n{str(job.get('description', ''))[:2000]}"
    )

    try:
        data = complete_json(
            config,
            system=SYSTEM_PROMPT,
            user=user_message,
            max_tokens=256,
        )
        return RankResult(
            score=int(data["score"]),
            reason=str(data.get("reason") or ""),
            provider=provider,
            model=model,
        )
    except MissingLLMKey:
        return heuristic_score(job, config)
    except Exception as exc:
        logger.warning("LLM scoring failed (%s); using heuristic fallback", exc)
        fallback = heuristic_score(job, config)
        fallback.reason = f"{fallback.reason} (LLM error: {exc})"
        return fallback


def run_ranking(jobs: list, config: dict, *, candidate_id: int) -> None:
    """Score each job for one candidate and store the result on that match only."""
    from jobagent.db import matches as match_repo

    search = config.get("search") or {}
    exclude = [e.lower() for e in search.get("exclude_keywords", [])]
    exclude_companies = [c.lower() for c in search.get("exclude_companies", [])]

    for row in jobs:
        job = dict(row)
        jid = match_repo.catalog_job_id(job)
        title_lower = job.get("title", "").lower()
        company_lower = job.get("company", "").lower()
        desc_lower = str(job.get("description", "")).lower()

        if any(e in title_lower or e in desc_lower for e in exclude):
            match_repo.update_match_score(
                candidate_id, jid, 0, "Excluded keyword match",
                rank_provider="filter", rank_model="exclude_keywords",
            )
            continue
        if any(e in company_lower for e in exclude_companies):
            match_repo.update_match_score(
                candidate_id, jid, 0, "Excluded company",
                rank_provider="filter", rank_model="exclude_companies",
            )
            continue

        try:
            result = score_job(job, config)
            if isinstance(result, tuple):
                result = RankResult(
                    score=int(result[0]),
                    reason=str(result[1] if len(result) > 1 else ""),
                    provider="test",
                    model="",
                )
            match_repo.update_match_score(
                candidate_id,
                jid,
                result.score,
                result.reason,
                rank_provider=result.provider,
                rank_model=result.model,
            )
            logger.info("[%s] %s @ %s: %s", result.score, job.get("title"), job.get("company"), result.reason)
        except Exception as e:
            logger.error("Scoring failed for %s: %s", jid, e)
            match_repo.update_match_score(
                candidate_id, jid, -1, f"Error: {e}",
                rank_provider="error", rank_model="",
            )
