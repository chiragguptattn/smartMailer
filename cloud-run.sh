#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON=python3
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON=python
elif [[ -d .venv ]]; then
  rm -rf .venv
fi

if [[ "$PYTHON" == "python3" ]] && python3 -c "import ensurepip" 2>/dev/null; then
  if python3 -m venv .venv; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    PYTHON=python
  fi
fi

pip install -q -r requirements.txt

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$PYTHON" main.py run "$@"
