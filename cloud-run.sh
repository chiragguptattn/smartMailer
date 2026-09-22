#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

_venv_python() {
  [[ -x .venv/bin/python ]]
}

PYTHON=(python3)
if _venv_python; then
  :
elif python3 -m venv .venv >/dev/null 2>&1 && _venv_python; then
  :
else
  rm -rf .venv
  pip3 install -q -r requirements.txt --user --break-system-packages 2>/dev/null \
    || pip3 install -q -r requirements.txt --user
fi

if _venv_python; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
  PYTHON=(python)
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "${PYTHON[@]}" main.py run "$@"
