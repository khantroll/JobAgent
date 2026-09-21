"""JobAgent 2.0 web UI — Jinja templates + HTMX, optional REST under /api."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jobagent import AUTO_APPLY_ENABLED, __version__
from jobagent.db import candidates as cand_repo
from jobagent.db import init_db
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo
from jobagent.db import runs as run_repo
from jobagent.paths import uploads_dir
from jobagent.web import worker
from jobagent.web.auth import (
    COOKIE_NAME,
    api_token_configured,
    configured_token,
    is_public_ui_path,
    tokens_match,
)
from jobagent.web.routes import jobs as jobs_api
from jobagent.web.routes import matches as matches_api
from jobagent.web.routes import runs as runs_api
from jobagent.web.routes import stats as stats_api

BASE_PATH = os.environ.get("JOB_AGENT_ROOT_PATH", "").rstrip("/")
UI_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    uploads_dir()
    init_db()
    yield


app = FastAPI(title="JobAgent 2.0", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")
templates = Jinja2Templates(directory=UI_DIR / "templates")


@app.middleware("http")
async def require_ui_token(request: Request, call_next):
    """If JOB_AGENT_API_TOKEN is set, HTML UI requires a login cookie. /api uses Bearer."""
    expected = configured_token()
    if not expected:
        return await call_next(request)
    path = request.url.path
    if path.startswith("/api") or is_public_ui_path(path):
        return await call_next(request)
    provided = request.cookies.get(COOKIE_NAME)
    if tokens_match(provided, expected):
        return await call_next(request)
    return RedirectResponse(url("/login"), status_code=303)

api = FastAPI(title="JobAgent API", version=__version__, docs_url="/docs", openapi_url="/openapi.json")
api.include_router(jobs_api.router)
api.include_router(matches_api.router)
api.include_router(runs_api.router)
api.include_router(stats_api.router)


@api.get("/health")
def api_health():
    return {
        "ok": True,
        "version": __version__,
        "ui": "simple",
        "auth_required": api_token_configured(),
        "auto_apply_enabled": AUTO_APPLY_ENABLED,
        "candidates": cand_repo.count_candidates(),
    }


app.mount("/api", api)


@app.get("/login")
def login_page(request: Request):
    if not configured_token():
        return RedirectResponse(url("/"), status_code=303)
    return _render(
        request,
        "login.html",
        {"error": request.query_params.get("error")},
    )


@app.post("/login")
def login_submit(token: str = Form("")):
    expected = configured_token()
    if not expected:
        return RedirectResponse(url("/"), status_code=303)
    if not tokens_match(token.strip(), expected):
        return RedirectResponse(url("/login?error=invalid"), status_code=303)
    response = RedirectResponse(url("/"), status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        token.strip(),
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/logout")
def logout():
    dest = url("/login") if configured_token() else url("/")
    response = RedirectResponse(dest, status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


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
        from jobagent.sources.higheredjobs_catalog import (
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
    search_defaults = {
        "min_score": (cand or {}).get("min_match_score", 65),
        "salary_min": (cand or {}).get("salary_min", 0),
        "salary_max": (cand or {}).get("salary_max", 0),
        "keywords": (cand or {}).get("keywords_text") or "",
        "commute_auto_apply_minutes": (cand or {}).get("commute_auto_apply_minutes", 30),
        "commute_review_minutes": (cand or {}).get("commute_review_minutes", 90),
        "search_enabled": 1 if (cand is None or cand.get("search_enabled", 1) not in (0, "0")) else 0,
    }
    return {
        "candidate": cand,
        "search_defaults": search_defaults,
        "matches_url": url(f"/candidates/{cand['id']}/matches") if cand else "",
        "hej_category_ids": hej_text,
        "hej_suggestions": suggest_categories_for_titles(titles) if suggest_categories_for_titles and titles else [],
        "hej_catalog": load_catalog() if load_catalog else [],
        "hej_labels": hej_labels,
        "msg": msg,
    }


def _render(request: Request, name: str, context: dict | None = None):
    ctx = dict(context or {})
    expected = configured_token()
    cookie = request.cookies.get(COOKIE_NAME)
    ctx.setdefault("base", BASE_PATH)
    ctx.setdefault("version", __version__)
    ctx.setdefault("auto_apply_enabled", AUTO_APPLY_ENABLED)
    ctx.setdefault("auth_configured", bool(expected))
    ctx.setdefault(
        "ui_authenticated",
        (not expected) or tokens_match(cookie, expected),
    )
    return templates.TemplateResponse(request, name, ctx)


@app.get("/")
def index(request: Request, candidate_id: int | None = None):
    init_db()
    people = cand_repo.list_candidates()
    selected = cand_repo.get_candidate(candidate_id) if candidate_id else None
    stats = match_repo.dashboard_stats(selected["id"] if selected else None)
    return _render(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "people": people,
            "selected": selected,
            "runs": worker.list_runs(5),
            "history": run_repo.list_run_history(5),
            "run_error": request.query_params.get("run_error"),
            "import_report": run_repo.latest_import_report(),
        },
    )


@app.get("/candidates")
def candidates(request: Request):
    init_db()
    people = cand_repo.list_candidates()
    _, _, parse_category_ids_text, _ = _hej_helpers()
    for p in people:
        p["match_count"] = match_repo.count_matches(p["id"])
        if parse_category_ids_text:
            hej_ids = parse_category_ids_text(p.get("hej_category_ids") or "")
        else:
            hej_ids = []
        p["hej_count"] = len(hej_ids)
        p["hej_preview"] = ", ".join(str(i) for i in hej_ids[:4])
        if len(hej_ids) > 4:
            p["hej_preview"] += f" (+{len(hej_ids) - 4} more)"
    return _render(
        request,
        "candidates.html",
        {"people": people, "msg": request.query_params.get("msg")},
    )


@app.get("/candidates/new")
def new_candidate(request: Request):
    return _render(
        request,
        "candidate_form.html",
        _candidate_form_context(None, msg=request.query_params.get("msg")),
    )


def _match_query_params(
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    date_field: str = "matched_at",
    sort_by: str = "score",
    sort_dir: str = "desc",
    hide_duplicates: str = "",
    q: str = "",
    min_score: str = "",
    source: str = "",
    work_type: str = "",
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
    if q:
        params["q"] = q
    if min_score:
        params["min_score"] = min_score
    if hide_duplicates in ("1", "true", "on", "yes"):
        params["hide_duplicates"] = "1"
    if source:
        params["source"] = source
    if work_type:
        params["work_type"] = work_type
    return urlencode(params)


def _match_query_from_request(qp) -> str:
    return _match_query_params(
        status=qp.get("status", ""),
        date_from=qp.get("date_from", ""),
        date_to=qp.get("date_to", ""),
        date_field=qp.get("date_field", "matched_at"),
        sort_by=qp.get("sort_by", "status_updated_at"),
        sort_dir=qp.get("sort_dir", "desc"),
        hide_duplicates=qp.get("hide_duplicates", ""),
        q=qp.get("q", ""),
        min_score=qp.get("min_score", ""),
        source=qp.get("source", ""),
        work_type=qp.get("work_type", ""),
    )


@app.get("/candidates/{cid}/matches")
def candidate_matches(
    cid: int,
    request: Request,
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    date_field: str = "matched_at",
    sort_by: str = "score",
    sort_dir: str = "desc",
    hide_duplicates: str = "",
    q: str = "",
    min_score: str = "",
    source: str = "",
    work_type: str = "",
):
    init_db()
    cand = cand_repo.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    if date_field not in match_repo.MATCH_DATE_FIELDS:
        date_field = "matched_at"
    if sort_by not in match_repo.MATCH_SORT_FIELDS:
        sort_by = "score"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"
    min_score_val = None
    if str(min_score or "").strip():
        try:
            min_score_val = int(min_score)
        except ValueError as exc:
            raise HTTPException(400, detail="Min score must be a number") from exc
    total_all = match_repo.count_matches(cid)
    rows, total = match_repo.list_matches(
        cid,
        status=status or None,
        date_from=date_from.strip() or None,
        date_to=date_to.strip() or None,
        date_field=date_field,
        sort_by=sort_by,
        sort_dir=sort_dir,
        q=q or None,
        min_score=min_score_val,
        source=source or None,
        work_type=work_type or None,
    )
    match_repo.annotate_match_duplicates(rows)
    duplicate_count = sum(1 for r in rows if r.get("is_duplicate"))
    if hide_duplicates in ("1", "true", "on", "yes"):
        rows = [r for r in rows if not r.get("is_duplicate")]
        total = len(rows)
    qstr = _match_query_params(
        status, date_from, date_to, date_field, sort_by, sort_dir, hide_duplicates, q, min_score,
        source, work_type,
    )
    return _render(
        request,
        "matches.html",
        {
            "candidate": cand,
            "matches": rows,
            "total": total,
            "total_all": total_all,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
            "date_field": date_field,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "q": q,
            "min_score": min_score,
            "source": source,
            "work_type": work_type,
            "catalog_sources": job_repo.list_sources(),
            "work_types": ["remote", "hybrid", "onsite"],
            "date_filter_on": bool(date_from or date_to),
            "statuses": sorted(match_repo.MATCH_STATUSES),
            "sort_fields": sorted(match_repo.MATCH_SORT_FIELDS),
            "filter_q": qstr,
            "export_url": url(f"/candidates/{cid}/matches/export?{qstr}"),
            "show_all_url": url(f"/candidates/{cid}/matches"),
            "duplicate_count": duplicate_count,
            "hide_duplicates": hide_duplicates in ("1", "true", "on", "yes"),
            "msg": request.query_params.get("msg"),
        },
    )


@app.get("/candidates/{cid}/matches/export")
def export_candidate_matches(
    cid: int,
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    date_field: str = "matched_at",
    sort_by: str = "status_updated_at",
    sort_dir: str = "desc",
    q: str = "",
    min_score: str = "",
):
    init_db()
    cand = cand_repo.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    min_score_val = int(min_score) if str(min_score or "").strip() else None
    csv_text = match_repo.export_matches_csv(
        cid,
        status=status or None,
        date_from=date_from.strip() or None,
        date_to=date_to.strip() or None,
        date_field=date_field,
        sort_by=sort_by,
        sort_dir=sort_dir,
        q=q or None,
        min_score=min_score_val,
    )
    safe = "".join(c if c.isalnum() else "_" for c in cand["name"])[:40]
    return Response(
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe}-job-matches.csv"'},
    )


@app.post("/candidates/{cid}/matches/clear")
def clear_matches(cid: int, request: Request):
    cand = cand_repo.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    removed = match_repo.clear_candidate_matches(cid)
    msg = f"Removed {removed} job link(s) from {cand['name']}'s list."
    back = _match_query_from_request(request.query_params)
    return RedirectResponse(url(f"/candidates/{cid}/matches?{back}&msg={quote(msg)}"), status_code=303)


@app.post("/candidates/{cid}/matches/ignore-duplicates")
def ignore_dups(cid: int, request: Request):
    cand = cand_repo.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    n = match_repo.ignore_duplicate_matches(cid)
    msg = (
        f"Marked {n} duplicate listing(s) as ignored for {cand['name']}."
        if n
        else f"No duplicate listings to ignore for {cand['name']}."
    )
    back = _match_query_from_request(request.query_params)
    return RedirectResponse(url(f"/candidates/{cid}/matches?{back}&msg={quote(msg)}"), status_code=303)


@app.post("/candidates/{cid}/matches/link-all")
def link_all_matches(cid: int, request: Request):
    cand = cand_repo.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    added = match_repo.link_all_jobs_to_candidate(cid)
    msg = f"Linked {added} job(s) to {cand['name']}."
    back = _match_query_from_request(request.query_params)
    return RedirectResponse(url(f"/candidates/{cid}/matches?{back}&msg={quote(msg)}"), status_code=303)


@app.post("/candidates/{cid}/matches/{jid}/status")
def update_match_status(
    cid: int,
    jid: str,
    request: Request,
    status: str = Form(...),
    status_reason: str = Form(""),
):
    if not cand_repo.get_candidate(cid):
        raise HTTPException(404)
    try:
        ok = match_repo.update_match_status(cid, jid, status, status_reason=status_reason)
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
    if not ok:
        match_repo.link_job_to_candidate(jid, cid, status=status)
        match_repo.update_match_status(cid, jid, status, status_reason=status_reason)
    if request.headers.get("HX-Request"):
        match = match_repo.get_match(cid, jid)
        return _render(
            request,
            "match_row.html",
            {
                "m": match,
                "candidate": cand_repo.get_candidate(cid),
                "statuses": sorted(match_repo.MATCH_STATUSES),
                "status": request.query_params.get("status", ""),
                "filter_q": _match_query_from_request(request.query_params),
            },
        )
    back = _match_query_from_request(request.query_params)
    return RedirectResponse(url(f"/candidates/{cid}/matches?{back}"), status_code=303)


@app.get("/candidates/{cid}")
def edit_candidate(cid: int, request: Request):
    cand = cand_repo.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    return _render(
        request,
        "candidate_form.html",
        _candidate_form_context(cand, msg=request.query_params.get("msg")),
    )


@app.post("/candidates/{cid}/suggest-hej-categories")
def suggest_hej_categories(cid: int):
    cand = cand_repo.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    from jobagent.sources.higheredjobs_catalog import (
        format_category_ids_text,
        suggest_category_ids_for_titles,
    )

    titles = [t["title"] for t in cand.get("titles", [])]
    ids = suggest_category_ids_for_titles(titles)
    if not ids:
        msg = "No HigherEdJobs categories matched those titles."
        return RedirectResponse(url(f"/candidates/{cid}?msg={quote(msg)}"), status_code=303)
    cand_repo.update_hej_category_ids(cid, format_category_ids_text(ids))
    msg = f"Suggested {len(ids)} HigherEdJobs category ID(s) from job titles. Review and Save."
    return RedirectResponse(url(f"/candidates/{cid}?msg={quote(msg)}"), status_code=303)


@app.post("/candidates/{cid}/delete")
def delete_candidate(cid: int):
    cand = cand_repo.get_candidate(cid)
    if not cand:
        raise HTTPException(404)
    name = cand["name"]
    cand_repo.delete_candidate(cid)
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
    commute_auto_apply_minutes: str = Form("30"),
    commute_review_minutes: str = Form("90"),
    search_enabled: list[str] = Form([]),
    resume_file: UploadFile | None = File(None),
):
    init_db()
    saved_file = ""
    upload_root = uploads_dir()
    if resume_file and resume_file.filename:
        safe_name = "".join(
            ch for ch in resume_file.filename if ch.isalnum() or ch in "-_."
        ).strip()[:120]
        target = upload_root / f"{_safe_upload_prefix(name)}_{safe_name}"
        content = await resume_file.read()
        target.write_bytes(content)
        saved_file = str(target)
        if not resume_text.strip() and safe_name.lower().endswith((".txt", ".md")):
            resume_text = content.decode("utf-8", errors="replace")
    elif candidate_id:
        old = cand_repo.get_candidate(candidate_id)
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
    cid = cand_repo.save_candidate(
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
            "commute_auto_apply_minutes": _form_int(commute_auto_apply_minutes, 30),
            "commute_review_minutes": _form_int(commute_review_minutes, 90),
            "search_enabled": 1 if "1" in search_enabled else 0,
        },
        titles,
        employers,
        candidate_id,
    )
    return RedirectResponse(url(f"/candidates/{cid}"), status_code=303)


@app.get("/jobs")
def jobs_page(
    request: Request,
    q: str = "",
    source: str = "",
    candidate_id: str = "",
    status: str = "",
    min_score: str = "",
    sort_by: str = "found_at",
    sort_dir: str = "desc",
):
    init_db()
    people = cand_repo.list_candidates()
    cid = int(candidate_id) if str(candidate_id).strip().isdigit() else None
    selected = cand_repo.get_candidate(cid) if cid else None
    min_score_val = int(min_score) if str(min_score or "").strip() else None
    if selected:
        if sort_by not in match_repo.MATCH_SORT_FIELDS:
            sort_by = "found_at"
        rows, total = match_repo.list_matches(
            selected["id"],
            status=status or None,
            q=q or None,
            min_score=min_score_val,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=100,
        )
        match_repo.annotate_match_duplicates(rows)
        sort_fields = sorted(match_repo.MATCH_SORT_FIELDS)
    else:
        if sort_by not in job_repo.JOB_SORT_FIELDS:
            sort_by = "found_at"
        rows, total = job_repo.list_jobs(
            source=source or None,
            q=q or None,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=100,
        )
        sort_fields = sorted(job_repo.JOB_SORT_FIELDS)
    return _render(
        request,
        "jobs.html",
        {
            "jobs": rows,
            "total": total,
            "q": q,
            "source": source,
            "sources": job_repo.list_sources(),
            "status": status,
            "min_score": min_score,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "sort_fields": sort_fields,
            "people": people,
            "selected": selected,
            "candidate_id": cid or "",
            "statuses": sorted(match_repo.MATCH_STATUSES),
        },
    )


@app.post("/run")
def run_now(candidate_id: Optional[int] = Form(None)):
    try:
        worker.start_run(candidate_id=candidate_id)
    except RuntimeError as e:
        return RedirectResponse(url(f"/?run_error={quote(str(e))}"), status_code=303)
    return RedirectResponse(url("/"), status_code=303)


@app.get("/runs")
def runs_page(request: Request):
    return _render(
        request,
        "runs.html",
        {
            "history": run_repo.list_run_history(50),
            "runs": worker.list_runs(50),
        },
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "version": __version__,
        "ui": "simple",
        "auto_apply_enabled": AUTO_APPLY_ENABLED,
    }
