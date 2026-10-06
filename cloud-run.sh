#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON="python3"
PIP_INSTALL=(pip3 install -q -r requirements.txt)

if [[ ! -d .venv ]] || ! .venv/bin/python3 -m pip --version &>/dev/null; then
  rm -rf .venv
  if python3 -m venv .venv 2>/dev/null && .venv/bin/python3 -m pip --version &>/dev/null; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    PIP_INSTALL=(pip install -q -r requirements.txt)
    PYTHON="python"
  fi
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PIP_INSTALL=(pip install -q -r requirements.txt)
  PYTHON="python"
fi

"${PIP_INSTALL[@]}"

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$PYTHON" main.py run "$@"
