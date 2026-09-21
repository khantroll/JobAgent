"""Background dry-run cycles (single worker, no concurrent cycles)."""
from __future__ import annotations

import logging
import threading
import traceback
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("jobagent.worker")

_lock = threading.Lock()
_runs: dict[str, dict] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_run(run_id: str) -> dict | None:
    return _runs.get(run_id)


def list_runs(limit: int = 20) -> list[dict]:
    items = sorted(_runs.values(), key=lambda r: r["started_at"], reverse=True)
    return items[:limit]


def latest_run() -> dict | None:
    runs = list_runs(1)
    return runs[0] if runs else None


def is_running() -> bool:
    return any(r["status"] == "running" for r in _runs.values())


def start_run(candidate_id: int | None = None) -> dict:
    with _lock:
        if is_running():
            raise RuntimeError("A cycle is already running")

        run_id = str(uuid.uuid4())
        record = {
            "id": run_id,
            "status": "running",
            "started_at": _utc_now(),
            "finished_at": None,
            "summary": None,
            "error": None,
            "candidate_id": candidate_id,
        }
        _runs[run_id] = record

        def _target():
            try:
                from jobagent.pipeline import run_cycle

                summary = run_cycle(candidate_id=candidate_id)
                record["status"] = "completed"
                record["summary"] = summary
            except Exception as e:
                logger.exception("Cycle failed")
                record["status"] = "failed"
                record["error"] = str(e)
                record["traceback"] = traceback.format_exc()
            finally:
                record["finished_at"] = _utc_now()

        thread = threading.Thread(target=_target, name=f"jobagent-{run_id[:8]}", daemon=True)
        thread.start()
        return record
