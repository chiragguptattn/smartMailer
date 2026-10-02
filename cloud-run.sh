#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON=python3
if [[ -d .venv/bin ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON=python
elif python3 -m venv .venv 2>/dev/null; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON=python
else
  rm -rf .venv
fi
pip install -q -r requirements.txt

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$PYTHON" main.py run "$@"
