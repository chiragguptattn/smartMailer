#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

ensure_venv() {
  if [[ -x .venv/bin/pip ]]; then
    return 0
  fi
  rm -rf .venv
  if python3 -m venv .venv 2>/dev/null && [[ -x .venv/bin/pip ]]; then
    return 0
  fi
  rm -rf .venv
  if ! python3 -m pip show virtualenv &>/dev/null; then
    python3 -m pip install -q --user virtualenv
  fi
  PATH="${HOME}/.local/bin:${PATH}"
  python3 -m virtualenv .venv
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
