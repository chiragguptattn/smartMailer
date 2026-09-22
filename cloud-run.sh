#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON=python3
_venv_pip_ok() {
  [[ -x .venv/bin/python ]] && .venv/bin/python -m pip --version &>/dev/null
}

if _venv_pip_ok; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON=python
else
  rm -rf .venv
  set +e
  python3 -m venv .venv &>/dev/null
  set -e
  if _venv_pip_ok; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    PYTHON=python
  else
    rm -rf .venv
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
