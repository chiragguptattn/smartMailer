#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON="python3"
if [[ -d .venv && ! -x .venv/bin/python ]]; then
  rm -rf .venv
fi
if [[ -x .venv/bin/python ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON="python"
elif python3 -m venv .venv &>/dev/null && [[ -x .venv/bin/python ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON="python"
else
  rm -rf .venv
  PYTHON="python3"
fi

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  pip install -q -r requirements.txt
else
  python3 -m pip install -q --user -r requirements.txt
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$PYTHON" main.py run "$@"
