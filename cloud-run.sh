#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON=python3
PIP=(python3 -m pip)
if [[ ! -f .venv/bin/activate ]]; then
  rm -rf .venv
  python3 -m venv .venv 2>/dev/null || true
fi
if [[ -f .venv/bin/activate ]] && [[ -x .venv/bin/python ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON=python
  PIP=(python -m pip)
fi
"${PIP[@]}" install -q -r requirements.txt

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$PYTHON" main.py run "$@"
