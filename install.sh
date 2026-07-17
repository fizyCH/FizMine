#!/bin/bash
set -e

INSTALL_DIR="${1:-/minecraft}"

echo "╔══════════════════════════════════╗"
echo "║       FizMine Panel Installer    ║"
echo "╚══════════════════════════════════╝"
echo ""

# Check dependencies
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
      echo "Error: $cmd is required. Install it manually."
      exit 1
    fi
  fi
done

# Check python3
if ! command -v python3 &>/dev/null; then
  echo "Installing python3..."
  if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip
  elif command -v yum &>/dev/null; then
    sudo yum install -y python3 python3-pip
  elif command -v apk &>/dev/null; then
    sudo apk add python3 py3-pip
  fi
fi

# Check java
if ! command -v java &>/dev/null; then
  echo "Installing Java 17..."
  if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq openjdk-17-jre-headless || sudo apt-get install -y -qq default-jre-headless
  elif command -v yum &>/dev/null; then
    sudo yum install -y java-17-openjdk-headless || sudo yum install -y java-latest-openjdk-headless
  elif command -v apk &>/dev/null; then
    sudo apk add openjdk17-jre-headless
  fi
fi

echo "Downloading FizMine Panel..."
mkdir -p "$INSTALL_DIR"
cd /tmp
curl -sL "https://github.com/fizyCH/FizMine/releases/download/FizMine_Login_and_Play!/panel.tar" -o fizmine-panel.tar
echo "Extracting to $INSTALL_DIR..."
sudo tar xzf fizmine-panel.tar -C "$INSTALL_DIR" --strip-components=0
rm -f fizmine-panel.tar

chmod +x "$INSTALL_DIR/ctl.sh" 2>/dev/null || true
chmod +x "$INSTALL_DIR/panel.py" 2>/dev/null || true

echo ""
echo "╔══════════════════════════════════╗"
echo "║     Installation complete!       ║"
echo "╠══════════════════════════════════╣"
echo "║  cd $INSTALL_DIR                "
echo "║  ./ctl.sh start                  ║"
echo "║  Panel: http://localhost:8080    ║"
echo "╚══════════════════════════════════╝"
