#!/usr/bin/env bash
# Per-boot checks: venv present; Gmail JSON secrets materialize on first connect().
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv — run install first (bash .cursor/cloud-agent-install.sh)" >&2
  exit 1
fi

.venv/bin/python -c "import cursor_sdk; import googleapiclient.discovery"
echo "smartMailer: environment ready (.venv ok)"
