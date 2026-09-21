from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from jobagent import AUTO_APPLY_ENABLED
from jobagent.config import load_settings
from jobagent.db import init_db
from jobagent.db import matches as match_repo
from jobagent.web.auth import require_token

router = APIRouter(tags=["stats"])


@router.get("/stats")
def dashboard_stats(
    candidate_id: int | None = Query(default=None),
    _: None = Depends(require_token),
):
    init_db()
    stats = match_repo.dashboard_stats(candidate_id)
    cfg = load_settings()
    return {
        **stats,
        "dry_run": True,
        "auto_apply_enabled": AUTO_APPLY_ENABLED,
        "min_match_score": (cfg.get("search") or {}).get("min_match_score", 65),
    }
