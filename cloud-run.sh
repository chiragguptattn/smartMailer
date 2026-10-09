#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

use_venv() {
  [[ -x .venv/bin/python ]] && [[ -x .venv/bin/pip ]]
}

if use_venv; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
  PYTHON=(python)
elif python3 -m venv .venv 2>/dev/null && use_venv; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
  PYTHON=(python)
else
  rm -rf .venv
  python3 -m pip install -q -r requirements.txt --user
  PYTHON=(python3)
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "${PYTHON[@]}" main.py run "$@"
