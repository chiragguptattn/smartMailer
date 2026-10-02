#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON="python3"
PIP=(python3 -m pip)

venv_ok() {
  [[ -x .venv/bin/python ]] && [[ -x .venv/bin/pip ]]
}

if ! venv_ok; then
  rm -rf .venv
  if python3 -m venv .venv 2>/dev/null && venv_ok; then
    PYTHON=".venv/bin/python"
    PIP=(.venv/bin/pip)
  else
    rm -rf .venv
  fi
fi

if venv_ok; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON="python"
  PIP=(pip)
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
