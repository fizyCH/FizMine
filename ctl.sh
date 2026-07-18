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

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

show_menu() {
  clear
  echo ""
  echo -e "  ${CYAN}╔══════════════════════════════════════╗${NC}"
  echo -e "  ${CYAN}║${NC}  _____ _     __  __ _                ${CYAN}║${NC}"
  echo -e "  ${CYAN}║${NC} |  ___(_)___|  \\/  (_)_ __   ___    ${CYAN}║${NC}"
  echo -e "  ${CYAN}║${NC} | |_  | |_  / |\\/| | | '_ \\ / _ \\   ${CYAN}║${NC}"
  echo -e "  ${CYAN}║${NC} |  _| | |/ /| |  | | | | | |  __/   ${CYAN}║${NC}"
  echo -e "  ${CYAN}║${NC} |_|   |_/___|_|  |_|_|_| |_|\\___|   ${CYAN}║${NC}"
  echo -e "  ${CYAN}║${NC}         ${DIM}Control Panel v2.0${NC}           ${CYAN}║${NC}"
  echo -e "  ${CYAN}╠══════════════════════════════════════╣${NC}"
  echo -e "  ${CYAN}║${NC}                                      ${CYAN}║${NC}"
  echo -e "  ${CYAN}║${NC}   ${GREEN}1)${NC} Change port                     ${CYAN}║${NC}"
  echo -e "  ${CYAN}║${NC}   ${RED}2)${NC} Delete panel                    ${CYAN}║${NC}"
  echo -e "  ${CYAN}║${NC}   ${YELLOW}3)${NC} Java version                    ${CYAN}║${NC}"
  echo -e "  ${CYAN}║${NC}   ${DIM}4)${NC} Exit                            ${CYAN}║${NC}"
  echo -e "  ${CYAN}║${NC}                                      ${CYAN}║${NC}"
  echo -e "  ${CYAN}╚══════════════════════════════════════╝${NC}"
  echo ""
}

change_port() {
  echo ""
  echo -e "  ${CYAN}Port Settings${NC}"
  echo -e "  ${DIM}─────────────${NC}"
  echo -e "  Current: ${BOLD}$PANEL_PORT${NC}"
  read -rp "  New port: " NEW_PORT
  if [ -n "$NEW_PORT" ]; then
    sed -i "s/^PANEL_PORT=.*/PANEL_PORT=$NEW_PORT/" "$ENV_FILE" 2>/dev/null || echo "PANEL_PORT=$NEW_PORT" >> "$ENV_FILE"
    echo -e "  ${GREEN}✓ Port changed to $NEW_PORT${NC}"
    read -rp "  Restart panel? (y/n) [y]: " RESTART
    RESTART="${RESTART:-y}"
    if [ "$RESTART" = "y" ] || [ "$RESTART" = "Y" ]; then
      restart_panel
    fi
  fi
}

delete_panel() {
  echo ""
  echo -e "  ${RED}╔═══════════════════════════════════╗${NC}"
  echo -e "  ${RED}║${NC}   ⚠  WARNING: Delete all files?  ${RED}║${NC}"
  echo -e "  ${RED}╚═══════════════════════════════════╝${NC}"
  echo -e "  ${DIM}Path: $SCRIPT_DIR${NC}"
  echo ""
  read -rp "  Type 'DELETE' to confirm: " CONFIRM
  if [ "$CONFIRM" = "DELETE" ]; then
    pkill -f "panel.py" 2>/dev/null
    rm -rf "$SCRIPT_DIR"
    echo -e "  ${RED}Panel deleted.${NC}"
    exit 0
  else
    echo -e "  ${GREEN}Cancelled.${NC}"
  fi
}

check_java() {
  echo ""
  echo -e "  ${CYAN}Java${NC}"
  echo -e "  ${DIM}────${NC}"
  if command -v java &>/dev/null; then
    java -version 2>&1 | head -1 | sed 's/^/  /'
    echo -e "  ${GREEN}✓ Java found${NC}"
  else
    echo -e "  ${RED}✗ Java not found!${NC}"
  fi
}

start_panel() {
  if pgrep -f "panel.py" > /dev/null; then
    echo -e "  ${YELLOW}⚠ Panel is already running${NC}"
    return
  fi
  cd "$SCRIPT_DIR"
  nohup python3 panel.py > /tmp/mcpanel.log 2>&1 &
  sleep 1
  if pgrep -f "panel.py" > /dev/null; then
    echo -e "  ${GREEN}✓ Panel started${NC} → http://0.0.0.0:$PANEL_PORT"
  else
    echo -e "  ${RED}✗ Failed to start${NC}"
  fi
}

stop_panel() {
  if pgrep -f "panel.py" > /dev/null; then
    pkill -f "panel.py"
    echo -e "  ${GREEN}✓ Panel stopped${NC}"
  else
    echo -e "  ${YELLOW}⚠ Panel is not running${NC}"
  fi
}

restart_panel() {
  stop_panel
  sleep 1
  start_panel
}

if [ -n "$1" ]; then
  case "$1" in
    start) start_panel ;;
    stop) stop_panel ;;
    restart) restart_panel ;;
    status)
      if pgrep -f "panel.py" > /dev/null; then
        echo -e "  ${GREEN}● Running${NC} (PID: $(pgrep -f panel.py))"
      else
        echo -e "  ${RED}● Stopped${NC}"
      fi
      ;;
    log) tail -50 /tmp/mcpanel.log ;;
    *) echo "Usage: $0 {start|stop|restart|status|log}" ;;
  esac
  exit 0
fi

while true; do
  show_menu
  read -rp "  Select [1-4]: " CHOICE
  case "$CHOICE" in
    1) change_port ;;
    2) delete_panel ;;
    3) check_java ;;
    4) echo -e "\n  ${DIM}Bye!${NC}"; exit 0 ;;
    *) echo -e "  ${RED}Invalid choice${NC}" ;;
  esac
  echo ""
  read -rp "  Press Enter..."
done
