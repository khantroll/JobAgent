"""HigherEdJobs saved-search alerts read from an IMAP mailbox.

The owner creates the alert on HigherEdJobs and forwards it to a dedicated
mailbox. This source downloads those messages and keeps the job links. It does
not log the mailbox password or username, and it does not submit applications.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
from email.header import decode_header
from html import unescape
from html.parser import HTMLParser

from jobagent.config import imap_configured, load_imap_settings
from jobagent.sources._common import insert_mapped, mark_source_status, source_enabled
from jobagent.sources.normalize import catalog_job

logger = logging.getLogger(__name__)

_JOB_LINK = re.compile(
    r"https?://(?:www\.)?higheredjobs\.com/(?:faculty/)?details\.cfm\?[^\"'\s<]*JobCode=\d+[^\"'\s<]*",
    re.I,
)


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = ""
        for key, value in attrs:
            if key.lower() == "href" and value:
                href = value
        self._href = href
        self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        self.links.append((self._href, "".join(self._chunks).strip()))
        self._href = ""
        self._chunks = []


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def parse_alert(html: str, plain: str = "") -> list[dict[str, str]]:
    """Job links and titles from one HigherEdJobs alert. Other links are ignored."""
    parser = _AnchorParser()
    try:
        parser.feed(html or "")
    except Exception:
        parser.links = []
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(url: str, title: str) -> None:
        match = _JOB_LINK.search(url or "")
        if not match:
            return
        clean = unescape(match.group(0)).rstrip(").,;")
        if clean in seen:
            return
        seen.add(clean)
        label = re.sub(r"\s+", " ", title or "").strip() or "HigherEdJobs listing"
        found.append({"title": label, "url": clean})

    for href, title in parser.links:
        add(href, title)
    for match in _JOB_LINK.finditer(plain or ""):
        add(match.group(0), "")
    for match in _JOB_LINK.finditer(html or ""):
        add(match.group(0), "")
    return found


def _message_parts(raw: bytes) -> tuple[str, str]:
    message = email.message_from_bytes(raw)
    html = ""
    plain = ""
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        kind = (part.get_content_type() or "").lower()
        payload = part.get_payload(decode=True)
        text = _decode(payload)
        if kind == "text/html":
            html += text
        elif kind == "text/plain":
            plain += text
    return html, plain


def _header_text(raw: bytes) -> str:
    message = email.message_from_bytes(raw)
    chunks: list[str] = []
    for key in ("From", "Subject"):
        value = message.get(key) or ""
        for piece, charset in decode_header(value):
            if isinstance(piece, bytes):
                chunks.append(piece.decode(charset or "utf-8", errors="replace"))
            else:
                chunks.append(piece)
    return " ".join(chunks)


class _Mailbox:
    """Thin IMAP wrapper so tests can substitute a fake client."""

    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self.client: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> _Mailbox:
        self.client = imaplib.IMAP4_SSL(self.settings["host"], int(self.settings.get("port") or 993))
        self.client.login(self.settings["username"], self.settings["password"])
        self.client.select(self.settings.get("folder") or "INBOX", readonly=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.client is not None:
            try:
                self.client.logout()
            except Exception:
                pass
        return None

    def search_ids(self) -> list[bytes]:
        assert self.client is not None
        status, data = self.client.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    def fetch(self, msg_id: bytes) -> bytes:
        assert self.client is not None
        status, data = self.client.fetch(msg_id, "(RFC822)")
        if status != "OK" or not data:
            return b""
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                return bytes(item[1])
        return b""


def iter_alert_bodies(settings: dict, mailbox_factory=_Mailbox):
    """Yield (html, plain) for messages that mention the configured sender."""
    sender = str(settings.get("sender") or "higheredjobs.com").lower()
    with mailbox_factory(settings) as mailbox:
        ids = mailbox.search_ids()
        logger.info("[higheredjobs_mail] mailbox has %s message(s)", len(ids))
        for msg_id in ids[-50:]:
            raw = mailbox.fetch(msg_id)
            if not raw:
                continue
            header = _header_text(raw).lower()
            html, plain = _message_parts(raw)
            blob = f"{header}\n{plain}\n{html}".lower()
            if sender and sender not in blob:
                continue
            yield html, plain


def crawl(config: dict, mailbox_factory=_Mailbox) -> int:
    if not source_enabled(config, "higheredjobs_mail"):
        return 0
    settings = load_imap_settings()
    if not imap_configured(settings):
        logger.info("[higheredjobs_mail] status=not_configured — mailbox host, username, and password are unset")
        return 0
    logger.info("[higheredjobs_mail] reading alerts from folder %s", settings.get("folder") or "INBOX")
    inserted = 0
    try:
        for html, plain in iter_alert_bodies(settings, mailbox_factory):
            for job in parse_alert(html, plain):
                mapped = catalog_job(
                    title=job["title"],
                    url=job["url"],
                    company="HigherEdJobs",
                    source="higheredjobs_mail",
                    description="",
                )
                if insert_mapped(mapped):
                    inserted += 1
    except Exception:
        logger.warning("[higheredjobs_mail] mailbox read failed")
        raise RuntimeError("HigherEdJobs mailbox read failed") from None
    if inserted == 0:
        mark_source_status("no_new_alerts", "mailbox checked; no new HigherEdJobs alerts")
        logger.info("[higheredjobs_mail] status=no_new_alerts — mailbox checked; no new HigherEdJobs alerts")
    else:
        logger.info("[higheredjobs_mail] %s new listing(s)", inserted)
    return inserted
