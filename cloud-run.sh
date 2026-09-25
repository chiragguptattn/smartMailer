#!/usr/bin/env bash
# One-shot entrypoint for Cursor Cloud Automations (cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -x .venv/bin/python ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif python3 -m venv .venv >/dev/null 2>&1 && [[ -x .venv/bin/python ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  rm -rf .venv 2>/dev/null || true
  echo "cloud-run: python3-venv unavailable; using system Python" >&2
fi
python3 -m pip install -q -r requirements.txt

# Prefer secrets injected by Cursor Cloud; fall back to local .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec python3 main.py run "$@"
