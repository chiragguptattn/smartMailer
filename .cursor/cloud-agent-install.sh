#!/usr/bin/env bash
# Idempotent dependency setup for Cloud Agents (matches cloud-run.sh venv layout).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

python -c "import cursor_sdk; import googleapiclient.discovery"
echo "smartMailer: Python deps installed in .venv"
