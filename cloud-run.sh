#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON="python3"
if [[ ! -x .venv/bin/pip ]]; then
  rm -rf .venv
  if python3 -m venv .venv &>/dev/null && [[ -x .venv/bin/pip ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    PYTHON=".venv/bin/python3"
    pip install -q -r requirements.txt
  else
    rm -rf .venv
    python3 -m pip install -q --user -r requirements.txt
  fi
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON=".venv/bin/python3"
  pip install -q -r requirements.txt
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$PYTHON" main.py run "$@"
