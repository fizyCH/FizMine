#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

MC_DIR="${MC_DIR:-$SCRIPT_DIR}"
PANEL_PORT="${PANEL_PORT:-8080}"

show_menu() {
  clear
  echo ""
  echo "  +---------------------------------------+"
  echo "  |  _____ _     __  __ _                 |"
  echo "  | |  ___(_)___|  \/  (_)_ __   ___      |"
  echo "  | | |_  | |_  / |\/| | | '_ \ / _ \     |"
  echo "  | |  _| | |/ /| |  | | | | | |  __/     |"
  echo "  | |_|   |_/___|_|  |_|_|_| |_|\___|     |"
  echo "  |         Control Panel v2.0            |"
  echo "  +---------------------------------------+"
  echo "  |                                       |"
  echo "  |   1) Start panel                      |"
  echo "  |   2) Stop panel                       |"
  echo "  |   3) Restart panel                    |"
  echo "  |   4) Panel status                     |"
  echo "  |   5) Change port                      |"
  echo "  |   6) Java version                     |"
  echo "  |   7) Delete panel                     |"
  echo "  |   8) Exit                             |"
  echo "  |                                       |"
  echo "  +---------------------------------------+"
  echo ""
}

change_port() {
  echo ""
  echo "  Port Settings"
  echo "  -------------"
  echo "  Current: $PANEL_PORT"
  read -rp "  New port: " NEW_PORT
  if [ -n "$NEW_PORT" ]; then
    sed -i "s/^PANEL_PORT=.*/PANEL_PORT=$NEW_PORT/" "$ENV_FILE" 2>/dev/null || echo "PANEL_PORT=$NEW_PORT" >> "$ENV_FILE"
    echo "  Done! Port changed to $NEW_PORT"
    read -rp "  Restart panel? (y/n) [y]: " RESTART
    RESTART="${RESTART:-y}"
    if [ "$RESTART" = "y" ] || [ "$RESTART" = "Y" ]; then
      restart_panel
    fi
  fi
}

delete_panel() {
  echo ""
  echo "  +-----------------------------------+"
  echo "  |   WARNING: Delete all files?      |"
  echo "  +-----------------------------------+"
  echo "  Path: $SCRIPT_DIR"
  echo ""
  read -rp "  Type 'DELETE' to confirm: " CONFIRM
  if [ "$CONFIRM" = "DELETE" ]; then
    pkill -f "panel.py" 2>/dev/null
    rm -rf "$SCRIPT_DIR"
    echo "  Panel deleted."
    exit 0
  else
    echo "  Cancelled."
  fi
}

check_java() {
  echo ""
  echo "  Java"
  echo "  ----"
  if command -v java &>/dev/null; then
    java -version 2>&1 | head -1 | sed 's/^/  /'
    echo "  Java found"
  else
    echo "  Java not found!"
  fi
}

start_panel() {
  if pgrep -f "panel.py" > /dev/null; then
    echo "  Panel is already running"
    return
  fi
  cd "$SCRIPT_DIR"
  nohup python3 panel.py > /tmp/mcpanel.log 2>&1 &
  sleep 1
  if pgrep -f "panel.py" > /dev/null; then
    echo "  Panel started -> http://0.0.0.0:$PANEL_PORT"
  else
    echo "  Failed to start"
  fi
}

stop_panel() {
  if pgrep -f "panel.py" > /dev/null; then
    pkill -f "panel.py"
    echo "  Panel stopped"
  else
    echo "  Panel is not running"
  fi
}

restart_panel() {
  stop_panel
  sleep 1
  start_panel
}

status_panel() {
  if pgrep -f "panel.py" > /dev/null; then
    echo "  Running (PID: $(pgrep -f panel.py))"
  else
    echo "  Stopped"
  fi
}

if [ -n "$1" ]; then
  case "$1" in
    start) start_panel ;;
    stop) stop_panel ;;
    restart) restart_panel ;;
    status) status_panel ;;
    log) tail -50 /tmp/mcpanel.log ;;
    *) echo "Usage: $0 {start|stop|restart|status|log}" ;;
  esac
  exit 0
fi

while true; do
  show_menu
  read -rp "  Select [1-8]: " CHOICE
  case "$CHOICE" in
    1) start_panel ;;
    2) stop_panel ;;
    3) restart_panel ;;
    4) status_panel ;;
    5) change_port ;;
    6) check_java ;;
    7) delete_panel ;;
    8) echo "  Bye!"; exit 0 ;;
    *) echo "  Invalid choice" ;;
  esac
  echo ""
  read -rp "  Press Enter..."
done
