#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

_venv_python() {
  [[ -x .venv/bin/python ]] && .venv/bin/python -c "import sys" >/dev/null 2>&1
}

if ! _venv_python; then
  rm -rf .venv
  if ! python3 -m venv .venv >/dev/null 2>&1 || ! _venv_python; then
    rm -rf .venv
    USE_SYSTEM_PYTHON=1
  fi
fi

if [[ -z "${USE_SYSTEM_PYTHON:-}" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
else
  pip3 install -q -r requirements.txt --user
  export PATH="${HOME}/.local/bin:${PATH}"
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec python3 main.py run "$@"
