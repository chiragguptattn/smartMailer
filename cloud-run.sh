#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

_venv_ok() {
  [[ -x .venv/bin/python ]] && .venv/bin/python -c "import sys" >/dev/null 2>&1
}

PYTHON=python3
if _venv_ok; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON=python
else
  rm -rf .venv
  if python3 -m venv .venv >/dev/null 2>&1 && _venv_ok; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    PYTHON=python
  else
    rm -rf .venv
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
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
