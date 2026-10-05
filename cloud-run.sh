#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON=(python3)
use_venv=false
if [[ -d .venv/bin ]] && [[ -x .venv/bin/python ]]; then
  use_venv=true
elif python3 -c "import ensurepip" >/dev/null 2>&1; then
  if [[ ! -d .venv ]] && python3 -m venv .venv >/dev/null 2>&1 && [[ -x .venv/bin/python ]]; then
    use_venv=true
  else
    rm -rf .venv 2>/dev/null || true
  fi
fi

if [[ "$use_venv" == true ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON=(python)
else
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if [[ -d .venv/bin ]] && [[ -x .venv/bin/python ]]; then
  pip install -q -r requirements.txt
else
  pip3 install -q -r requirements.txt
fi

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "${PYTHON[@]}" main.py run "$@"
