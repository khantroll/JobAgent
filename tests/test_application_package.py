from __future__ import annotations

from pathlib import Path

import pytest

from jobagent import application_package as packages
from jobagent.db import candidates as cand_repo
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo


def _fixture_match():
    cid = cand_repo.save_candidate(
        {"name": "Candidate", "resume_text": "Real experience", "search_enabled": 1},
        ["Systems Administrator"],
        [],
    )
    job_repo.upsert_job(
        {"title": "Systems Administrator", "company": "Example", "url": "https://example.test/job",
         "description": "Infrastructure role", "source": "test"}
    )
    jid = job_repo.list_job_ids()[0]
    match_repo.link_job_to_candidate(jid, cid)
    match_repo.update_match_status(cid, jid, "reviewed")
    return cid, jid


def test_generate_and_edit_package_is_candidate_scoped(db_path, tmp_path, monkeypatch):
    cid, jid = _fixture_match()
    monkeypatch.setenv("JOBAGENT_OUTPUT_DIR", str(tmp_path / "output"))

    def fake_generate(job, config, *, candidate_id):
        base = packages.output_dir()
        resume = base / "resumes" / str(candidate_id) / f"{jid}.txt"
        cover = base / "cover_letters" / str(candidate_id) / f"{jid}.txt"
        resume.parent.mkdir(parents=True, exist_ok=True)
        cover.parent.mkdir(parents=True, exist_ok=True)
        resume.write_text("draft resume", encoding="utf-8")
        cover.write_text("draft cover", encoding="utf-8")
        return str(resume), str(cover)

    monkeypatch.setattr(packages, "generate_docs", fake_generate)
    result = packages.generate_package(cid, jid)
    assert result["generated"]
    assert result["resume_text"] == "draft resume"
    saved = packages.save_package(cid, jid, resume_text="reviewed resume", cover_text="reviewed cover")
    assert saved["resume_text"] == "reviewed resume"
    assert saved["cover_text"] == "reviewed cover"


def test_rejected_match_requires_approval_before_generation(db_path, monkeypatch):
    cid, jid = _fixture_match()
    match_repo.update_match_status(cid, jid, "rejected")
    with pytest.raises(packages.PackageError, match="reviewed"):
        packages.generate_package(cid, jid)

def test_package_rejects_cross_candidate_document_path(db_path, tmp_path, monkeypatch):
    cid, jid = _fixture_match()
    other = cand_repo.save_candidate(
        {"name": "Other", "resume_text": "Other experience", "search_enabled": 1},
        ["Engineer"],
        [],
    )
    monkeypatch.setenv("JOBAGENT_OUTPUT_DIR", str(tmp_path / "output"))
    alien = packages.output_dir() / "resumes" / str(other) / "alien.txt"
    alien.parent.mkdir(parents=True, exist_ok=True)
    alien.write_text("not yours", encoding="utf-8")
    match_repo.update_match_documents(cid, jid, str(alien), str(alien))
    with pytest.raises(packages.PackageError, match="outside"):
        packages.get_package(cid, jid)


def test_package_document_returns_reviewed_text(db_path, tmp_path, monkeypatch):
    cid, jid = _fixture_match()
    monkeypatch.setenv("JOBAGENT_OUTPUT_DIR", str(tmp_path / "output"))
    resume = packages.output_dir() / "resumes" / str(cid) / "resume.txt"
    cover = packages.output_dir() / "cover_letters" / str(cid) / "cover.txt"
    resume.parent.mkdir(parents=True, exist_ok=True)
    cover.parent.mkdir(parents=True, exist_ok=True)
    resume.write_text("reviewed resume", encoding="utf-8")
    cover.write_text("reviewed cover", encoding="utf-8")
    match_repo.update_match_documents(cid, jid, str(resume), str(cover))

    resume_path, resume_text = packages.package_document(cid, jid, "resume")
    cover_path, cover_text = packages.package_document(cid, jid, "cover-letter")
    assert resume_path == resume.resolve()
    assert cover_path == cover.resolve()
    assert resume_text == "reviewed resume"
    assert cover_text == "reviewed cover"


def test_package_document_rejects_unknown_kind(db_path, tmp_path, monkeypatch):
    cid, jid = _fixture_match()
    monkeypatch.setenv("JOBAGENT_OUTPUT_DIR", str(tmp_path / "output"))
    resume = packages.output_dir() / "resumes" / str(cid) / "resume.txt"
    cover = packages.output_dir() / "cover_letters" / str(cid) / "cover.txt"
    resume.parent.mkdir(parents=True, exist_ok=True)
    cover.parent.mkdir(parents=True, exist_ok=True)
    resume.write_text("resume", encoding="utf-8")
    cover.write_text("cover", encoding="utf-8")
    match_repo.update_match_documents(cid, jid, str(resume), str(cover))
    with pytest.raises(packages.PackageError, match="Unknown"):
        packages.package_document(cid, jid, "other")


def test_submitted_package_snapshot_is_immutable_copy(db_path, tmp_path, monkeypatch):
    cid, jid = _fixture_match()
    monkeypatch.setenv("JOBAGENT_OUTPUT_DIR", str(tmp_path / "output"))
    resume = packages.output_dir() / "resumes" / str(cid) / "resume.txt"
    cover = packages.output_dir() / "cover_letters" / str(cid) / "cover.txt"
    resume.parent.mkdir(parents=True, exist_ok=True)
    cover.parent.mkdir(parents=True, exist_ok=True)
    resume.write_text("submitted resume", encoding="utf-8")
    cover.write_text("submitted cover", encoding="utf-8")
    match_repo.update_match_documents(cid, jid, str(resume), str(cover))
    snapshot = packages.snapshot_submitted_package(cid, jid)
    resume.write_text("later edit", encoding="utf-8")
    assert Path(snapshot["resume_path"]).read_text(encoding="utf-8") == "submitted resume"
    row = match_repo.get_match(cid, jid)
    assert row["submitted_resume_path"] == snapshot["resume_path"]
