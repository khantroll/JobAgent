"""Commute radius: saved limits, blank locations, geocoding, and board distance units."""
from __future__ import annotations

import requests

from jobagent.commute import (
    classify_job,
    clear_geocode_cache,
    detect_work_type,
    geocode,
    prepare_geocode_query,
)
from jobagent.config import candidate_runtime_config
from jobagent.sources._common import location_acceptable
from jobagent.sources.adzuna import crawl as crawl_adzuna
from jobagent.sources.adzuna import distance_km_from_miles


def _home_config(**search):
    base = {
        "profile": {"location": "Fort Smith, AR"},
        "search": {
            "filter_by_location": True,
            "location_accept_remote": True,
            "commute_auto_apply_minutes": 30,
            "commute_review_minutes": 90,
        },
        "sources": {},
    }
    base["search"].update(search)
    return base


def test_incidental_remote_language_does_not_skip_the_radius(monkeypatch):
    monkeypatch.setattr(
        "jobagent.commute.resolve_drive_minutes",
        lambda *args, **kwargs: (120, False),
    )
    job = {
        "title": "System Administrator",
        "location": "Dallas, TX",
        "description": "Provide remote desktop support. This role is not a remote position.",
    }
    assert detect_work_type(job) == "onsite"
    result = classify_job(job, _home_config())
    assert result["work_type"] == "onsite"
    assert result["action"] == "skip"
    assert result["commute_minutes"] == 120


def test_explicit_remote_label_still_skips_commute():
    job = {"title": "SRE", "location": "Remote, USA", "description": "Distributed team"}
    assert detect_work_type(job) == "remote"
    result = classify_job(job, _home_config())
    assert result["action"] == "auto_apply"
    assert result["commute_minutes"] is None


def test_remote_opt_out_is_read():
    job = {"title": "SRE", "location": "Remote", "description": "Fully remote"}
    result = classify_job(job, _home_config(location_accept_remote=False))
    assert result["action"] == "skip"
    assert "not accepting remote" in result["commute_note"]


def test_blank_location_is_outside_the_radius():
    job = {"title": "Sysadmin", "location": "", "description": "On site campus role"}
    result = classify_job(job, _home_config())
    assert result["action"] == "skip"
    assert result["commute_minutes"] is None
    kept = classify_job(job, _home_config(filter_by_location=False))
    assert kept["action"] == "needs_review"


def test_saved_review_minutes_exclude_a_job_inside_the_old_default(monkeypatch):
    monkeypatch.setattr(
        "jobagent.commute.resolve_drive_minutes",
        lambda *args, **kwargs: (40, False),
    )
    job = {"title": "Sysadmin", "location": "Van Buren, AR", "description": "On site"}
    # 40 minutes is inside the historical 90-minute default and outside a saved 30.
    skipped = classify_job(job, _home_config(commute_review_minutes=30, commute_auto_apply_minutes=15))
    assert skipped["action"] == "skip"
    assert skipped["commute_minutes"] == 40
    reviewed = classify_job(job, _home_config(commute_review_minutes=90, commute_auto_apply_minutes=15))
    assert reviewed["action"] == "needs_review"


def test_straight_line_fallback_skips_a_far_job_when_routing_fails(monkeypatch):
    def _coords(location, config):
        if "fort smith" in location.lower():
            return (35.3859, -94.3985)
        return (32.7767, -96.7970)

    monkeypatch.setattr("jobagent.commute.geocode", _coords)
    monkeypatch.setattr("jobagent.commute._get_drive_minutes_osrm", lambda *a, **k: None)
    result = classify_job(
        {
            "title": "Sysadmin",
            "location": "Dallas, TX",
            "description": "On site. Remote desktop support is required. Not a remote position.",
        },
        _home_config(),
    )
    assert result["work_type"] == "onsite"
    assert result["action"] == "skip"
    assert result["commute_minutes"] > 90
    assert "estimated" in result["commute_note"]


def test_country_suffix_is_geocodable_and_errors_are_not_cached(monkeypatch):
    assert prepare_geocode_query("Little Rock, AR, United States") == "Little Rock, AR"
    assert prepare_geocode_query("United States") is None
    assert prepare_geocode_query("Remote") is None

    clear_geocode_cache()
    monkeypatch.setattr("jobagent.commute._nominatim_throttle", lambda: None)
    calls = {"n": 0}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"lat": "34.7465", "lon": "-92.2896"}]

    def fake_get(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        assert "Little Rock" in (params or {}).get("q", "")
        if calls["n"] == 1:
            raise requests.ConnectionError("temporary")
        return _Response()

    monkeypatch.setattr("jobagent.commute.requests.get", fake_get)
    assert geocode("Little Rock, AR, United States", {}) is None
    coords = geocode("Little Rock, AR, United States", {})
    assert coords == (34.7465, -92.2896)
    assert calls["n"] == 2
    assert geocode("Remote", {}) is None
    assert calls["n"] == 2


def test_location_tokens_do_not_match_inside_other_words():
    cfg = _home_config()
    assert location_acceptable("Little Rock, AR", cfg, "usajobs") is True
    assert location_acceptable("Newark, NJ", cfg, "usajobs") is False
    assert location_acceptable("Charlotte, NC", cfg, "usajobs") is False
    assert location_acceptable("Maryland", cfg, "usajobs") is False
    assert location_acceptable("Poteau, OK", cfg, "usajobs") is False
    assert location_acceptable("Poteau, OK", cfg, "usajobs", radius_scoped=True) is True
    assert location_acceptable("", cfg, "usajobs", radius_scoped=True) is False
    assert location_acceptable("Remote", cfg, "usajobs") is True
    assert location_acceptable("Berlin, Germany", cfg, "usajobs", radius_scoped=True) is False
    assert location_acceptable("", {"search": {"filter_by_location": False}}, "usajobs") is True


def test_adzuna_distance_miles_are_sent_as_kilometres(monkeypatch):
    assert distance_km_from_miles(50) == 80
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _Response()

    monkeypatch.setattr("jobagent.sources.adzuna.requests.get", fake_get)
    crawl_adzuna(
        {
            "sources": {
                "adzuna": {
                    "enabled": True,
                    "distance_miles": 50,
                    "where": "Fort Smith, AR",
                    "max_pages": 1,
                }
            },
            "search": {"titles": ["Sysadmin"]},
            "api": {"adzuna_app_id": "app", "adzuna_app_key": "key"},
            "profile": {"location": "Fort Smith, AR"},
        }
    )
    assert captured["params"]["distance"] == 80
    assert captured["params"]["where"] == "Fort Smith, AR"


def test_candidate_commute_limit_is_what_classification_reads(db_path):
    from jobagent.db import candidates as cand_repo

    cid = cand_repo.save_candidate(
        {
            "name": "Ada",
            "location": "Fort Smith, AR",
            "commute_review_minutes": 30,
            "commute_auto_apply_minutes": 15,
            "location_accept_remote": 0,
        },
        ["Sysadmin"],
        [],
    )
    person = cand_repo.get_candidate(cid)
    cfg = candidate_runtime_config(person, {"search": {}, "scheduler": {"dry_run": True}})
    assert cfg["search"]["commute_review_minutes"] == 30
    assert cfg["search"]["location_accept_remote"] is False
    job = {"title": "Sysadmin", "location": "Remote", "description": "Fully remote"}
    assert classify_job(job, cfg)["action"] == "skip"
