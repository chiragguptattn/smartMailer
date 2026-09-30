#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON="python3"
if [[ ! -d .venv ]]; then
  if ! python3 -m venv .venv &>/dev/null; then
    rm -rf .venv
  fi
fi
if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON="python"
  pip install -q -r requirements.txt
else
  pip3 install -q --user -r requirements.txt
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$PYTHON" main.py run "$@"
