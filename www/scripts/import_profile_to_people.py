#!/usr/bin/env python3
"""One-off: copy config/profile.yaml into People (candidates) if the table is empty."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simple_ui.profile_import import sync_from_profile_yaml

if __name__ == "__main__":
    cid, action = sync_from_profile_yaml(set_active=True)
    if not cid:
        print("Skipped (profile.yaml missing or has no profile name).")
    else:
        print(f"{action} candidate id={cid} (active).")
