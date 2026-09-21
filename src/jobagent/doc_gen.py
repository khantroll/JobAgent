"""Document generation helper — tailors resume and cover letter per candidate/job.

Alpha.1 does not call this from auto-apply. Paths are per-candidate so two people
never share or overwrite generated documents for the same listing.
"""
from __future__ import annotations

import logging

from jobagent.llm import complete
from jobagent.paths import output_dir

logger = logging.getLogger(__name__)


def _ensure_output_dirs(candidate_id: int):
    base = output_dir()
    resume_dir = base / "resumes" / str(candidate_id)
    cover_dir = base / "cover_letters" / str(candidate_id)
    resume_dir.mkdir(parents=True, exist_ok=True)
    cover_dir.mkdir(parents=True, exist_ok=True)
    return resume_dir, cover_dir


RESUME_SYSTEM = """You are an expert resume writer. Rewrite the candidate's resume to best match
the job description. Keep all facts accurate — do not invent experience. Mirror the
job's language and emphasize the most relevant skills and achievements. Output plain
text formatted as a clean resume, no JSON, no markdown fences."""

COVER_SYSTEM = """You are an expert cover letter writer. Write a concise, compelling cover letter
(3 short paragraphs). Sound like a real human, not a template. No "Dear Hiring Manager"
cliches. Reference specific things from the job description. Output plain text only."""


def generate_docs(job: dict, config: dict, *, candidate_id: int) -> tuple:
    """Returns (resume_path, cover_path) as strings, namespaced by candidate."""
    from jobagent.db.matches import catalog_job_id

    resume_dir, cover_dir = _ensure_output_dirs(candidate_id)
    profile = config["profile"]
    job_id = catalog_job_id(job)

    resume_text = complete(
        config,
        system=RESUME_SYSTEM,
        user=f"""
JOB: {job['title']} at {job['company']}
DESCRIPTION: {str(job.get('description', ''))[:3000]}

ORIGINAL RESUME:
{config['resume_text']}

Rewrite the resume to match this job. Keep it to one page worth of text.
""".strip(),
        max_tokens=1500,
    )

    cover_text = complete(
        config,
        system=COVER_SYSTEM,
        user=f"""
Candidate: {profile['name']}
Applying for: {job['title']} at {job['company']}
Job description: {str(job.get('description', ''))[:2000]}
Resume: {config['resume_text'][:1000]}

Write the cover letter.
""".strip(),
        max_tokens=800,
    )

    resume_path = resume_dir / f"{job_id}.txt"
    cover_path = cover_dir / f"{job_id}.txt"
    resume_path.write_text(resume_text)
    cover_path.write_text(cover_text)

    logger.info(f"Generated docs for {job['title']} @ {job['company']}")
    return str(resume_path), str(cover_path)
