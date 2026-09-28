#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

activate_venv() {
  # shellcheck disable=SC1091
  source .venv/bin/activate
}

venv_ok() {
  [[ -f .venv/bin/activate ]] && .venv/bin/python -c "import sys" &>/dev/null
}

if venv_ok; then
  activate_venv
else
  rm -rf .venv 2>/dev/null || true
  if python3 -m venv .venv &>/dev/null && venv_ok; then
    activate_venv
  else
    rm -rf .venv 2>/dev/null || true
  fi
fi

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  pip install -q -r requirements.txt
  PYTHON=python
else
  pip3 install -q -r requirements.txt
  PYTHON=python3
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$PYTHON" main.py run "$@"
