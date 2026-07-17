#!/bin/bash
set -e

C='\033[0;36m'
R='\033[0m'

echo ""
echo -e "${C}  _____ _     __  __ _            ${R}"
echo -e "${C} |  ___(_)___|  \\/  (_)_ __   ___ ${R}"
echo -e "${C} | |_  | |_  / |\\/| | | '_ \\ / _ \\${R}"
echo -e "${C} |  _| | |/ /| |  | | | | | |  __/${R}"
echo -e "${C} |_|   |_/___|_|  |_|_|_| |_|\\___| ${R}"
echo -e "${C}          Panel Installer${R}"
echo ""

read -rp "Install path [~/minecraft]: " INSTALL_DIR
INSTALL_DIR="${INSTALL_DIR:-$HOME/minecraft}"

read -rp "Enable authentication? (y/n) [n]: " AUTH_CHOICE
AUTH_CHOICE="${AUTH_CHOICE:-n}"

read -rp "Panel port [8080]: " PANEL_PORT
PANEL_PORT="${PANEL_PORT:-8080}"

echo ""
echo "Installing to: $INSTALL_DIR"
echo "Auth: $AUTH_CHOICE"
echo "Port: $PANEL_PORT"
echo ""

for cmd in curl tar; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "Installing $cmd..."
    if command -v apt-get &>/dev/null; then
      sudo apt-get update -qq && sudo apt-get install -y -qq "$cmd"
    elif command -v yum &>/dev/null; then
      sudo yum install -y "$cmd"
    elif command -v apk &>/dev/null; then
      sudo apk add "$cmd"
    elif command -v brew &>/dev/null; then
      brew install "$cmd"
    else
      echo "Error: $cmd is required."
      exit 1
    fi
  fi
done

if ! command -v python3 &>/dev/null; then
  echo "Installing Python3..."
  if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip
  elif command -v yum &>/dev/null; then
    sudo yum install -y python3 python3-pip
  elif command -v apk &>/dev/null; then
    sudo apk add python3 py3-pip
  elif command -v brew &>/dev/null; then
    brew install python3
  fi
fi

if ! command -v java &>/dev/null; then
  echo "Installing Java 17..."
  if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq openjdk-17-jre-headless 2>/dev/null || sudo apt-get install -y -qq default-jre-headless
  elif command -v yum &>/dev/null; then
    sudo yum install -y java-17-openjdk-headless 2>/dev/null || sudo yum install -y java-latest-openjdk-headless
  elif command -v apk &>/dev/null; then
    sudo apk add openjdk17-jre-headless
  elif command -v brew &>/dev/null; then
    brew install openjdk@17
  fi
fi

echo "Downloading FizMine Panel..."
mkdir -p "$INSTALL_DIR"
cd /tmp
curl -sL "https://github.com/fizyCH/FizMine/releases/download/FizMine_Login_and_Play%21/panel.tar" -o fizmine-panel.tar

if [ ! -s fizmine-panel.tar ] || ! file fizmine-panel.tar | grep -q gzip; then
  echo "Download failed. Please download manually from:"
  echo "https://github.com/fizyCH/FizMine/releases"
  rm -f fizmine-panel.tar
  exit 1
fi

echo "Extracting to $INSTALL_DIR..."
sudo tar xzf fizmine-panel.tar -C "$INSTALL_DIR" --strip-components=0
rm -f fizmine-panel.tar

chmod +x "$INSTALL_DIR/ctl.sh" 2>/dev/null || true
chmod +x "$INSTALL_DIR/panel.py" 2>/dev/null || true

AUTH_TOKEN=""
if [ "$AUTH_CHOICE" = "y" ] || [ "$AUTH_CHOICE" = "Y" ]; then
  read -rp "Set authentication password: " AUTH_TOKEN
fi

cat > "$INSTALL_DIR/.env" << ENVEOF
PANEL_PORT=$PANEL_PORT
MC_DIR=$INSTALL_DIR
PANEL_TOKEN=$AUTH_TOKEN
ENVEOF

echo ""
echo -e "${C}  Installation complete!${R}"
echo "  ======================"
echo "  cd $INSTALL_DIR"
echo "  ./ctl.sh start"
echo "  Panel: http://localhost:$PANEL_PORT"
echo ""
