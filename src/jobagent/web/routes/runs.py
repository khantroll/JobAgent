from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from jobagent.db import init_db
from jobagent.db import runs as run_repo
from jobagent.web import worker
from jobagent.web.auth import require_token

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
def list_api_runs(_: None = Depends(require_token)):
    return {"runs": worker.list_runs(30)}


@router.get("/latest")
def latest_api_run(_: None = Depends(require_token)):
    run = worker.latest_run()
    return {"run": run, "running": worker.is_running()}


@router.get("/history")
def run_history(_: None = Depends(require_token)):
    init_db()
    return {"runs": run_repo.list_run_history(50)}


@router.post("")
def trigger_run(_: None = Depends(require_token)):
    try:
        record = worker.start_run()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"run": record}


@router.get("/{run_id}")
def get_api_run(run_id: str, _: None = Depends(require_token)):
    run = worker.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run
