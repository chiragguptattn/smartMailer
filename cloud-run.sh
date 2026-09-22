#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  if ! python3 -m venv .venv >/dev/null 2>&1; then
    if ! command -v virtualenv >/dev/null 2>&1; then
      pip install -q --user virtualenv
      export PATH="${HOME}/.local/bin:${PATH}"
    fi
    virtualenv .venv
  fi
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec python main.py run "$@"
