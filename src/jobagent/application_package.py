"""Human-reviewed application package workflow.

Generates and edits candidate/job-specific resume and cover-letter text.
Nothing here submits an application.
"""
from __future__ import annotations

from pathlib import Path

from jobagent.config import candidate_runtime_config, load_settings
from jobagent.db import candidates as cand_repo
from jobagent.db import matches as match_repo
from jobagent.doc_gen import generate_docs
from jobagent.paths import output_dir


class PackageError(ValueError):
    pass


def _safe_package_path(raw: str | None, *, candidate_id: int) -> Path:
    if not raw:
        raise PackageError("Application package has not been generated.")
    path = Path(raw).resolve()
    root = output_dir().resolve()
    allowed = {
        (root / "resumes" / str(candidate_id)).resolve(),
        (root / "cover_letters" / str(candidate_id)).resolve(),
    }
    if path.parent not in allowed:
        raise PackageError("Package path is outside this candidate's output directory.")
    return path


def generate_package(candidate_id: int, job_id: str) -> dict:
    candidate = cand_repo.get_candidate(candidate_id)
    match = match_repo.get_match(candidate_id, job_id)
    if not candidate or not match:
        raise PackageError("Candidate/job match not found.")
    if match.get("status") in {"ignored", "rejected"}:
        raise PackageError("Approve the match before generating an application package.")
    if not (candidate.get("resume_text") or "").strip():
        raise PackageError("Candidate resume text is required before generation.")
    config = candidate_runtime_config(candidate, load_settings())
    resume_path, cover_path = generate_docs(match, config, candidate_id=candidate_id)
    match_repo.update_match_documents(candidate_id, job_id, resume_path, cover_path)
    return get_package(candidate_id, job_id)


def get_package(candidate_id: int, job_id: str) -> dict:
    match = match_repo.get_match(candidate_id, job_id)
    if not match:
        raise PackageError("Candidate/job match not found.")
    out = {"match": match, "resume_text": "", "cover_text": "", "generated": False}
    if match.get("resume_path") and match.get("cover_path"):
        resume = _safe_package_path(match["resume_path"], candidate_id=candidate_id)
        cover = _safe_package_path(match["cover_path"], candidate_id=candidate_id)
        if resume.is_file() and cover.is_file():
            out.update(
                resume_text=resume.read_text(encoding="utf-8"),
                cover_text=cover.read_text(encoding="utf-8"),
                generated=True,
            )
    return out


def save_package(candidate_id: int, job_id: str, *, resume_text: str, cover_text: str) -> dict:
    match = match_repo.get_match(candidate_id, job_id)
    if not match:
        raise PackageError("Candidate/job match not found.")
    resume = _safe_package_path(match.get("resume_path"), candidate_id=candidate_id)
    cover = _safe_package_path(match.get("cover_path"), candidate_id=candidate_id)
    resume.write_text(resume_text, encoding="utf-8")
    cover.write_text(cover_text, encoding="utf-8")
    return get_package(candidate_id, job_id)
