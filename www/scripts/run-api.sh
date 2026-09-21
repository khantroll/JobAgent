#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x venv/bin/uvicorn ]]; then
  python3 -m venv venv
  ./venv/bin/pip install -r requirements.txt
fi
exec ./venv/bin/uvicorn simple_ui.app:app --host 127.0.0.1 --port 8765 --reload
