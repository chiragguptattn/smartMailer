#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

venv_python_ok() {
  [[ -x .venv/bin/python ]] && .venv/bin/python -c 'import pip' &>/dev/null
}

PYTHON="python3"
if venv_python_ok; then
  PYTHON=".venv/bin/python"
elif python3 -c 'import ensurepip' &>/dev/null \
  && python3 -m venv .venv &>/dev/null \
  && venv_python_ok; then
  PYTHON=".venv/bin/python"
else
  rm -rf .venv 2>/dev/null || true
fi

if [[ "$PYTHON" == .venv/bin/python ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
else
  python3 -m pip install -q -r requirements.txt --user
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ "$PYTHON" == .venv/bin/python ]]; then
  exec python main.py run "$@"
else
  exec python3 main.py run "$@"
fi
