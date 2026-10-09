#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

USE_VENV=0
if [[ -d .venv && -x .venv/bin/python ]]; then
  USE_VENV=1
elif python3 -c "import ensurepip" >/dev/null 2>&1 \
  && python3 -m venv .venv 2>/dev/null \
  && [[ -x .venv/bin/python ]]; then
  USE_VENV=1
else
  rm -rf .venv
fi

if [[ "$USE_VENV" == 1 ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
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

if [[ "$USE_VENV" == 1 ]]; then
  exec python main.py run "$@"
else
  exec python3 main.py run "$@"
fi
