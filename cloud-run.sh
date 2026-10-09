#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON=python3
PIP=pip3
if [[ -d .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
  PIP=.venv/bin/pip
elif [[ ! -d .venv ]]; then
  if python3 -m venv .venv 2>/dev/null; then
    PYTHON=.venv/bin/python
    PIP=.venv/bin/pip
  else
    echo "note: python3-venv unavailable; using system python" >&2
  fi
fi
"$PIP" install -q -r requirements.txt

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$PYTHON" main.py run "$@"
