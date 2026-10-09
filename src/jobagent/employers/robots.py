"""Minimal robots.txt check for employer-board probes."""
from __future__ import annotations

from urllib.parse import urlparse


def disallow_rules(text: str) -> list[str]:
    """Disallow paths for a JobAgent group, otherwise for User-agent: *."""
    groups: list[tuple[list[str], list[str]]] = []
    agents: list[str] = []
    rules: list[str] = []

    def flush() -> None:
        nonlocal agents, rules
        if agents:
            groups.append((agents, rules))
        agents = []
        rules = []

    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            flush()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if rules:
                flush()
            agents.append(value.lower())
        elif key == "disallow" and agents:
            rules.append(value)
    flush()

    star: list[str] | None = None
    specific: list[str] | None = None
    for group_agents, group_rules in groups:
        if any(agent == "*" for agent in group_agents):
            star = group_rules
        if any("jobagent" in agent for agent in group_agents):
            specific = group_rules
    chosen = specific if specific is not None else star
    return list(chosen or [])


def path_allowed(rules: list[str], path: str) -> bool:
    """Empty Disallow allows the site. Any matching prefix blocks the path."""
    target = path or "/"
    for rule in rules:
        if not rule:
            continue
        if target.startswith(rule):
            return False
    return True


def origin_of(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def request_path(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path or "/"
