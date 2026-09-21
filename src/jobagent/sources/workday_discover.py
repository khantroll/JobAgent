"""Discover and parse Workday career board URLs from employer pages."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# https://okgov.wd1.myworkdayjobs.com/okgovjobs  (no locale segment)
# https://okgov.wd1.myworkdayjobs.com/en-US/okgovjobs
CLUSTERED_RE = re.compile(
    r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com"
    r"(?:/([a-z]{2}-[A-Z]{2}))?/([^/?#]+)",
    re.I,
)
# Legacy bare hostname: https://okgov.myworkdayjobs.com/okgovjobs
BARE_RE = re.compile(
    r"https?://([a-z0-9-]+)\.myworkdayjobs\.com"
    r"(?:/([a-z]{2}-[A-Z]{2}))?/([^/?#]+)",
    re.I,
)
WORKDAY_HREF_RE = re.compile(r"https?://[^\s\"'<>]*myworkdayjobs\.com[^\s\"'<>]*", re.I)

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass(frozen=True)
class BoardSpec:
    tenant: str
    cluster: str  # wd1, wd503, or "bare"
    site: str
    locale: str | None  # None → default en-US in URL builders
    source_url: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.tenant, self.cluster, self.site)


def board_page_url(
    tenant: str,
    site: str,
    cluster: str,
    locale: str | None = None,
) -> str:
    """Canonical public careers page URL."""
    default_locale = "en-US"
    if locale is None:
        locale = default_locale
    base = (
        f"https://{tenant}.myworkdayjobs.com"
        if not cluster or cluster == "bare"
        else f"https://{tenant}.{cluster}.myworkdayjobs.com"
    )
    if locale in ("", "none", "/"):
        return f"{base}/{site}"
    return f"{base}/{locale.strip('/')}/{site}"


def jobs_api_url(tenant: str, site: str, cluster: str) -> str:
    base = (
        f"https://{tenant}.myworkdayjobs.com"
        if not cluster or cluster == "bare"
        else f"https://{tenant}.{cluster}.myworkdayjobs.com"
    )
    return f"{base}/wday/cxs/{tenant}/{site}/jobs"


def job_posting_url(
    tenant: str,
    site: str,
    cluster: str,
    external_path: str,
    locale: str | None = None,
) -> str:
    default_locale = "en-US"
    if locale is None:
        locale = default_locale
    base = (
        f"https://{tenant}.myworkdayjobs.com"
        if not cluster or cluster == "bare"
        else f"https://{tenant}.{cluster}.myworkdayjobs.com"
    )
    if locale in ("", "none", "/"):
        return f"{base}/{site}{external_path}"
    return f"{base}/{locale.strip('/')}/{site}{external_path}"


def parse_workday_url(url: str) -> BoardSpec | None:
    """Parse a Workday careers URL into tenant / cluster / site / locale."""
    url = (url or "").strip()
    if not url:
        return None

    match = CLUSTERED_RE.search(url)
    if match:
        tenant, cluster, locale, site = match.groups()
        return BoardSpec(
            tenant=tenant.lower(),
            cluster=cluster.lower(),
            site=site,
            locale=locale if locale else "",
            source_url=url.split("?")[0],
        )

    match = BARE_RE.search(url)
    if match:
        tenant, locale, site = match.groups()
        if site.lower() in ("wday", "job"):
            return None
        return BoardSpec(
            tenant=tenant.lower(),
            cluster="bare",
            site=site,
            locale=locale if locale else "",
            source_url=url.split("?")[0],
        )
    return None


def find_workday_urls(html: str, base_url: str = "") -> list[str]:
    """Extract Workday URLs from HTML anchors and embedded strings."""
    found: set[str] = set()

    if base_url:
        try:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup.find_all("a", href=True):
                href = urljoin(base_url, tag["href"])
                if "myworkdayjobs.com" in href.lower():
                    found.add(href.split("?")[0])
        except Exception:
            pass

    for match in WORKDAY_HREF_RE.findall(html):
        found.add(match.split("?")[0])

    return sorted(found)


def discover_from_careers_page(careers_url: str) -> list[BoardSpec]:
    """Fetch an employer careers page and parse any linked Workday boards."""
    try:
        resp = requests.get(
            careers_url,
            headers=FETCH_HEADERS,
            timeout=30,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("[workday] careers page fetch failed %s: %s", careers_url, e)
        return []

    urls = find_workday_urls(resp.text, resp.url)
    if "myworkdayjobs.com" in resp.url.lower():
        urls.append(resp.url.split("?")[0])

    boards: list[BoardSpec] = []
    seen: set[tuple[str, str, str]] = set()
    for url in urls:
        spec = parse_workday_url(url)
        if not spec or spec.key in seen:
            continue
        seen.add(spec.key)
        boards.append(spec)

    if boards:
        logger.info(
            "[workday] discovered %s board(s) from %s",
            len(boards),
            careers_url,
        )
    else:
        logger.warning("[workday] no Workday links found on %s", careers_url)

    return boards


def resolve_board_specs(entry: dict, defaults: dict | None = None) -> list[BoardSpec]:
    """
    Resolve one config entry into board specs.

    Priority:
      1. careers_url — discover live Workday links (preferred)
      2. workday_url — canonical fallback when discovery finds nothing
      3. tenant + site/board + cluster/host — manual identifiers
    """
    defaults = defaults or {}
    specs: list[BoardSpec] = []
    seen: set[tuple[str, str, str]] = set()

    def add(spec: BoardSpec | None) -> None:
        if spec and spec.key not in seen:
            seen.add(spec.key)
            specs.append(spec)

    careers_url = (entry.get("careers_url") or "").strip()
    if careers_url:
        for spec in discover_from_careers_page(careers_url):
            add(spec)

    workday_url = (entry.get("workday_url") or entry.get("url") or "").strip()
    if workday_url and not specs:
        add(parse_workday_url(workday_url))

    tenant = (entry.get("tenant") or "").strip()
    site = (entry.get("site") or entry.get("board") or "").strip()
    if tenant and site and not specs:
        cluster = (
            entry.get("cluster")
            or entry.get("host")
            or defaults.get("cluster")
            or defaults.get("host")
            or "wd1"
        )
        cluster = str(cluster).strip() or "wd1"
        locale = entry["locale"] if "locale" in entry else defaults.get("locale")
        add(
            BoardSpec(
                tenant=tenant.lower(),
                cluster=cluster,
                site=site,
                locale=locale,
                source_url=workday_url or "",
            )
        )

    return specs
