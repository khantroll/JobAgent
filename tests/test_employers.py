"""Employer discovery: slug patterns, polite probes, and discovered-board merge."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import requests

import yaml
from fastapi.testclient import TestClient

from jobagent.cli import main
from jobagent.config import load_imap_settings, save_api_secrets, save_imap_settings
from jobagent.employers.boards import (
    enabled_boards,
    load_store,
    parse_company_names,
    read_employer_names,
    slugs_for,
    upsert_confirmed,
    workday_entries,
    write_employer_names,
)
from jobagent.employers.discover import Prober, collect_names, discover_employers, names_near_home
from jobagent.employers.robots import disallow_rules, path_allowed
from jobagent.employers.slugs import all_pattern_ids, candidate_probes, response_is_board, slug_variants
from jobagent.web.app import app


RICH_NAME = "Summit Ridge Holdings"


class _Response:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class _Session:
    def __init__(self, responder):
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[object] = []
        self.responder = responder

    def get(self, url, timeout=20, headers=None):
        self.calls.append(("GET", url))
        return self.responder("GET", url)

    def post(self, url, json=None, data=None, timeout=20, headers=None):
        self.calls.append(("POST", url))
        self.bodies.append(json if json is not None else data)
        return self.responder("POST", url)


def test_rich_name_emits_every_pattern():
    probes = candidate_probes(RICH_NAME)
    patterns = [item.pattern for item in probes]
    assert len(all_pattern_ids()) == 28
    assert len(probes) == 28
    assert patterns == all_pattern_ids()
    assert {item.method for item in probes if item.ats != "workday"} == {"GET"}
    assert {item.method for item in probes if item.ats == "workday"} == {"POST"}
    hosts = {item.url.split("/")[2] for item in probes if item.ats == "workday"}
    assert hosts == {
        "summitridge.wd1.myworkdayjobs.com",
        "summitridge.wd5.myworkdayjobs.com",
        "summitridge.wd3.myworkdayjobs.com",
    }
    sites = {item.site for item in probes if item.ats == "workday"}
    assert sites == {"summitridge", "External", "Careers", "SummitRidge"}


def test_suffixes_and_csv_names():
    assert parse_company_names('# skip\n\n"Beta, Inc",note\nAcme\nacme\n') == ["Beta, Inc", "Acme"]
    slugs = [slug for _pattern, slug in slug_variants("The Beta, Inc")]
    assert slugs[0] == "beta"


def test_board_json_shapes():
    assert response_is_board("greenhouse", {"jobs": []})
    assert response_is_board("lever", [])
    assert response_is_board("ashby", {"jobs": [{"title": "A"}]})
    assert response_is_board("smartrecruiters", {"content": []})
    assert response_is_board("workday", {"total": 0, "jobPostings": []})
    assert not response_is_board("greenhouse", {"jobs": {}})
    assert not response_is_board("lever", {"data": []})
    assert not response_is_board("workday", {"ok": True})


def _routes(method: str, url: str) -> _Response:
    if url.endswith("/robots.txt"):
        if "boards-api.greenhouse.io" in url:
            return _Response(200, text="User-agent: *\nDisallow:\n\nUser-agent: JobAgent\nDisallow: /v1/\n")
        return _Response(404, text="missing")
    if "boards-api.greenhouse.io" in url:
        return _Response(200, {"jobs": []})
    if "api.lever.co" in url and "/acmewidgets" in url:
        return _Response(200, [{"text": "Role", "hostedUrl": "https://jobs.lever.co/acmewidgets/1"}])
    if "api.ashbyhq.com" in url and "/acmewidgets" in url:
        return _Response(200, {"jobs": []})
    if "api.smartrecruiters.com" in url and "/acmewidgets" in url:
        return _Response(200, {"content": []})
    if "myworkdayjobs.com" in url and method == "POST" and "acmewidgets" in url:
        return _Response(200, {"total": 1, "jobPostings": []})
    return _Response(404, text="no")


def test_robots_disallow_skips_the_board_request():
    session = _Session(_routes)
    prober = Prober(session, delay=0, cache={"entries": {}})
    item = next(probe for probe in candidate_probes("Acme Widgets Inc") if probe.ats == "greenhouse")
    result = prober.probe(item)
    assert result["ok"] is False
    assert result["reason"] == "robots"
    assert not any(call[1] == item.url for call in session.calls)
    assert any(call[1].endswith("/robots.txt") for call in session.calls)
    rules = disallow_rules("User-agent: *\nDisallow: /secret\n")
    assert path_allowed(rules, "/v1/boards/acme/jobs")
    assert not path_allowed(rules, "/secret/jobs")


def test_negative_cache_skips_a_second_request(tmp_path):
    session = _Session(lambda method, url: _Response(404, text="missing"))
    cache_file = tmp_path / "cache.json"
    prober = Prober(session, delay=0, cache_file=cache_file)
    item = candidate_probes("Acme Widgets Inc")[0]
    first = prober.probe(item)
    calls = len(session.calls)
    second = prober.probe(item)
    assert first["ok"] is False
    assert second == first
    assert len(session.calls) == calls
    stored = json.loads(cache_file.read_text(encoding="utf-8"))
    blob = json.dumps(stored)
    assert "missing" not in blob
    assert item.url in blob

    stale = stored["entries"][f"GET {item.url}"]
    stale["at"] = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    cache_file.write_text(json.dumps(stored), encoding="utf-8")
    again = Prober(session, delay=0, cache_file=cache_file)
    again.probe(item)
    assert len(session.calls) > calls


def test_discover_keeps_first_valid_board_and_unmatched(tmp_path):
    session = _Session(_routes)
    boards = tmp_path / "boards.yaml"
    summary = discover_employers(
        ["Acme Widgets Inc", "Nope Consulting LLC"],
        delay=0,
        session=session,
        boards_file=boards,
        cache_file=tmp_path / "cache.json",
    )
    assert summary["patterns"] == 28
    assert len(summary["confirmed"]) == 1
    assert summary["confirmed"][0]["ats"] == "lever"
    assert summary["confirmed"][0]["slug"] == "acmewidgets"
    assert summary["unmatched"] == ["Nope Consulting LLC"]
    store = load_store(boards)
    assert store["boards"][0]["enabled"] is True
    assert store["unmatched"][0]["name"] == "Nope Consulting LLC"
    posted = [body for body in session.bodies if isinstance(body, dict)]
    assert not posted or posted[0]["limit"] == 1


def test_rediscovery_does_not_reenable_a_board(tmp_path):
    boards = tmp_path / "boards.yaml"
    session = _Session(_routes)
    discover_employers(
        ["Acme Widgets Inc"],
        delay=0,
        session=session,
        boards_file=boards,
        cache_file=tmp_path / "cache.json",
    )
    store = load_store(boards)
    store["boards"][0]["enabled"] = False
    from jobagent.employers.boards import save_store

    save_store(store, boards)
    discover_employers(
        ["Acme Widgets Inc"],
        delay=0,
        session=_Session(_routes),
        boards_file=boards,
        cache_file=tmp_path / "cache-2.json",
    )
    assert load_store(boards)["boards"][0]["enabled"] is False


def test_crawlers_merge_enabled_discovered_boards(db_path, monkeypatch):
    upsert_confirmed(
        {
            "id": "greenhouse:foundco",
            "ats": "greenhouse",
            "name": "Found Co",
            "pattern": "greenhouse:compact",
            "url": "https://boards-api.greenhouse.io/v1/boards/foundco/jobs",
            "slug": "foundco",
            "enabled": True,
        }
    )
    upsert_confirmed(
        {
            "id": "lever:offco",
            "ats": "lever",
            "name": "Off Co",
            "pattern": "lever:compact",
            "url": "https://api.lever.co/v0/postings/offco",
            "slug": "offco",
            "enabled": False,
        }
    )
    upsert_confirmed(
        {
            "id": "workday:northwind:wd5:External",
            "ats": "workday",
            "name": "Northwind",
            "pattern": "workday:wd5:External",
            "url": "https://northwind.wd5.myworkdayjobs.com/wday/cxs/northwind/External/jobs",
            "tenant": "northwind",
            "cluster": "wd5",
            "site": "External",
            "enabled": True,
        }
    )
    assert slugs_for("greenhouse", ["manual"]) == ["manual", "foundco"]
    assert slugs_for("lever", []) == []
    assert enabled_boards("lever") == []
    entries = workday_entries([{"tenant": "manual", "site": "Careers", "cluster": "wd1"}])
    assert {(row["tenant"], row["site"]) for row in entries} == {("manual", "Careers"), ("northwind", "External")}

    from jobagent.sources import greenhouse

    seen: list[str] = []

    def fake_get(url, headers=None, timeout=30):
        seen.append(url)
        return _Response(
            200,
            {
                "jobs": [
                    {
                        "title": "Analyst",
                        "absolute_url": "https://boards.greenhouse.io/foundco/jobs/1",
                        "content": "<p>Work</p>",
                        "offices": [{"name": "Remote"}],
                    }
                ]
            },
        )

    monkeypatch.setattr(greenhouse.requests, "get", fake_get)
    added = greenhouse.crawl(
        {"sources": {"greenhouse": {"enabled": True, "companies": [], "filter_by_titles": False}}, "search": {}}
    )
    assert added == 1
    assert seen == ["https://boards-api.greenhouse.io/v1/boards/foundco/jobs?content=true"]


def test_catalog_and_near_home_sources(db_path, monkeypatch):
    from jobagent.db import jobs as job_repo

    job_repo.upsert_job(
        {
            "title": "Admin",
            "company": "Catalog Electric",
            "location": "Fort Smith, AR",
            "url": "https://example.test/catalog",
            "source": "test",
        }
    )
    write_employer_names("File Manufacturing\n")
    names = collect_names(include_catalog=True, near_home=False)
    assert names[0] == "File Manufacturing"
    assert "Catalog Electric" in names

    def geocode(location, config):
        assert location == "Fort Smith, AR"
        return (35.38, -94.42)

    monkeypatch.setattr("jobagent.commute.geocode", geocode)
    session = _Session(
        lambda method, url: _Response(
            200,
            {"elements": [{"tags": {"name": "River Office"}}, {"tags": {"name": "A"}}]},
        )
    )
    found = names_near_home({"search": {"location": "Fort Smith, AR"}}, session, limit=25)
    assert found == ["River Office"]
    assert len(session.calls) == 1
    assert session.calls[0][0] == "POST"
    assert "overpass-api.de" in session.calls[0][1]


def test_cli_discover_employers_prints_summary_without_network(db_path, capsys):
    write_employer_names("")
    code = main(["discover-employers", "--no-include-catalog", "--delay", "0", "--limit", "5"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["names"] == 0
    assert payload["patterns"] == 28
    assert payload["confirmed"] == []


def test_settings_company_list_and_board_toggle(db_path, tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    path.write_text("scheduler:\n  dry_run: true\n  auto_apply: false\nsearch: {}\nsources: {}\n", encoding="utf-8")
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(path))
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "")
    upsert_confirmed(
        {
            "id": "ashby:river",
            "ats": "ashby",
            "name": "River",
            "pattern": "ashby:compact",
            "url": "https://api.ashbyhq.com/posting-api/job-board/river",
            "slug": "river",
            "enabled": True,
        }
    )
    from jobagent.employers.boards import record_unmatched

    record_unmatched("Missing Name")
    with TestClient(app) as client:
        page = client.get("/settings")
        assert page.status_code == 200
        assert "River" in page.text
        assert "Missing Name" in page.text
        assert 'id="dry-run"' in page.text
        saved = client.post(
            "/settings/employers",
            data={
                "employer_names": "Northwind Traders\n# note\n",
                "board_id": ["ashby:river"],
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
        again = client.get("/settings")
        assert 'id="dry-run"' in again.text
        assert "checked" in again.text.split('id="dry-run"', 1)[1][:80]
    assert "Northwind Traders" in read_employer_names()
    assert load_store()["boards"][0]["enabled"] is False
    assert "auto_apply: false" in path.read_text(encoding="utf-8")


def test_save_api_secrets_keeps_imap_block():
    save_imap_settings({"host": "imap.example.test", "username": "alerts@example.test", "password": "mailbox-secret-ZZ99"})
    save_api_secrets({"adzuna_app_id": "app-id-value"})
    imap = load_imap_settings()
    assert imap["password"] == "mailbox-secret-ZZ99"
    assert imap["host"] == "imap.example.test"
    raw = yaml.safe_load(open(os.environ["JOBAGENT_SECRETS_PATH"], encoding="utf-8"))
    assert raw["api"]["adzuna_app_id"] == "app-id-value"
    assert raw["imap"]["password"] == "mailbox-secret-ZZ99"
