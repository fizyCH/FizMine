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
  echo "  _____ _     __  __ _            "
  echo " |  ___(_)___|  \\/  (_)_ __   ___ "
  echo " | |_  | |_  / |\\/| | | '_ \\ / _ \\"
  echo " |  _| | |/ /| |  | | | | | |  __/"
  echo " |_|   |_/___|_|  |_|_|_| |_|\___| "
  echo "          Control Panel"
  echo ""
  echo "  1) Change port"
  echo "  2) Delete panel"
  echo "  3) Java version"
  echo "  4) Exit"
  echo ""
}

change_port() {
  echo ""
  echo "Current port: $PANEL_PORT"
  read -rp "New port: " NEW_PORT
  if [ -n "$NEW_PORT" ]; then
    sed -i "s/^PANEL_PORT=.*/PANEL_PORT=$NEW_PORT/" "$ENV_FILE" 2>/dev/null || echo "PANEL_PORT=$NEW_PORT" >> "$ENV_FILE"
    echo "Port changed to $NEW_PORT"
    read -rp "Restart panel now? (y/n) [y]: " RESTART
    RESTART="${RESTART:-y}"
    if [ "$RESTART" = "y" ] || [ "$RESTART" = "Y" ]; then
      restart_panel
    fi
  fi
}

delete_panel() {
  echo ""
  echo "WARNING: This will delete the entire panel directory!"
  echo "Path: $SCRIPT_DIR"
  read -rp "Are you sure? (y/n) [n]: " CONFIRM
  if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
    pkill -f "panel.py" 2>/dev/null
    rm -rf "$SCRIPT_DIR"
    echo "Panel deleted."
    exit 0
  else
    echo "Cancelled."
  fi
}

check_java() {
  echo ""
  if command -v java &>/dev/null; then
    java -version 2>&1 | head -1
  else
    echo "Java not found!"
  fi
}

start_panel() {
  if pgrep -f "panel.py" > /dev/null; then
    echo "Panel is already running"
    return
  fi
  cd "$SCRIPT_DIR"
  nohup python3 panel.py > /tmp/mcpanel.log 2>&1 &
  sleep 1
  if pgrep -f "panel.py" > /dev/null; then
    echo "Panel started on http://0.0.0.0:$PANEL_PORT"
  else
    echo "Failed to start panel"
  fi
}

stop_panel() {
  pkill -f "panel.py" 2>/dev/null
  echo "Panel stopped"
}

restart_panel() {
  stop_panel
  sleep 1
  start_panel
}

# If argument passed, use old behavior
if [ -n "$1" ]; then
  case "$1" in
    start) start_panel ;;
    stop) stop_panel ;;
    restart) restart_panel ;;
    status)
      if pgrep -f "panel.py" > /dev/null; then
        echo "Panel is running (PID: $(pgrep -f panel.py))"
      else
        echo "Panel is not running"
      fi
      ;;
    log) tail -50 /tmp/mcpanel.log ;;
    *) echo "Usage: $0 {start|stop|restart|status|log}" ;;
  esac
  exit 0
fi

# Interactive menu
while true; do
  show_menu
  read -rp "Select [1-4]: " CHOICE
  case "$CHOICE" in
    1) change_port ;;
    2) delete_panel ;;
    3) check_java ;;
    4) echo "Bye!"; exit 0 ;;
    *) echo "Invalid choice" ;;
  esac
  echo ""
  read -rp "Press Enter to continue..."
done
