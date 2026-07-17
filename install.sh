#!/bin/bash
set -e

echo ""
echo "  _____ _     __  __ _            "
echo " |  ___(_)___|  \/  (_)_ __   ___ "
echo " | |_  | |_  / |\/| | | '_ \ / _ \\"
echo " |  _| | |/ /| |  | | | | | |  __/"
echo " |_|   |_/___|_|  |_|_|_| |_|\___| "
echo "          Panel Installer"
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
  fi
fi

echo "Downloading FizMine Panel..."
mkdir -p "$INSTALL_DIR"
cd /tmp
rm -f fizmine-panel.tar

DOWNLOAD_URL="https://github.com/fizyCH/FizMine/releases/download/FizMine_Login_and_Play%21/panel.tar"
curl -L --max-time 120 -H "User-Agent: Mozilla/5.0" "$DOWNLOAD_URL" -o fizmine-panel.tar 2>/dev/null

if [ ! -s fizmine-panel.tar ]; then
  echo "Download failed. Please download manually:"
  echo "wget https://github.com/fizyCH/FizMine/releases/download/FizMine_Login_and_Play%21/panel.tar -O panel.tar"
  echo "tar xf panel.tar -C $INSTALL_DIR"
  echo "cd $INSTALL_DIR && ./ctl.sh start"
  exit 1
fi

echo "Extracting to $INSTALL_DIR..."
sudo tar xf fizmine-panel.tar -C "$INSTALL_DIR" --strip-components=0
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
echo "Installation complete!"
echo "======================"
echo "cd $INSTALL_DIR"
echo "./ctl.sh start"
echo "Panel: http://localhost:$PANEL_PORT"
echo ""
