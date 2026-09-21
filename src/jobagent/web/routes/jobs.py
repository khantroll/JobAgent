from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from jobagent.db import init_db
from jobagent.db import jobs as job_repo
from jobagent.web.auth import require_token

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    source: str | None = None,
    q: str | None = None,
    sort_by: str = "found_at",
    sort_dir: str = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: None = Depends(require_token),
):
    init_db()
    jobs, total = job_repo.list_jobs(
        source=source,
        q=q,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    return {"jobs": jobs, "total": total, "limit": limit, "offset": offset}


@router.get("/{job_id}")
def get_job(job_id: str, _: None = Depends(require_token)):
    init_db()
    job = job_repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
