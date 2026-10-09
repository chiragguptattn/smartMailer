#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

_venv_ok() {
  [[ -x .venv/bin/python ]] && .venv/bin/python -c "import sys" &>/dev/null
}

if _venv_ok; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
else
  rm -rf .venv
  if python3 -m venv .venv 2>/dev/null && _venv_ok; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install -q -r requirements.txt
  else
    rm -rf .venv
    python3 -m pip install -q -r requirements.txt --user
  fi
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  exec python main.py run "$@"
fi
exec python3 main.py run "$@"
