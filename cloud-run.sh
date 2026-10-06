#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

ensure_venv() {
  if [[ -d .venv && ! -f .venv/bin/activate ]]; then
    rm -rf .venv
  fi
  if [[ -d .venv ]]; then
    return 0
  fi
  if ! python3 -m venv .venv 2>/dev/null; then
    rm -rf .venv
    python3 -m pip install -q --user virtualenv
    python3 -m virtualenv .venv
  fi
  if [[ ! -f .venv/bin/activate ]]; then
    echo "Failed to create virtual environment (.venv/bin/activate missing)" >&2
    exit 1
  fi
}

ensure_venv
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
