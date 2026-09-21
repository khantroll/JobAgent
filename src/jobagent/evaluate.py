"""Read-only evaluation of ranking quality. Does not modify the database."""
from __future__ import annotations

from collections import Counter
from typing import Any

from jobagent.db import candidates as cand_repo
from jobagent.db import init_db
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo


def _buckets(scores: list[int]) -> dict[str, int]:
    buckets = {"90-100": 0, "70-89": 0, "50-69": 0, "1-49": 0, "0": 0, "unscored": 0, "error(-1)": 0}
    return buckets


def _score_distribution(rows: list[dict]) -> dict[str, int]:
    buckets = _buckets([])
    for row in rows:
        score = row.get("score")
        if score is None:
            buckets["unscored"] += 1
        elif int(score) < 0:
            buckets["error(-1)"] += 1
        elif int(score) == 0:
            buckets["0"] += 1
        elif int(score) >= 90:
            buckets["90-100"] += 1
        elif int(score) >= 70:
            buckets["70-89"] += 1
        elif int(score) >= 50:
            buckets["50-69"] += 1
        else:
            buckets["1-49"] += 1
    return buckets


def evaluate_database(*, top_n: int = 10) -> dict[str, Any]:
    init_db()
    catalog_jobs = job_repo.count_jobs()
    people = cand_repo.list_candidates()
    report: dict[str, Any] = {
        "catalog_jobs": catalog_jobs,
        "candidates": [],
        "search_enabled_ids": [
            p["id"] for p in people if cand_repo.search_enabled_value(p)
        ],
    }
    for person in people:
        cid = person["id"]
        full = cand_repo.get_candidate(cid) or person
        rows, total = match_repo.list_matches(cid, sort_by="score", sort_dir="desc", limit=10_000)
        match_repo.annotate_match_duplicates(rows)
        duplicate_count = sum(1 for r in rows if r.get("is_duplicate"))
        by_source = Counter((r.get("source") or "unknown") for r in rows)
        by_work = Counter((r.get("work_type") or "unknown") for r in rows)
        by_commute = Counter((r.get("commute_result") or "none") for r in rows)
        by_status = Counter((r.get("status") or "unknown") for r in rows)
        missing_salary = sum(1 for r in rows if not (r.get("salary_raw") or "").strip())
        missing_location = sum(1 for r in rows if not (r.get("location") or "").strip())
        scored = [r for r in rows if r.get("score") is not None and int(r["score"]) >= 0]
        top = [
            {
                "job_id": r["job_id"],
                "title": r.get("title"),
                "company": r.get("company"),
                "location": r.get("location"),
                "source": r.get("source"),
                "score": r.get("score"),
                "reason": r.get("score_reason"),
                "status": r.get("status"),
                "work_type": r.get("work_type"),
                "salary_raw": r.get("salary_raw"),
                "url": r.get("url"),
                "rank_provider": r.get("rank_provider"),
            }
            for r in scored[:top_n]
        ]
        report["candidates"].append(
            {
                "id": cid,
                "name": full.get("name"),
                "search_enabled": cand_repo.search_enabled_value(full),
                "matches": total,
                "score_distribution": _score_distribution(rows),
                "by_status": dict(by_status),
                "by_source": dict(by_source),
                "by_work_type": dict(by_work),
                "by_commute_result": dict(by_commute),
                "missing_salary": missing_salary,
                "missing_location": missing_location,
                "obvious_duplicates": duplicate_count,
                "top_ranked": top,
            }
        )
    return report


def format_evaluation(report: dict[str, Any]) -> str:
    lines = [
        f"JobAgent evaluation ({report['catalog_jobs']} catalog jobs)",
        f"  search-enabled candidate ids: {report.get('search_enabled_ids')}",
    ]
    for cand in report.get("candidates") or []:
        lines.append("")
        lines.append(
            f"{cand['name']} (id={cand['id']}, search={'on' if cand['search_enabled'] else 'off'})"
        )
        lines.append(f"  matches: {cand['matches']}")
        dist = cand.get("score_distribution") or {}
        lines.append("  scores: " + ", ".join(f"{k}={v}" for k, v in dist.items()))
        src = cand.get("by_source") or {}
        lines.append("  sources: " + (", ".join(f"{k}={v}" for k, v in src.items()) or "(none)"))
        lines.append("  work type: " + ", ".join(f"{k}={v}" for k, v in (cand.get('by_work_type') or {}).items()))
        lines.append("  commute: " + ", ".join(f"{k}={v}" for k, v in (cand.get('by_commute_result') or {}).items()))
        lines.append(
            f"  missing salary={cand.get('missing_salary')} location={cand.get('missing_location')} "
            f"duplicates={cand.get('obvious_duplicates')}"
        )
        if cand.get("top_ranked"):
            lines.append("  top ranked:")
            for row in cand["top_ranked"][:8]:
                lines.append(
                    f"    [{row.get('score')}] {row.get('title')} @ {row.get('company')} "
                    f"({row.get('source')}) {row.get('url')}"
                )
    return "\n".join(lines)
