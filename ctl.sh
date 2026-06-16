#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

MC_DIR="${MC_DIR:-/minecraft}"
PANEL_PORT="${PANEL_PORT:-8080}"
PANEL_LANG="${PANEL_LANG:-en}"

case "$1" in
  start)
    if pgrep -f "panel.py" > /dev/null; then
      echo "Panel is already running"
      exit 1
    fi
    cd "$SCRIPT_DIR"
    nohup python3 panel.py > /tmp/mcpanel.log 2>&1 &
    sleep 1
    if pgrep -f "panel.py" > /dev/null; then
      echo "Panel started on http://0.0.0.0:$PANEL_PORT"
    else
      echo "Failed to start panel"
      exit 1
    fi
    ;;
  stop)
    pkill -f "panel.py"
    echo "Panel stopped"
    ;;
  restart)
    $0 stop
    sleep 1
    $0 start
    ;;
  status)
    if pgrep -f "panel.py" > /dev/null; then
      echo "Panel is running (PID: $(pgrep -f panel.py))"
    else
      echo "Panel is not running"
    fi
    ;;
  log)
    tail -50 /tmp/mcpanel.log
    ;;
  *)
    echo "FizMine Panel Manager"
    echo "Usage: $0 {start|stop|restart|status|log}"
    exit 1
    ;;
esac
