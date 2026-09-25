#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if python3 -m venv --help >/dev/null 2>&1 && python3 -c "import ensurepip" 2>/dev/null; then
  if [[ ! -d .venv ]]; then
    python3 -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
else
  export PATH="${HOME}/.local/bin:${PATH}"
  python3 -m pip install -q -r requirements.txt --user
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PYTHON=python3
if command -v python >/dev/null 2>&1; then
  PYTHON=python
fi
exec "$PYTHON" main.py run "$@"
