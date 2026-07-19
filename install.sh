#!/bin/bash
set -e

echo ""
echo "  _____ _     __  __ _            "
echo " |  ___(_)___|  \\/  (_)_ __   ___ "
echo " | |_  | |_  / |\\/| | | '_ \\ / _ \\"
echo " |  _| | |/ /| |  | | | | | |  __/"
echo " |_|   |_/___|_|  |_|_|_| |_|\___| "
echo "          Panel Installer"
echo ""

# Check for updates
if [ -f "$HOME/minecraft/panel.py" ]; then
  LOCAL_VER=$(grep -o "FizMine Panel v[0-9.]*" "$HOME/minecraft/panel.py" 2>/dev/null | head -1 | grep -o "[0-9.]*" || echo "0")
  REMOTE_VER=$(curl -sL "https://raw.githubusercontent.com/fizyCH/FizMine/main/panel.py" 2>/dev/null | grep -o "FizMine Panel v[0-9.]*" | head -1 | grep -o "[0-9.]*" || echo "0")
  if [ -n "$LOCAL_VER" ] && [ -n "$REMOTE_VER" ] && [ "$LOCAL_VER" != "$REMOTE_VER" ]; then
    echo "Update available: $LOCAL_VER -> $REMOTE_VER"
    read -rp "Update now? (y/n) [y]: " UPDATE_CHOICE
    UPDATE_CHOICE="${UPDATE_CHOICE:-y}"
    if [ "$UPDATE_CHOICE" = "y" ] || [ "$UPDATE_CHOICE" = "Y" ]; then
      echo "Updating..."
      cd "$HOME/minecraft"
      sudo wget -q "https://github.com/fizyCH/FizMine/releases/download/FizMine_Login_and_Play%21/panel.tar" -O /tmp/panel-update.tar
      sudo tar xf /tmp/panel-update.tar -C "$HOME/minecraft" --strip-components=0
      rm -f /tmp/panel-update.tar
      chmod +x ctl.sh panel.py 2>/dev/null
      echo "Updated to $REMOTE_VER!"
      exit 0
    fi
  fi
fi

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

for cmd in wget tar; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "Installing $cmd..."
    if command -v apt-get &>/dev/null; then
      sudo apt-get update -qq && sudo apt-get install -y -qq "$cmd"
    elif command -v dnf &>/dev/null; then
      sudo dnf install -y "$cmd"
    elif command -v yum &>/dev/null; then
      sudo yum install -y "$cmd"
    elif command -v pacman &>/dev/null; then
      sudo pacman -S --noconfirm "$cmd"
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
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y python3 python3-pip
  elif command -v yum &>/dev/null; then
    sudo yum install -y python3 python3-pip
  elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm python python-pip
  elif command -v apk &>/dev/null; then
    sudo apk add python3 py3-pip
  fi
fi

if ! command -v java &>/dev/null; then
  echo "Installing Java 17..."
  if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq openjdk-17-jre-headless 2>/dev/null || sudo apt-get install -y -qq default-jre-headless
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y java-17-openjdk-headless 2>/dev/null || sudo dnf install -y java-latest-openjdk-headless
  elif command -v yum &>/dev/null; then
    sudo yum install -y java-17-openjdk-headless 2>/dev/null || sudo yum install -y java-latest-openjdk-headless
  elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm jdk17-openjdk
  elif command -v apk &>/dev/null; then
    sudo apk add openjdk17-jre-headless
  fi
fi

echo "Downloading FizMine Panel..."
mkdir -p "$INSTALL_DIR"
cd /tmp
rm -f fizmine-panel.tar

DOWNLOAD_URL="https://github.com/fizyCH/FizMine/releases/download/FizMine_Login_and_Play%21/panel.tar"
wget -q "$DOWNLOAD_URL" -O fizmine-panel.tar

if [ ! -s fizmine-panel.tar ]; then
  echo "Download failed."
  echo "Download manually: wget \"$DOWNLOAD_URL\" -O panel.tar"
  echo "Then: tar xf panel.tar -C $INSTALL_DIR && cd $INSTALL_DIR && ./ctl.sh start"
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
