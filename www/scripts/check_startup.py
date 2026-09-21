#!/usr/bin/env python3
"""Run on the server to see why uvicorn exits with status=3."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    print("=== Job Agent startup check ===")
    print(f"ROOT: {ROOT}")
    print(f"Python: {sys.version.split()[0]}")
    py = sys.version_info
    if py < (3, 10):
        print("WARN: Python 3.10+ recommended (this code uses dict | None syntax)")

    profile = ROOT / "config" / "profile.yaml"
    if not profile.is_file():
        print("FAIL: missing config/profile.yaml")
    else:
        try:
            import yaml

            yaml.safe_load(profile.read_text(encoding="utf-8"))
            print("OK   config/profile.yaml parses")
        except Exception as exc:
            print(f"FAIL config/profile.yaml: {exc}")
            return 1

    modules = [
        "simple_ui.app",
        "simple_ui.config_writer",
        "data.db",
        "agents.apply_pipeline",
        "agents.sources.higheredjobs_catalog",
        "api.routes.matches",
    ]
    for name in modules:
        try:
            __import__(name)
            print(f"OK   import {name}")
        except Exception as exc:
            print(f"FAIL import {name}: {exc}")
            return 1

    try:
        from simple_ui.app import app

        print(f"OK   app loaded ({app.title})")
    except Exception as exc:
        print(f"FAIL app load: {exc}")
        return 1

    print("=== All checks passed — try: sudo systemctl restart job-agent ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
