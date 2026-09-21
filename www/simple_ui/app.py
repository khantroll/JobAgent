"""
Job Agent web UI — HTML templates + optional REST under /api.

Run: uvicorn simple_ui.app:app --host 127.0.0.1 --port 8765
Yunohost: set JOB_AGENT_ROOT_PATH=/jobagent for template links only.
nginx must strip /jobagent before proxy_pass (do not set FastAPI root_path).
"""
import os
import sys
from datetime import date, timedelta
from urllib.parse import quote, urlencode
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import worker
from api.auth import api_token_configured
from api.routes import jobs, matches, runs, stats as stats_api
from agents.apply_pipeline import (
    apply_all_reviewed_matches,
    apply_reviewed_match,
    load_base_config,
)
from data import db as jobs_db
from simple_ui import db
from simple_ui.config_writer import list_profile_backups, restore_profile_from_backup, write_active_profile
from simple_ui.profile_import import (
    ensure_profile_imported,
    search_defaults_for_candidate,
    sync_from_profile_yaml,
)

# Public URL prefix for HTML links (e.g. /jobagent). Not passed to FastAPI root_path
# when nginx strips that prefix before proxying to uvicorn.
BASE_PATH = os.environ.get("JOB_AGENT_ROOT_PATH", "").rstrip("/")
UPLOAD_DIR = ROOT / "uploads"

UI_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    log = logging.getLogger("simple_ui.startup")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    (ROOT / "config" / "backups").mkdir(parents=True, exist_ok=True)
    try:
        db.init()
        jobs_db.init_db()
    except Exception:
        log.exception("Database init failed")
        raise
    try:
        ensure_profile_imported()
    except Exception:
        log.exception(
            "Profile import skipped (fix config/profile.yaml or use People → Import)"
        )
    yield


app = FastAPI(title="Job Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")
templates = Jinja2Templates(directory=UI_DIR / "templates")

# REST API (optional clients / scripts)
api = FastAPI(title="Job Agent API", docs_url="/docs", openapi_url="/openapi.json")
api.include_router(jobs.router)
api.include_router(matches.router)
api.include_router(runs.router)
api.include_router(stats_api.router)


@api.get("/health")
def api_health():
    active = db.active_candidate()
    return {
        "ok": True,
        "ui": "simple",
        "auth_required": api_token_configured(),
        "active_candidate": active["name"] if active else None,
    }


app.mount("/api", api)


def url(path: str = "") -> str:
    return f"{BASE_PATH}{path}" if BASE_PATH else path


def _format_date(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else text


templates.env.filters["format_date"] = _format_date


def _hej_helpers():
    try:
        from agents.sources.higheredjobs_catalog import (
            catalog_names_by_id,
            load_catalog,
            parse_category_ids_text,
            suggest_categories_for_titles,
        )
        return catalog_names_by_id, load_catalog, parse_category_ids_text, suggest_categories_for_titles
    except ImportError:
        return None, None, None, None


def _candidate_form_context(cand: dict | None, *, msg: str | None = None) -> dict:
    catalog_names_by_id, load_catalog, _, suggest_categories_for_titles = _hej_helpers()

    titles = [t["title"] for t in (cand or {}).get("titles", [])]
    hej_text = (cand or {}).get("hej_category_ids") or ""
    hej_labels = []
    if catalog_names_by_id:
        names = catalog_names_by_id()
        for cid in hej_text.replace(",", "\n").split():
            cid = cid.strip()
            if cid.isdigit():
                hej_labels.append(f"{cid} — {names.get(int(cid), 'unknown')}")
    return {
        "candidate": cand,
        "search_defaults": search_defaults_for_candidate(cand),
        "matches_url": url(f"/candidates/{cand['id']}/matches") if cand else "",
        "hej_category_ids": hej_text,
        "hej_suggestions": suggest_categories_for_titles(titles) if suggest_categories_for_titles and titles else [],
        "hej_catalog": load_catalog() if load_catalog else [],
        "hej_labels": hej_labels,
        "msg": msg,
    }


def _render(request: Request, name: str, context: dict | None = None):
    """Starlette 0.29+ requires TemplateResponse(request, name, context)."""
    ctx = dict(context or {})
    ctx.setdefault("base", BASE_PATH)
    return templates.TemplateResponse(request, name, ctx)


@app.get("/")
def index(request: Request):
    try:
        jobs_db.init_db()
        db.init()
        dash_stats = jobs_db.get_dashboard_stats()
        active = db.active_candidate()
        return _render(
            request,
            "dashboard.html",
            {
                "stats": dash_stats,
                "active": active,
                "runs": worker.list_runs(5),
                "history": jobs_db.list_run_log(5),
                "run_error": request.query_params.get("run_error"),
            },
        )
    except Exception:
        import logging
        logging.exception("Dashboard render failed")
        raise


@app.get("/candidates")
def candidates(request: Request):
    db.init()
    jobs_db.init_db()
    jobs_db.ensure_job_match_schema()
    people = db.rows(
        "SELECT * FROM candidates ORDER BY active DESC, updated_at DESC"
    )
    _, _, parse_category_ids_text, _ = _hej_helpers()
    for p in people:
        p["match_count"] = jobs_db.count_candidate_matches(p["id"])
        if parse_category_ids_text:
            hej_ids = parse_category_ids_text(p.get("hej_category_ids") or "")
        else:
            hej_ids = []
        p["hej_count"] = len(hej_ids)
        p["hej_preview"] = ", ".join(str(i) for i in hej_ids[:4])
        if len(hej_ids) > 4:
            p["hej_preview"] += f" (+{len(hej_ids) - 4} more)"
    backups = list_profile_backups()
    return _render(
        request,
        "candidates.html",
        {
            "people": people,
            "msg": request.query_params.get("msg"),
            "profile_path": "config/profile.yaml",
            "profile_backups": [b.name for b in backups[:5]],
        },
    )


@app.get("/candidates/new")
def new_candidate(request: Request):
    return _render(
        request,
        "candidate_form.html",
        _candidate_form_context(None, msg=request.query_params.get("msg")),
    )


def _default_match_dates() -> tuple[str, str]:
    """Empty bounds = show all linked jobs (avoids hiding older crawls)."""
    return "", ""


def _all_time_query() -> str:
    return urlencode({"date_field": "matched_at"})


def _match_query_params(
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    date_field: str = "matched_at",
    sort_by: str = "status_updated_at",
    sort_dir: str = "desc",
    hide_duplicates: str = "",
) -> str:
    params = {
        "date_field": date_field,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if status:
        params["status"] = status
    if hide_duplicates in ("1", "true", "on", "yes"):
        params["hide_duplicates"] = "1"
    return urlencode(params)


def _match_query_from_request(q, *, defaults: dict | None = None) -> str:
    defaults = defaults or {}
    return _match_query_params(
        status=q.get("status", defaults.get("status", "")),
        date_from=q.get("date_from", defaults.get("date_from", "")),
        date_to=q.get("date_to", defaults.get("date_to", "")),
        date_field=q.get("date_field", defaults.get("date_field", "matched_at")),
        sort_by=q.get("sort_by", defaults.get("sort_by", "status_updated_at")),
        sort_dir=q.get("sort_dir", defaults.get("sort_dir", "desc")),
        hide_duplicates=q.get("hide_duplicates", defaults.get("hide_duplicates", "")),
    )


@app.get("/candidates/{cid}/matches")
def candidate_matches(
    cid: int,
    request: Request,
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    date_field: str = "matched_at",
    sort_by: str = "status_updated_at",
    sort_dir: str = "desc",
    hide_duplicates: str = "",
):
    import logging

    try:
        jobs_db.init_db()
        jobs_db.ensure_job_match_schema()
        db.init()
        cand = db.get_candidate(cid)
        if not cand:
            raise HTTPException(404)
        d_from, d_to = date_from.strip(), date_to.strip()
        if date_field not in jobs_db.MATCH_DATE_FIELDS:
            date_field = "matched_at"
        if sort_by not in jobs_db.MATCH_SORT_FIELDS:
            sort_by = "status_updated_at"
        if sort_dir not in ("asc", "desc"):
            sort_dir = "desc"
        total_all = jobs_db.count_candidate_matches(cid)
        rows, total = jobs_db.list_candidate_job_matches(
            cid,
            status=status or None,
            date_from=d_from or None,
            date_to=d_to or None,
            date_field=date_field,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
        duplicate_count = 0
        if hasattr(jobs_db, "annotate_match_duplicates"):
            jobs_db.annotate_match_duplicates(rows)
            duplicate_count = sum(1 for r in rows if r.get("is_duplicate"))
        if hide_duplicates in ("1", "true", "on", "yes"):
            rows = [r for r in rows if not r.get("is_duplicate")]
            total = len(rows)
        q = _match_query_params(
            status,
            d_from,
            d_to,
            date_field,
            sort_by,
            sort_dir,
            hide_duplicates,
        )
        active = db.active_candidate()
        others = []
        for p in db.rows(
            "SELECT id, name FROM candidates WHERE id != ? ORDER BY name",
            (cid,),
        ):
            mc = jobs_db.count_candidate_matches(p["id"])
            if mc > 0:
                others.append({"id": p["id"], "name": p["name"], "match_count": mc})
        others.sort(key=lambda x: x["match_count"], reverse=True)
        reviewed_count = jobs_db.count_candidate_matches(cid, status="reviewed")
        scheduler = load_base_config().get("scheduler", {})
        return _render(
            request,
            "matches.html",
            {
                "candidate": cand,
                "matches": rows,
                "total": total,
                "total_all": total_all,
                "status": status,
                "date_from": d_from,
                "date_to": d_to,
                "date_field": date_field,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "date_filter_on": bool(d_from or d_to),
                "statuses": sorted(jobs_db.MATCH_STATUSES),
                "sort_fields": sorted(jobs_db.MATCH_SORT_FIELDS),
                "filter_q": q,
                "export_url": url(f"/candidates/{cid}/matches/export?{q}"),
                "show_all_url": url(f"/candidates/{cid}/matches?{_all_time_query()}"),
                "active_name": active["name"] if active else None,
                "is_active": bool(active and active["id"] == cid),
                "others_with_jobs": others,
                "duplicate_count": duplicate_count,
                "hide_duplicates": hide_duplicates in ("1", "true", "on", "yes"),
                "reviewed_count": reviewed_count,
                "dry_run": scheduler.get("dry_run", True),
                "msg": request.query_params.get("msg"),
            },
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
    except AttributeError as e:
        logging.exception("Job matches unavailable (deploy data/db.py)")
        raise HTTPException(
            500,
            detail="Job matches requires an updated data/db.py on the server.",
        ) from e
    except Exception:
        logging.exception("Job matches page failed for candidate %s", cid)
        raise


@app.get("/candidates/{cid}/matches/export")
def export_candidate_matches(
    cid: int,
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    date_field: str = "matched_at",
    sort_by: str = "status_updated_at",
    sort_dir: str = "desc",
):
    jobs_db.init_db()
    jobs_db.ensure_job_match_schema()
    cand = db.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    d_from, d_to = date_from.strip(), date_to.strip()
    if date_field not in jobs_db.MATCH_DATE_FIELDS:
        date_field = "matched_at"
    if sort_by not in jobs_db.MATCH_SORT_FIELDS:
        sort_by = "status_updated_at"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"
    csv_text = jobs_db.export_candidate_matches_csv(
        cid,
        status=status or None,
        date_from=d_from or None,
        date_to=d_to or None,
        date_field=date_field,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    safe = "".join(c if c.isalnum() else "_" for c in cand["name"])[:40]
    return Response(
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}-job-matches.csv"'
        },
    )


@app.post("/candidates/{cid}/matches/clear")
def clear_matches(cid: int, request: Request):
    jobs_db.init_db()
    cand = db.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    removed = jobs_db.clear_candidate_matches(cid)
    msg = f"Removed {removed} job link(s) from {cand['name']}'s list."
    back = _match_query_from_request(request.query_params)
    return RedirectResponse(
        url(f"/candidates/{cid}/matches?{back}&msg={quote(msg)}"),
        status_code=303,
    )


@app.post("/candidates/{cid}/matches/claim-from/{from_cid}")
def claim_matches_from(cid: int, from_cid: int, request: Request):
    jobs_db.init_db()
    target = db.get_candidate(cid)
    source = db.get_candidate(from_cid)
    if not target or not source:
        raise HTTPException(404)
    try:
        result = jobs_db.transfer_job_history(from_cid, cid)
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
    msg = (
        f"Moved {result['moved']} job(s) from {source['name']} to {target['name']}. "
        f"{source['name']} now has 0 linked jobs."
    )
    if result["added_to_target"]:
        msg += f" Also attached {result['added_to_target']} extra listing(s) from the database."
    back = _match_query_from_request(request.query_params)
    return RedirectResponse(
        url(f"/candidates/{cid}/matches?{back}&msg={quote(msg)}"),
        status_code=303,
    )


@app.post("/candidates/{cid}/matches/ignore-duplicates")
def ignore_duplicate_matches(cid: int, request: Request):
    jobs_db.init_db()
    cand = db.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    n = jobs_db.ignore_duplicate_matches(cid)
    msg = (
        f"Marked {n} duplicate listing(s) as ignored for {cand['name']}."
        if n
        else f"No duplicate listings to ignore for {cand['name']}."
    )
    back = _match_query_from_request(request.query_params)
    return RedirectResponse(
        url(f"/candidates/{cid}/matches?{back}&msg={quote(msg)}"),
        status_code=303,
    )


@app.post("/candidates/{cid}/matches/link-all")
def link_all_matches(cid: int, request: Request):
    jobs_db.init_db()
    cand = db.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    added = jobs_db.link_all_jobs_to_candidate(cid)
    msg = f"Linked {added} job(s) to {cand['name']}."
    back = _match_query_from_request(request.query_params)
    return RedirectResponse(
        url(f"/candidates/{cid}/matches?{back}&msg={quote(msg)}"),
        status_code=303,
    )


@app.post("/candidates/{cid}/matches/{jid}/status")
def update_match_status(
    cid: int,
    jid: str,
    status: str = Form(...),
    status_reason: str = Form(""),
    filter_status: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    date_field: str = Form("matched_at"),
    sort_by: str = Form("status_updated_at"),
    sort_dir: str = Form("desc"),
    hide_duplicates: str = Form(""),
):
    jobs_db.init_db()
    if not db.get_candidate(cid):
        raise HTTPException(404)
    try:
        ok = jobs_db.update_candidate_match_status(
            cid, jid, status, status_reason=status_reason
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
    if not ok:
        jobs_db.link_job_to_candidate(
            jid, cid, status=status, status_reason=status_reason, allow_duplicate=True
        )
        jobs_db.update_candidate_match_status(
            cid, jid, status, status_reason=status_reason
        )
    q = _match_query_params(
        filter_status,
        date_from,
        date_to,
        date_field,
        sort_by,
        sort_dir,
        hide_duplicates,
    )
    return RedirectResponse(url(f"/candidates/{cid}/matches?{q}"), status_code=303)


@app.post("/candidates/{cid}/matches/{jid}/apply")
def apply_match(cid: int, jid: str, request: Request):
    jobs_db.init_db()
    cand = db.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    result = apply_reviewed_match(cid, jid, cand)
    back = _match_query_from_request(request.query_params)
    msg = result["message"]
    return RedirectResponse(
        url(f"/candidates/{cid}/matches?{back}&msg={quote(msg)}"),
        status_code=303,
    )


@app.post("/candidates/{cid}/matches/apply-reviewed")
def apply_all_reviewed(cid: int, request: Request):
    jobs_db.init_db()
    cand = db.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    batch = apply_all_reviewed_matches(cid, cand)
    back = _match_query_from_request(request.query_params)
    msg = batch["summary"]
    return RedirectResponse(
        url(f"/candidates/{cid}/matches?{back}&msg={quote(msg)}"),
        status_code=303,
    )


@app.get("/candidates/{cid}")
def edit_candidate(cid: int, request: Request):
    cand = db.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    return _render(
        request,
        "candidate_form.html",
        _candidate_form_context(cand, msg=request.query_params.get("msg")),
    )


@app.post("/candidates/{cid}/suggest-hej-categories")
def suggest_hej_categories(cid: int):
    cand = db.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    from agents.sources.higheredjobs_catalog import (
        format_category_ids_text,
        suggest_category_ids_for_titles,
    )

    titles = [t["title"] for t in cand.get("titles", [])]
    ids = suggest_category_ids_for_titles(titles)
    if not ids:
        msg = "No HigherEdJobs categories matched those titles. Add titles above or enter IDs manually."
        return RedirectResponse(url(f"/candidates/{cid}?msg={quote(msg)}"), status_code=303)
    db.update_hej_category_ids(cid, format_category_ids_text(ids))
    msg = f"Suggested {len(ids)} HigherEdJobs category ID(s) from job titles. Review and Save."
    return RedirectResponse(url(f"/candidates/{cid}?msg={quote(msg)}"), status_code=303)


@app.post("/candidates/restore-profile-backup")
def restore_profile_backup():
    try:
        path = restore_profile_from_backup()
    except FileNotFoundError as e:
        msg = str(e)
        return RedirectResponse(url(f"/candidates?msg={quote(msg)}"), status_code=303)
    except Exception as e:
        msg = f"Could not restore backup: {e}"
        return RedirectResponse(url(f"/candidates?msg={quote(msg)}"), status_code=303)
    try:
        sync_from_profile_yaml(set_active=False)
    except Exception as e:
        msg = f"Restored profile.yaml from {path.name} but could not sync to People: {e}"
        return RedirectResponse(url(f"/candidates?msg={quote(msg)}"), status_code=303)
    msg = f"Restored profile.yaml from {path.name}. Use Import from profile.yaml if People is empty."
    return RedirectResponse(url(f"/candidates?msg={quote(msg)}"), status_code=303)


@app.post("/candidates/import-profile")
def import_profile(request: Request):
    cid, action = sync_from_profile_yaml(set_active=True)
    if not cid:
        msg = "Could not import: config/profile.yaml missing or has no profile name."
    elif action == "updated":
        msg = f"Updated person #{cid} from profile.yaml and set them active."
    else:
        msg = f"Added person #{cid} from profile.yaml and set them active."
    return RedirectResponse(url(f"/candidates?msg={quote(msg)}"), status_code=303)


@app.post("/candidates/{cid}/set-active")
def set_active_quick(cid: int):
    cand = db.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    prefs = search_defaults_for_candidate(cand)
    db.set_active(cid)
    try:
        write_active_profile(cand, **prefs)
        msg = f"Set {cand['name']} active and wrote profile.yaml"
    except Exception as e:
        msg = f"Set {cand['name']} active but could not write profile.yaml: {e}"
    return RedirectResponse(url(f"/candidates?msg={quote(msg)}"), status_code=303)


@app.post("/candidates/{cid}/delete")
def delete_candidate(cid: int):
    cand = db.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    name = cand["name"]
    jobs_db.clear_candidate_matches(cid)
    db.delete_candidate(cid)
    msg = f"Deleted {name} and all their job matches."
    return RedirectResponse(url(f"/candidates?msg={quote(msg)}"), status_code=303)



def _safe_upload_prefix(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (name or "person"))
    safe = safe.strip("._-")[:80]
    return safe or "person"


def _form_int(value, default: int = 0) -> int:
    text = str(value or "").strip()
    if text == "":
        return default
    try:
        return int(text)
    except ValueError as exc:
        raise HTTPException(400, detail=f"Expected a whole number, got: {text}") from exc

def parse_employers(names, types, careers, workdays, greenhouses, levers):
    out = []
    n = len(names) if names else 0
    for i in range(n):
        out.append(
            {
                "name": names[i] if i < len(names) else "",
                "source_type": types[i] if i < len(types) else "workday",
                "careers_url": careers[i] if i < len(careers) else "",
                "workday_url": workdays[i] if i < len(workdays) else "",
                "greenhouse_slug": greenhouses[i] if i < len(greenhouses) else "",
                "lever_slug": levers[i] if i < len(levers) else "",
            }
        )
    return out


@app.post("/candidates/save")
async def save_candidate(
    request: Request,
    candidate_id: Optional[int] = Form(None),
    name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    location: str = Form(""),
    linkedin: str = Form(""),
    github: str = Form(""),
    resume_text: str = Form(""),
    titles_text: str = Form(""),
    employer_name: list[str] = Form([]),
    employer_source: list[str] = Form([]),
    careers_url: list[str] = Form([]),
    workday_url: list[str] = Form([]),
    greenhouse_slug: list[str] = Form([]),
    lever_slug: list[str] = Form([]),
    min_score: str = Form("65"),
    salary_min: str = Form("0"),
    salary_max: str = Form("0"),
    keywords: str = Form(""),
    hej_category_ids: str = Form(""),
    resume_file: UploadFile | None = File(None),
):
    db.init()
    saved_file = ""
    if resume_file and resume_file.filename:
        safe_name = "".join(
            ch for ch in resume_file.filename if ch.isalnum() or ch in "-_."
        ).strip()[:120]
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = UPLOAD_DIR / f"{_safe_upload_prefix(name)}_{safe_name}"
        content = await resume_file.read()
        target.write_bytes(content)
        saved_file = str(target.relative_to(ROOT))
        if not resume_text.strip() and safe_name.lower().endswith((".txt", ".md")):
            resume_text = content.decode("utf-8", errors="replace")
    elif candidate_id:
        old = db.get_candidate(candidate_id)
        saved_file = old.get("resume_file", "") if old else ""

    titles = titles_text.replace("\r", "").split("\n")
    employers = parse_employers(
        employer_name,
        employer_source,
        careers_url,
        workday_url,
        greenhouse_slug,
        lever_slug,
    )
    cid = db.save_candidate(
        {
            "name": name,
            "email": email,
            "phone": phone,
            "location": location,
            "linkedin": linkedin,
            "github": github,
            "resume_text": resume_text,
            "resume_file": saved_file,
            "min_match_score": _form_int(min_score, 65),
            "salary_min": _form_int(salary_min, 0),
            "salary_max": _form_int(salary_max, 0),
            "keywords_text": keywords.replace("\r", ""),
            "hej_category_ids": hej_category_ids.replace("\r", ""),
        },
        titles,
        employers,
        candidate_id,
    )
    return RedirectResponse(url(f"/candidates/{cid}"), status_code=303)


@app.post("/candidates/{cid}/activate")
def activate(
    cid: int,
    min_score: str = Form("65"),
    salary_min: str = Form("0"),
    salary_max: str = Form("0"),
    keywords: str = Form(""),
):
    cand = db.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    kw = keywords.replace("\r", "")
    min_score_i = _form_int(min_score, 65)
    salary_min_i = _form_int(salary_min, 0)
    salary_max_i = _form_int(salary_max, 0)
    db.update_search_prefs(
        cid,
        min_match_score=min_score_i,
        salary_min=salary_min_i,
        salary_max=salary_max_i,
        keywords_text=kw,
    )
    cand = db.get_candidate(cid)
    db.set_active(cid)
    try:
        write_active_profile(
            cand,
            min_score=min_score_i,
            salary_min=salary_min_i,
            salary_max=salary_max_i,
            keywords=kw,
        )
        msg = f"Saved search settings to profile.yaml for {cand['name']}"
    except Exception as e:
        msg = f"Person set active but could not write profile.yaml: {e}"
    return RedirectResponse(url(f"/candidates?msg={quote(msg)}"), status_code=303)


@app.get("/jobs")
def jobs_page(
    request: Request,
    status: str = "",
    q: str = "",
    min_score: str = "",
    sort_by: str = "found_at",
    sort_dir: str = "desc",
):
    import logging

    jobs_db.init_db()
    sort_fields = sorted(getattr(jobs_db, "JOB_SORT_FIELDS", {"found_at", "score", "title", "company"}))
    if sort_by not in sort_fields:
        sort_by = "found_at"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"
    min_score_val = None
    if str(min_score or "").strip():
        try:
            min_score_val = int(min_score)
        except ValueError:
            raise HTTPException(400, detail="Min score must be a number") from None
    try:
        rows, total = jobs_db.list_jobs(
            status=status or None,
            q=q or None,
            min_score=min_score_val,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=100,
        )
    except Exception:
        logging.exception("Jobs list failed")
        rows, total = jobs_db.list_jobs(
            status=status or None,
            q=q or None,
            min_score=min_score_val,
            limit=100,
        )
        sort_by = "found_at"
        sort_dir = "desc"
    return _render(
        request,
        "jobs.html",
        {
            "jobs": rows,
            "total": total,
            "status": status,
            "q": q,
            "min_score": min_score,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "sort_fields": sort_fields,
        },
    )


@app.post("/jobs/{jid}/status")
def update_status(jid: str, status: str = Form(...)):
    jobs_db.update_job_status(jid, status)
    return RedirectResponse(url("/jobs"), status_code=303)


@app.post("/run")
def run_now():
    try:
        worker.start_run()
    except RuntimeError as e:
        return RedirectResponse(
            url(f"/?run_error={e}"),
            status_code=303,
        )
    return RedirectResponse(url("/"), status_code=303)


@app.get("/runs")
def runs_page(request: Request):
    return _render(
        request,
        "runs.html",
        {
            "history": jobs_db.list_run_log(50),
            "runs": worker.list_runs(50),
        },
    )


@app.get("/health")
def health():
    active = db.active_candidate()
    return {
        "ok": True,
        "ui": "simple",
        "active_candidate": active["name"] if active else None,
    }
