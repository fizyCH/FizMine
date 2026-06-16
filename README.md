# FizMine Panel

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Platform-Linux%20%7C%20Windows-brightgreen" alt="Platform">
  <img src="https://img.shields.io/badge/Java-17%2B-orange?logo=openjdk&logoColor=white" alt="Java">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
</p>

<p align="center">
  <b>Lightweight, cross-platform Minecraft server management panel with a modern dark UI.</b>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#features">Features</a> •
  <a href="#configuration">Configuration</a> •
  <a href="#commands">Commands</a>
</p>

---

## What's New

### v2.0

| Change | Description |
|--------|-------------|
| Confirmation modals | Custom animated modals instead of browser `confirm()` |
| File editor | Edit `.json`, `.yml`, `.txt`, `.properties` directly in panel |
| Folder navigation | Browse server directories with breadcrumbs |
| Fireflies | Ambient background effect with accent color |
| 5 languages | English, Russian, German, French, Chinese |
| Crash detection | Sound notification when server crashes |
| UI overhaul | Animations, SVG icons, Minecraft-style FM logo |
| .env settings | Edit MC_DIR, PORT, JAVA_ARGS from the panel |
| Bug fixes | TPS display, ban/unban UUIDs for premium & offline accounts |

---

## Features

### Dashboard
- Real-time server status, memory usage, TPS, player count
- Start / Stop / Restart with one click
- Live console preview

### Server Setup
- Upload `server.jar` via drag & drop (Spigot, Paper, Purpur, Forge, etc.)
- Auto-accept EULA, generate `server.properties`
- Replace core with toggle options: keep or delete world, mods, plugins, ops, bans, whitelist

### Console
- Live server console with auto-scroll
- Send any command directly from the panel

### Player Management
- Online players list
- Add / Remove OPs, Whitelist, Banned players
- UUID resolution for both **premium** and **offline (cracked)** accounts
- Uses server commands (`op`, `ban`, `pardon`, `whitelist add/remove`) — no restart needed

### Files
- Edit `server.properties` with validation
- Browse server files with folder navigation and breadcrumbs
- Edit `.json`, `.yml`, `.txt`, `.properties` and other text files directly

### Plugins & Mods
- Upload `.jar` files via drag & drop or file picker
- Delete individual or all plugins/mods
- Delete All button when more than 2 installed

### Settings
- Multi-language: **English**, **Русский**, **Deutsch**, **Français**, **中文**
- Accent color picker with 10 presets + custom color
- Fireflies ambient background effect (toggle)
- Java auto-detection (prefers Java 17+)
- RCON support for reliable command delivery
- MC_DIR, PANEL_PORT, JAVA_ARGS, JAVA_ENCODING configurable from UI

---

## Quick Start

### 1. Download panel

Download the archive.
Unzip it into the server folder.

### 2. Configure

```bash
nano .env
```

In `.env` set up what you need (for example, take the `.env.example`):

```env
MC_DIR=YOURFOLDER
PANEL_PORT=8080
```

> In the new version, you can remove `PANEL_LANG` from `.env` and set the language through the panel settings.

### 3. Run

```bash
python3 ctl.py start
```

Open **http://localhost:8080** in your browser.

---

## Commands

```bash
python3 ctl.py start     # Start the panel
python3 ctl.py stop      # Stop the panel
python3 ctl.py restart   # Restart the panel
python3 ctl.py status    # Check panel status
```

---

## Configuration

All settings are in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `MC_DIR` | `/minecraft` | Minecraft server directory |
| `PANEL_PORT` | `8080` | Panel web port |
| `PANEL_LANG` | `en` | Panel language (`en`, `ru`, `de`, `fr`, `zh`) |
| `JAVA_PATH` | *(auto)* | Path to Java executable |
| `JAVA_ARGS` | `-Xmx2G -Xms1G` | JVM arguments |

---

## Windows Support

FizMine Panel works on both **Linux** and **Windows**.

> **Recommended:** Enable RCON in `server.properties` for best Windows support:

```properties
enable-rcon=true
rcon.port=25575
rcon.password=your_password
```

---

## Requirements

- Python 3.8+
- Java 17+
- Minecraft server jar (Spigot, Paper, Purpur, Forge, etc.)

---

## License

MIT License
