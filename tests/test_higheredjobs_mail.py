"""HigherEdJobs alert mailbox: parse fixtures, dedupe, and masked settings."""
from __future__ import annotations

import logging
import os
from email.message import EmailMessage

import pytest
import yaml
from fastapi.testclient import TestClient

from jobagent.config import imap_configured, load_imap_settings, save_imap_settings
from jobagent.doctor import collect_report, format_report
from jobagent.sources import _execute_source, source_skip_reason, source_status_rows
from jobagent.sources import higheredjobs_mail
from jobagent.web.app import app

PASSWORD = "mailbox-secret-ZZ99"
USERNAME = "alerts-user-QQ11"


def _message(html: str, sender: str = "alerts@higheredjobs.com", plain: str = "") -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["Subject"] = "Your HigherEdJobs alert"
    message.set_content(plain or "See the HTML part.")
    message.add_alternative(html, subtype="html")
    return message.as_bytes()


ALERT_HTML = """
<html><body>
  <a href="https://www.higheredjobs.com/details.cfm?JobCode=4412&amp;Title=Dean">Dean of Libraries</a>
  <a href="https://www.higheredjobs.com/faculty/details.cfm?JobCode=4413">Assistant Professor of History</a>
  <a href="https://example.com/not-a-job">Ignore me</a>
</body></html>
"""


class _Box:
    messages = [_message(ALERT_HTML), _message("<a href='https://example.com/x'>Other</a>", sender="other@example.test")]

    def __init__(self, settings):
        self.settings = settings

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def search_ids(self):
        return [b"1", b"2"]

    def fetch(self, msg_id):
        return self.messages[int(msg_id) - 1]


class _Boom:
    def __init__(self, settings):
        self.settings = settings

    def __enter__(self):
        raise RuntimeError(f"login failed for {self.settings['password']}")

    def __exit__(self, exc_type, exc, tb):
        return None


def _config():
    return {"sources": {"higheredjobs_mail": {"enabled": True}}, "search": {}}


def test_parse_alert_keeps_job_links_only():
    jobs = higheredjobs_mail.parse_alert(ALERT_HTML, "https://www.higheredjobs.com/details.cfm?JobCode=999")
    urls = [job["url"] for job in jobs]
    assert "https://www.higheredjobs.com/details.cfm?JobCode=4412&Title=Dean" in urls
    assert any("JobCode=4413" in url for url in urls)
    assert any(url.endswith("JobCode=999") for url in urls)
    assert all("higheredjobs.com" in url for url in urls)
    assert jobs[0]["title"] == "Dean of Libraries"


def test_missing_mailbox_is_not_configured():
    assert imap_configured() is False
    assert source_skip_reason("higheredjobs_mail", _config()) == "not configured"
    rows = source_status_rows(_config(), {})
    row = next(item for item in rows if item["name"] == "higheredjobs_mail")
    assert row["status"] == "not_configured"
    assert row["status_label"] == "not configured"


def test_alert_inserts_then_second_pass_is_no_new_alerts(db_path, caplog):
    save_imap_settings(
        {
            "host": "imap.example.test",
            "port": "993",
            "username": USERNAME,
            "password": PASSWORD,
            "folder": "Alerts",
            "sender": "higheredjobs.com",
        }
    )
    caplog.set_level(logging.INFO)
    first = _execute_source("higheredjobs_mail", lambda config: higheredjobs_mail.crawl(config, _Box), _config())
    assert first["status"] == "ok"
    assert first["inserted"] == 2
    from jobagent.db.connection import get_conn

    with get_conn() as conn:
        rows = conn.execute("SELECT title, url, source FROM jobs ORDER BY title").fetchall()
    assert len(rows) == 2
    assert {row[2] for row in rows} == {"higheredjobs_mail"}

    second = _execute_source("higheredjobs_mail", lambda config: higheredjobs_mail.crawl(config, _Box), _config())
    assert second["status"] == "no_new_alerts"
    assert second["inserted"] == 0
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert PASSWORD not in logged
    assert USERNAME not in logged


def test_mailbox_failure_does_not_log_the_password(db_path, caplog):
    save_imap_settings({"host": "imap.example.test", "username": USERNAME, "password": PASSWORD})
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RuntimeError, match="HigherEdJobs mailbox read failed") as caught:
        higheredjobs_mail.crawl(_config(), _Boom)
    assert PASSWORD not in str(caught.value)
    assert USERNAME not in str(caught.value)
    record = _execute_source("higheredjobs_mail", lambda config: higheredjobs_mail.crawl(config, _Boom), _config())
    assert record["status"] == "error"
    assert PASSWORD not in record["error"]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert PASSWORD not in logged


def test_blank_imap_field_keeps_the_secret_and_settings_masks_it(db_path, tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    path.write_text("scheduler:\n  dry_run: true\n  auto_apply: false\nsearch: {}\nsources: {}\n", encoding="utf-8")
    monkeypatch.setenv("JOBAGENT_SETTINGS_PATH", str(path))
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "")
    save_imap_settings(
        {
            "host": "imap.example.test",
            "username": USERNAME,
            "password": PASSWORD,
            "folder": "INBOX",
            "sender": "higheredjobs.com",
        }
    )
    monkeypatch.setenv("HEJ_IMAP_PASSWORD", "")
    with TestClient(app) as client:
        page = client.get("/settings")
        assert page.status_code == 200
        assert PASSWORD not in page.text
        assert USERNAME not in page.text
        assert "••••ZZ99" in page.text
        assert "••••QQ11" in page.text
        assert "not configured" in page.text
        saved = client.post(
            "/settings",
            data={
                "dry_run": "1",
                "auto_apply": "0",
                "imap_host": "imap.other.test",
                "imap_port": "993",
                "imap_username": "",
                "imap_password": "",
                "imap_folder": "INBOX",
                "imap_sender": "higheredjobs.com",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
    stored = load_imap_settings()
    assert stored["password"] == PASSWORD
    assert stored["username"] == USERNAME
    assert stored["host"] == "imap.other.test"
    raw = yaml.safe_load(open(os.environ["JOBAGENT_SECRETS_PATH"], encoding="utf-8"))
    assert raw["imap"]["password"] == PASSWORD
    report = collect_report()
    text = format_report(report)
    assert report["higheredjobs_mail_configured"] is True
    assert "HEJ mailbox:  configured" in text
    assert PASSWORD not in text
    assert USERNAME not in text
    assert "dry_run: true" in path.read_text(encoding="utf-8")
    assert "auto_apply: false" in path.read_text(encoding="utf-8")
