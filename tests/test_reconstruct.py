from __future__ import annotations

from pathlib import Path

from jobagent.db import candidates as cand_repo
from jobagent.db import jobs as job_repo
from jobagent.db import matches as match_repo
from jobagent.reconstruct import load_legacy_yaml, reconstruct_candidates
from jobagent.ranking import heuristic_score, run_ranking, score_job


def test_load_legacy_yaml_drops_secrets(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        "profile:\n  name: Someone\n"
        "api:\n  mistral_key: SUPERSECRET\n"
        "notifications:\n  gmail:\n    app_password: also-secret\n",
        encoding="utf-8",
    )
    data = load_legacy_yaml(path)
    blob = str(data)
    assert "SUPERSECRET" not in blob
    assert "also-secret" not in blob
    assert "api" not in data
    assert data["profile"]["name"] == "Someone"


def test_reconstruct_does_not_merge_jeff_stub_with_jeffrey(db_path):
    cand_repo.insert_candidate_with_id(
        {
            "id": 1,
            "name": "Test User",
            "email": "t@t.com",
            "keywords_text": "python",
            "min_match_score": 65,
        },
        [{"title": "IT Manager"}],
        [],
    )
    cand_repo.insert_candidate_with_id(
        {
            "id": 2,
            "name": "Jeff",
            "email": "a@b.com",
            "min_match_score": 65,
        },
        [{"title": "IT"}],
        [],
    )
    report = reconstruct_candidates(report_dir=db_path.parent / "reports")
    stub = cand_repo.get_candidate(2)
    assert stub["name"] == "Jeff"
    assert stub["email"] == "a@b.com"
    assert int(stub["search_enabled"]) == 0
    jeffrey = cand_repo.find_candidate_by_email("khantroll@gmail.com")
    tami = cand_repo.find_candidate_by_email("tami.wood@fortsmithar.gov")
    assert jeffrey and jeffrey["id"] not in {1, 2}
    assert "IT Manager" in [t["title"] for t in jeffrey["titles"]]
    assert int(jeffrey["salary_min"]) == 80000
    assert int(jeffrey["search_enabled"]) == 1
    assert tami and tami["id"] not in {1, 2}
    assert int(tami["search_enabled"]) == 1
    assert not (tami.get("keywords_text") or "").strip()
    names = {c["name"] for c in report["candidates"]}
    assert "Jeff" in names and "Jeffrey Bowers" in names and "Tami Wood" in names
    assert any("Did not upsert Jeffrey Bowers onto candidates.id=2" in c for c in report["conflicts"])


def test_reconstructed_prefs_drive_independent_ranking(db_path, monkeypatch):
    a = cand_repo.save_candidate(
        {"name": "Jeffrey", "keywords_text": "VMware\nActive Directory", "search_enabled": 1},
        ["IT Manager"],
        [],
    )
    b = cand_repo.save_candidate(
        {"name": "Tami", "keywords_text": "", "search_enabled": 1},
        ["Telecommunications Manager"],
        [],
    )
    job_repo.upsert_job(
        {
            "title": "IT Manager",
            "company": "City",
            "url": "https://example.com/it-mgr",
            "description": "VMware Active Directory Windows Server",
            "source": "test",
        }
    )
    jid = job_repo.list_job_ids()[0]
    cfg_a = {"search": {"titles": ["IT Manager"], "keywords": ["VMware"]}, "profile": {"name": "Jeffrey"}}
    cfg_b = {"search": {"titles": ["Telecommunications Manager"], "keywords": []}, "profile": {"name": "Tami"}}
    monkeypatch.setitem(cfg_a, "llm", {"provider": "mock"})
    monkeypatch.setitem(cfg_b, "llm", {"provider": "mock"})
    run_ranking([job_repo.get_job(jid)], cfg_a, candidate_id=a)
    run_ranking([job_repo.get_job(jid)], cfg_b, candidate_id=b)
    match_a = match_repo.get_match(a, jid)
    match_b = match_repo.get_match(b, jid)
    assert match_a["score"] != match_b["score"] or match_a["score_reason"] != match_b["score_reason"]
    assert match_a["rank_provider"]
    assert match_b["ranked_at"]
    assert match_a["score"] == match_repo.get_match(a, jid)["score"]
    # B unchanged by extra A ranking
    run_ranking([job_repo.get_job(jid)], cfg_a, candidate_id=a)
    assert match_repo.get_match(b, jid)["score"] == match_b["score"]


def test_heuristic_and_mock_need_no_credentials(db_path):
    job = {"title": "IT Manager", "company": "X", "description": "VMware infrastructure"}
    cfg = {
        "llm": {"provider": "anthropic"},
        "api": {},
        "search": {"titles": ["IT Manager"], "keywords": ["VMware"]},
        "profile": {"name": "A"},
    }
    result = score_job(job, cfg)
    assert result.provider == "fallback"
    assert 0 <= result.score <= 100
    mock_cfg = dict(cfg)
    mock_cfg["llm"] = {"provider": "mock"}
    mocked = score_job(job, mock_cfg)
    assert mocked.provider == "mock"
    again = heuristic_score(job, cfg)
    assert again.provider == "fallback"
    assert "SUPERSECRET" not in again.reason
