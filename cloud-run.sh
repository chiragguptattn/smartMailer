#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

venv_ok() {
  [[ -x .venv/bin/python ]] && .venv/bin/python -m pip --version &>/dev/null
}

ensure_venv() {
  if [[ -d .venv ]] && ! venv_ok; then
    rm -rf .venv
  fi
  if [[ ! -d .venv ]]; then
    if ! python3 -m venv .venv &>/dev/null; then
      rm -rf .venv 2>/dev/null || true
    fi
  fi
  if ! venv_ok; then
    rm -rf .venv 2>/dev/null || true
    python3 -m pip install -q --user virtualenv
    python3 -m virtualenv .venv
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
