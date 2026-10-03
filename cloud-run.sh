#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -d .venv ]] && [[ ! -f .venv/bin/activate ]]; then
  rm -rf .venv
fi

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
else
  if python3 -m venv .venv 2>/dev/null; then
    if [[ -f .venv/bin/activate ]]; then
      # shellcheck disable=SC1091
      source .venv/bin/activate
      pip install -q -r requirements.txt
    else
      rm -rf .venv
    fi
  fi
  if [[ ! -f .venv/bin/activate ]]; then
    # Cloud images may lack python3-venv; use user site-packages.
    pip install -q --user -r requirements.txt
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -f .venv/bin/activate ]]; then
  exec python main.py run "$@"
fi
exec python3 main.py run "$@"
