#!/usr/bin/env bash
# Install / uninstall macOS LaunchAgent for always-on Gmail draft watcher.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.tothenew.gmail-draft-agent"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
PYTHON="$ROOT/.venv/bin/python"
LOG_DIR="$ROOT/logs"

usage() {
  echo "Usage: $0 {install|uninstall|status|restart}"
  exit 1
}

[[ $# -ge 1 ]] || usage
CMD="$1"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing venv python at $PYTHON — run: python3 -m venv .venv && pip install -r requirements.txt"
  exit 1
fi
if [[ ! -f "$ROOT/.env" ]]; then
  echo "Missing $ROOT/.env — copy from .env.example and set CURSOR_API_KEY / GMAIL_FROM_DOMAIN"
  exit 1
fi

case "$CMD" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
    cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${ROOT}/main.py</string>
    <string>watch</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/launchd.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:${ROOT}/.venv/bin</string>
  </dict>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    launchctl enable "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    echo "Installed and started: $PLIST"
    echo "Logs: $LOG_DIR/watch.log  (also launchd.out.log / launchd.err.log)"
    launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | head -20 || true
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Uninstalled ${LABEL}"
    ;;
  status)
    if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
      echo "LaunchAgent loaded"
      launchctl print "gui/$(id -u)/${LABEL}" | head -40
    else
      echo "LaunchAgent not loaded"
    fi
    if [[ -f "$ROOT/watch.pid" ]]; then
      pid="$(cat "$ROOT/watch.pid")"
      if kill -0 "$pid" 2>/dev/null; then
        echo "watch.pid=$pid (running)"
      else
        echo "watch.pid=$pid (stale)"
      fi
    fi
    ;;
  restart)
    "$0" uninstall
    "$0" install
    ;;
  *)
    usage
    ;;
esac
