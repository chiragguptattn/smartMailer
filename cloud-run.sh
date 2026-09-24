#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON="python3"
if [[ ! -x .venv/bin/python ]]; then
  rm -rf .venv
  if python3 -m venv .venv >/dev/null 2>&1 && [[ -x .venv/bin/python ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install -q -r requirements.txt
    PYTHON=".venv/bin/python"
  else
    rm -rf .venv
    python3 -m pip install -q -r requirements.txt --user
  fi
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
  PYTHON=".venv/bin/python"
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$PYTHON" main.py run "$@"
