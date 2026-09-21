#!/usr/bin/env python3
"""One-off: print HigherEdJobs RSS category id -> name from higheredjobs.com/rss/"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from agents.sources._common import BROWSER_HEADERS

r = requests.get("https://www.higheredjobs.com/rss/", headers=BROWSER_HEADERS, timeout=30)
r.raise_for_status()
for m in re.finditer(r"categoryFeed\.cfm\?catID=(\d+)[^>]*>([^<]+)", r.text):
    print(f"{m.group(1)}\t{m.group(2).strip()}")
