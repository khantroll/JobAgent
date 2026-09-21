"""Alpha.1 auth: a single shared token from the environment.

No user accounts. If JOB_AGENT_API_TOKEN is unset, API and UI stay open for
local development. If it is set, the REST API requires a Bearer token and the
HTML UI requires a login cookie.
"""
from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException, Request, status

COOKIE_NAME = "jobagent_token"


def configured_token() -> str:
    return os.environ.get("JOB_AGENT_API_TOKEN", "").strip()


def api_token_configured() -> bool:
    return bool(configured_token())


def tokens_match(provided: str | None, expected: str) -> bool:
    if not provided or not expected:
        return False
    left = provided.encode("utf-8")
    right = expected.encode("utf-8")
    # Python < 3.12 raises ValueError when the lengths differ.
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def token_from_request(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    cookie = request.cookies.get(COOKIE_NAME)
    return cookie.strip() if cookie else None


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = configured_token()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header (Bearer token)",
        )
    token = authorization[7:].strip()
    if not tokens_match(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
        )


def is_public_ui_path(path: str) -> bool:
    if path == "/health" or path == "/login":
        return True
    if path.startswith("/static/") or path == "/static":
        return True
    return False
