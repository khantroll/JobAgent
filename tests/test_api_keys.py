"""API keys persist in a gitignored file and stay out of logs and HTML."""
from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from jobagent.config import (
    credential_usable,
    ensure_secrets_file,
    load_settings,
    mask_secret,
    save_scheduler_settings,
)
from jobagent.sources import _execute_source, source_status_rows
from jobagent.web.app import app


def _clear_key_env(monkeypatch) -> None:
    for name in (
        "ADZUNA_APP_ID",
        "ADZUNA_APP_KEY",
        "RAPIDAPI_KEY",
        "USAJOBS_API_KEY",
        "USAJOBS_USER_AGENT",
        "THEMUSE_API_KEY",
        "GOOGLE_MAPS_KEY",
        "ANTHROPIC_API_KEY",
        "MISTRAL_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_placeholder_and_mask():
    assert credential_usable("YOUR_ADZUNA_APP_ID") is False
    assert credential_usable("  ") is False
    assert mask_secret("adzuna-live-secret-ZZ9Q") == "••••ZZ9Q"
    assert "adzuna-live-secret" not in mask_secret("adzuna-live-secret-ZZ9Q")


def test_env_overrides_secrets_and_secrets_override_settings(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "api:\n  adzuna_app_id: from-settings\n  adzuna_app_key: from-settings-key\n",
        encoding="utf-8",
    )
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text("api:\n  adzuna_app_id: from-secrets\n", encoding="utf-8")
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("JOBAGENT_SECRETS_PATH", str(secrets))
    monkeypatch.setenv("ADZUNA_APP_KEY", "from-env-key")
    cfg = load_settings()
    assert cfg["api"]["adzuna_app_id"] == "from-secrets"
    assert cfg["api"]["adzuna_app_key"] == "from-env-key"


def test_your_placeholder_does_not_wipe_a_saved_key(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    settings = tmp_path / "settings.yaml"
    settings.write_text("api:\n  adzuna_app_id: YOUR_ADZUNA_APP_ID\n", encoding="utf-8")
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text("api:\n  adzuna_app_id: real-adzuna-id\n", encoding="utf-8")
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("JOBAGENT_SECRETS_PATH", str(secrets))
    monkeypatch.setenv("ADZUNA_APP_ID", "YOUR_ADZUNA_APP_ID")
    cfg = load_settings()
    assert cfg["api"]["adzuna_app_id"] == "real-adzuna-id"


def test_blank_process_env_does_not_hide_dotenv(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setattr("jobagent.config.project_root", lambda: tmp_path)
    monkeypatch.setenv("JOBAGENT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(tmp_path / "missing.yaml"))
    (tmp_path / ".env").write_text("ADZUNA_APP_ID=from-dotenv-file\n", encoding="utf-8")
    monkeypatch.setenv("ADZUNA_APP_ID", "   ")
    cfg = load_settings()
    assert cfg["api"]["adzuna_app_id"] == "from-dotenv-file"


def test_profile_yaml_is_a_fallback_and_survives_a_settings_replace(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "profile.yaml").write_text(
        "api:\n  rapidapi_key: from-profile-key\n",
        encoding="utf-8",
    )
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "api:\n  adzuna_app_id: from-settings-id\nscheduler:\n  dry_run: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOBAGENT_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(settings))
    monkeypatch.setattr("jobagent.config.project_root", lambda: tmp_path)
    ensure_secrets_file()
    (cfg_dir / "profile.yaml").unlink()
    settings.write_text("scheduler:\n  dry_run: false\nsearch: {}\nsources: {}\n", encoding="utf-8")
    cfg = load_settings()
    assert cfg["api"]["rapidapi_key"] == "from-profile-key"
    assert cfg["api"]["adzuna_app_id"] == "from-settings-id"
    text = settings.read_text(encoding="utf-8")
    assert "from-profile-key" not in text
    assert "from-settings-id" not in text
    save_scheduler_settings(dry_run=False, auto_apply=False)
    assert "from-settings-id" not in settings.read_text(encoding="utf-8")
    again = load_settings()
    assert again["api"]["adzuna_app_id"] == "from-settings-id"


def test_ensure_secrets_does_not_overwrite_a_saved_key(tmp_path, monkeypatch):
    _clear_key_env(monkeypatch)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "profile.yaml").write_text(
        "api:\n  rapidapi_key: from-profile\n",
        encoding="utf-8",
    )
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text("api:\n  rapidapi_key: already-saved\n", encoding="utf-8")
    monkeypatch.setenv("JOBAGENT_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("JOBAGENT_SECRETS_PATH", str(secrets))
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(tmp_path / "missing.yaml"))
    ensure_secrets_file()
    assert "already-saved" in secrets.read_text(encoding="utf-8")
    assert "from-profile" not in secrets.read_text(encoding="utf-8")


def test_settings_page_masks_keys_and_blank_field_keeps_them(db_path, tmp_path, monkeypatch, caplog):
    _clear_key_env(monkeypatch)
    secret = "adzuna-live-secret-ZZ9Q"
    new_id = "new-id-value-QQ11"
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "scheduler:\n  dry_run: true\n  auto_apply: false\n"
        "search: {}\n"
        "sources:\n  adzuna:\n    enabled: true\n  jsearch:\n    enabled: true\n",
        encoding="utf-8",
    )
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text(f"api:\n  adzuna_app_key: {secret}\n", encoding="utf-8")
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("JOBAGENT_SECRETS_PATH", str(secrets))
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "")
    with caplog.at_level(logging.DEBUG):
        with TestClient(app) as client:
            page = client.get("/settings")
            assert page.status_code == 200
            assert secret not in page.text
            assert "••••ZZ9Q" in page.text
            assert "missing key" in page.text
            saved = client.post(
                "/settings",
                data={
                    "dry_run": "1",
                    "auto_apply": "0",
                    "key_adzuna_app_id": new_id,
                },
                follow_redirects=True,
            )
            assert saved.status_code == 200
            assert secret not in saved.text
            assert new_id not in saved.text
            assert "••••ZZ9Q" in saved.text
            assert "••••QQ11" in saved.text
    assert secret not in caplog.text
    assert new_id not in caplog.text
    stored = secrets.read_text(encoding="utf-8")
    assert secret in stored
    assert new_id in stored
    on_disk = settings.read_text(encoding="utf-8")
    assert secret not in on_disk
    assert new_id not in on_disk


def test_missing_key_log_and_status_rows(caplog):
    cfg = {
        "sources": {"adzuna": {"enabled": True}, "remotive": {"enabled": True}},
        "api": {},
        "search": {},
    }
    with caplog.at_level(logging.INFO):
        record = _execute_source("adzuna", lambda config: 0, cfg)
    assert record["status"] == "missing_key"
    assert "status=missing_key" in caplog.text
    assert "ADZUNA_APP_ID" in caplog.text
    rows = source_status_rows(
        cfg,
        {"remotive": {"status": "no_results", "status_detail": "crawl finished with no listings", "attempted": True}},
    )
    by_name = {row["name"]: row for row in rows}
    assert by_name["adzuna"]["status"] == "missing_key"
    assert by_name["remotive"]["status"] == "no_results"
    assert by_name["workday"]["status"] == "disabled"


def test_themuse_location_and_seniority(monkeypatch):
    from jobagent.config import crawl_config_for_candidates
    from jobagent.sources import themuse

    cfg = crawl_config_for_candidates(
        [{"titles": [{"title": "Sysadmin"}], "location": "Fort Smith, AR"}],
        base={
            "search": {},
            "sources": {
                "themuse": {
                    "enabled": True,
                    "max_pages": 1,
                    "location": "",
                    "levels": ["Senior Level", "Mid Level"],
                    "categories": ["Computer and IT", "Science and Engineering"],
                }
            },
        },
    )
    assert cfg["sources"]["themuse"]["location"] == "Fort Smith, AR"

    captured: dict = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _Resp()

    monkeypatch.setattr(themuse.requests, "get", fake_get)
    assert themuse.crawl(cfg) == 0
    assert captured["params"]["location"] == "Fort Smith, AR"
    assert captured["params"]["level"] == ["Senior Level", "Mid Level"]
    assert captured["params"]["category"] == ["Computer and IT", "Science and Engineering"]


def test_higheredjobs_html_is_blocked(monkeypatch):
    from jobagent.sources import higheredjobs

    class _Resp:
        status_code = 200
        content = b"<html><body>bot check</body></html>"
        headers = {"Content-Type": "text/html"}

        def raise_for_status(self):
            return None

    monkeypatch.setattr(higheredjobs.requests, "get", lambda *args, **kwargs: _Resp())
    record = _execute_source(
        "higheredjobs",
        higheredjobs.crawl,
        {"sources": {"higheredjobs": {"enabled": True, "category_ids": [144]}}, "search": {}},
    )
    assert record["status"] == "blocked"
    assert "bot-check" in record["status_detail"]


def test_workday_csrf_and_blocked_without_playwright(monkeypatch):
    import requests

    from jobagent.sources import workday
    from jobagent.sources.workday_discover import BoardSpec

    session = requests.Session()
    session.cookies.set("CALYPSO_CSRF_TOKEN", "abc%2B123")
    assert workday._csrf_token(session, "") == "abc+123"
    assert workday._csrf_token(requests.Session(), '{"csrfToken": "html-token"}') == "html-token"

    spec = BoardSpec(tenant="acme", cluster="wd1", site="Acme", locale="en-US")
    monkeypatch.setattr(workday, "_bootstrap_session", lambda trial: (None, None, "https://example.test"))
    monkeypatch.setattr(workday, "_clusters_to_try", lambda trial, for_playwright=False: ["wd1"])
    record = _execute_source(
        "workday",
        lambda config: workday._crawl_board(spec, "Acme", 1, config, False, None),
        {"sources": {"workday": {"enabled": True, "companies": [{"name": "Acme"}]}}, "search": {}},
    )
    assert record["status"] == "blocked"
    assert "no API key" in record["status_detail"]
    assert "Playwright" in record["status_detail"]
