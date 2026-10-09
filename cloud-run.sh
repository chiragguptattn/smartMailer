#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

venv_usable() {
  [[ -x .venv/bin/python ]] && .venv/bin/python -m pip --version &>/dev/null
}

PYTHON="python3"
if venv_usable; then
  PYTHON=".venv/bin/python"
else
  rm -rf .venv
  if python3 -m venv .venv &>/dev/null && venv_usable; then
    PYTHON=".venv/bin/python"
  fi
fi

"$PYTHON" -m pip install -q -r requirements.txt

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$PYTHON" main.py run "$@"
