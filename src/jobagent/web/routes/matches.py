from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from jobagent.db import candidates as cand_repo
from jobagent.db import init_db
from jobagent.db import matches as match_repo
from jobagent.web.auth import require_token

router = APIRouter(prefix="/candidates", tags=["matches"])


class MatchStatusUpdate(BaseModel):
    status: str = Field(..., description="new | reviewed | ignored | rejected | applied")
    status_reason: str = ""


def _candidate_or_404(candidate_id: int) -> dict:
    cand = cand_repo.get_candidate(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return cand


@router.get("/{candidate_id}/matches")
def list_matches(
    candidate_id: int,
    status: str | None = None,
    q: str | None = None,
    min_score: int | None = None,
    sort_by: str = "status_updated_at",
    sort_dir: str = "desc",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(require_token),
):
    init_db()
    _candidate_or_404(candidate_id)
    rows, total = match_repo.list_matches(
        candidate_id,
        status=status,
        q=q,
        min_score=min_score,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    return {"matches": rows, "total": total, "limit": limit, "offset": offset}


@router.patch("/{candidate_id}/matches/{job_id}")
def patch_match(
    candidate_id: int,
    job_id: str,
    body: MatchStatusUpdate,
    _: None = Depends(require_token),
):
    init_db()
    _candidate_or_404(candidate_id)
    try:
        ok = match_repo.update_match_status(
            candidate_id, job_id, body.status, status_reason=body.status_reason
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="Match not found")
    return match_repo.get_match(candidate_id, job_id)
