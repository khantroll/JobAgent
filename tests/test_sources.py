from __future__ import annotations

from jobagent.sources.normalize import (
    from_adzuna,
    from_greenhouse,
    from_higheredjobs_rss,
    from_jsearch,
    from_lever,
    from_remotive,
    from_rss,
    from_themuse,
    from_usajobs,
    from_workday_posting,
    parse_higheredjobs_company_location,
)


def test_adzuna_mapping():
    mapped = from_adzuna(
        {
            "title": "IT Manager",
            "company": {"display_name": "Acme"},
            "location": {"display_name": "Fort Smith, AR"},
            "redirect_url": "https://example.com/adzuna/1",
            "description": "VMware",
            "salary_min": 90000,
            "salary_max": 120000,
            "created": "2026-08-01",
        }
    )
    assert mapped["source"] == "adzuna"
    assert mapped["company"] == "Acme"
    assert mapped["salary_raw"].startswith("$")
    assert mapped["url"] == "https://example.com/adzuna/1"


def test_jsearch_remote_prefix():
    mapped = from_jsearch(
        {
            "job_title": "SRE",
            "employer_name": "Globex",
            "job_city": "Austin",
            "job_state": "TX",
            "job_country": "US",
            "job_is_remote": True,
            "job_apply_link": "https://example.com/js/1",
            "job_description": "Linux",
        }
    )
    assert mapped["location"].startswith("Remote")
    assert mapped["source"] == "jsearch"


def test_remotive_strips_html():
    mapped = from_remotive(
        {
            "title": "DevOps",
            "company_name": "RemoteCo",
            "url": "https://example.com/remotive/1",
            "description": "<p>Hello</p>",
            "candidate_required_location": "USA",
        }
    )
    assert "<p>" not in mapped["description"]
    assert mapped["source"] == "remotive"


def test_greenhouse_and_lever():
    gh = from_greenhouse(
        {"title": "Eng", "absolute_url": "https://boards.greenhouse.io/x/jobs/1", "offices": [{"name": "NYC"}]},
        "sumologic",
    )
    assert gh["company"] == "Sumologic"
    assert gh["location"] == "NYC"
    lever = from_lever(
        {"text": "SRE", "hostedUrl": "https://jobs.lever.co/x/1", "categories": {"location": "Remote"}},
        "wachter",
    )
    assert lever["source"] == "lever"
    assert lever["location"] == "Remote"


def test_usajobs_and_themuse():
    usa = from_usajobs(
        {
            "MatchedObjectDescriptor": {
                "PositionTitle": "IT Specialist",
                "OrganizationName": "VA",
                "PositionURI": "https://www.usajobs.gov/job/1",
                "PositionLocation": [{"LocationName": "Fort Smith, AR"}],
                "UserArea": {"Details": {"Remuneration": {"MinimumRange": "80000", "MaximumRange": "110000"}}},
            }
        }
    )
    assert usa["source"] == "usajobs"
    assert "80000" in usa["salary_raw"]
    muse = from_themuse(
        {
            "name": "Platform Engineer",
            "company": {"name": "MuseCo"},
            "refs": {"landing_page": "https://www.themuse.com/jobs/1"},
            "locations": [{"name": "Remote"}],
            "contents": "<b>Hi</b>",
        }
    )
    assert muse["company"] == "MuseCo"
    assert "<b>" not in muse["description"]


def test_rss_and_higheredjobs():
    rss = from_rss(title="SRE at Datadog", url="https://weworkremotely.com/jobs/1", description="Remote")
    assert rss["company"] == "Datadog"
    assert rss["source"] == "rss"
    company, loc = parse_higheredjobs_company_location("State University (Little Rock, AR)")
    assert company == "State University"
    assert loc == "Little Rock, AR"
    hej = from_higheredjobs_rss(
        title="IT Manager",
        url="https://www.higheredjobs.com/x",
        description="State University (Little Rock, AR)",
    )
    assert hej["company"] == "State University"


def test_workday_posting_and_reject_empty():
    mapped = from_workday_posting(
        {"title": "Sysadmin", "locationsText": "Fort Smith, AR", "postedOn": "Posted 2 Days Ago"},
        company="ArcBest",
        url="https://arcbest.wd1.myworkdayjobs.com/en-US/ArcBest/job/1",
    )
    assert mapped["source"] == "workday"
    assert from_adzuna({"title": "", "redirect_url": "https://x"}) is None
    assert from_rss(title="Hi", url="") is None


def test_source_skip_reason_without_credentials():
    from jobagent.sources import source_skip_reason

    cfg = {"sources": {"adzuna": {"enabled": True}, "greenhouse": {"enabled": True, "companies": []}}, "api": {}, "search": {}}
    assert "ADZUNA" in (source_skip_reason("adzuna", cfg) or "")
    assert source_skip_reason("greenhouse", cfg)
    assert source_skip_reason("rss", cfg)
