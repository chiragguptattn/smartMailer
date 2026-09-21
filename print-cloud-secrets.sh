#!/usr/bin/env bash
# Print Cursor Cloud secret values to copy into dashboard (stdout only — do not commit).
# Usage: ./print-cloud-secrets.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -f credentials.json || ! -f token.json ]]; then
  echo "Need credentials.json and token.json (run: python main.py auth)" >&2
  exit 1
fi

echo "Add these as Secrets at https://cursor.com/dashboard/cloud-agents"
echo "(Secrets tab). Do not commit them."
echo
echo "=== CURSOR_API_KEY ==="
if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a && source .env && set +a
fi
if [[ -z "${CURSOR_API_KEY:-}" ]]; then
  echo "(set CURSOR_API_KEY in .env first)" >&2
else
  echo "$CURSOR_API_KEY"
fi
echo
echo "=== GMAIL_FROM_DOMAIN ==="
echo "${GMAIL_FROM_DOMAIN:-tothenew.com}"
echo
echo "=== AGENT_SIGN_OFF_NAME ==="
echo "${AGENT_SIGN_OFF_NAME:-}"
echo
echo "=== CURSOR_MODEL ==="
echo "${CURSOR_MODEL:-composer-2.5}"
echo
echo "=== GMAIL_MAX_THREADS ==="
echo "${GMAIL_MAX_THREADS:-10}"
echo
echo "=== GMAIL_PROCESSED_LABEL ==="
echo "${GMAIL_PROCESSED_LABEL:-AI/Drafted}"
echo
echo "=== GMAIL_CREDENTIALS_JSON (single line) ==="
python3 -c 'import json; print(json.dumps(json.load(open("credentials.json"))))'
echo
echo "=== GMAIL_TOKEN_JSON (single line) ==="
python3 -c 'import json; print(json.dumps(json.load(open("token.json"))))'
