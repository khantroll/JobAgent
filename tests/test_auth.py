from __future__ import annotations

from fastapi.testclient import TestClient

from jobagent.web.auth import COOKIE_NAME, is_public_ui_path
from jobagent.web.app import app


def test_public_ui_paths():
    assert is_public_ui_path("/login") is True
    assert is_public_ui_path("/health") is True
    assert is_public_ui_path("/static/style.css") is True
    assert is_public_ui_path("/") is False
    assert is_public_ui_path("/candidates") is False


def test_unprotected_mode_opens_ui_and_api(db_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "")
    with TestClient(app) as client:
        home = client.get("/", follow_redirects=False)
        assert home.status_code == 200
        assert "Dashboard" in home.text
        api = client.get("/api/jobs")
        assert api.status_code == 200
        assert "jobs" in api.json()
        health = client.get("/health")
        assert health.status_code == 200
        login = client.get("/login", follow_redirects=False)
        assert login.status_code in (302, 303)


def test_protected_mode_blocks_ui_and_api_until_authenticated(db_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "alpha-secret-token")
    with TestClient(app) as client:
        home = client.get("/", follow_redirects=False)
        assert home.status_code in (302, 303)
        assert "/login" in home.headers.get("location", "")

        people = client.get("/candidates", follow_redirects=False)
        assert people.status_code in (302, 303)

        health = client.get("/health")
        assert health.status_code == 200

        login = client.get("/login")
        assert login.status_code == 200
        assert "Sign in" in login.text

        denied = client.get("/api/jobs")
        assert denied.status_code == 401

        wrong = client.get(
            "/api/jobs",
            headers={"Authorization": "Bearer not-the-token"},
        )
        assert wrong.status_code == 401

        ok = client.get(
            "/api/jobs",
            headers={"Authorization": "Bearer alpha-secret-token"},
        )
        assert ok.status_code == 200

        bad_login = client.post("/login", data={"token": "nope"}, follow_redirects=False)
        assert bad_login.status_code in (302, 303)
        assert "error=invalid" in bad_login.headers.get("location", "")

        good_login = client.post(
            "/login",
            data={"token": "alpha-secret-token"},
            follow_redirects=False,
        )
        assert good_login.status_code in (302, 303)
        assert COOKIE_NAME in good_login.cookies

        dashboard = client.get("/", follow_redirects=False)
        assert dashboard.status_code == 200
        assert "Dashboard" in dashboard.text
