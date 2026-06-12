# FizMine Panel

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Platform-Linux%20%7C%20Windows-brightgreen" alt="Platform">
  <img src="https://img.shields.io/badge/Java-17%2B-orange?logo=openjdk&logoColor=white" alt="Java">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
</p>

<p align="center">
  Lightweight, cross-platform Minecraft server management panel with a modern dark UI.
</p>

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
- Browse server files and directories

### Plugins & Mods
- Upload `.jar` files via drag & drop or file picker
- Delete installed plugins and mods

### Settings
- Multi-language: **English**, **Русский**, **Deutsch**
- Java auto-detection (prefers Java 17+)
- RCON support for reliable command delivery
- Configurable via `.env` file

---

## Quick Start

### 1. Clone

```bash
git clone https://github.com/yourusername/fizmine-panel.git
cd fizmine-panel/panel
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` if needed:

```env
MC_DIR=/minecraft
PANEL_PORT=8080
PANEL_LANG=en
```

### 3. Run

```bash
python3 ctl.py start
```

Open **http://localhost:8080** in your browser.

---

## Commands

| Command | Description |
|---------|-------------|
| `python3 ctl.py start` | Start the panel |
| `python3 ctl.py stop` | Stop the panel |
| `python3 ctl.py restart` | Restart the panel |
| `python3 ctl.py status` | Check panel status |

Or use the bash script:

```bash
./ctl.sh start
./ctl.sh stop
```

---

## Configuration

All settings are in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `MC_DIR` | `/minecraft` | Minecraft server directory |
| `PANEL_PORT` | `8080` | Panel web port |
| `PANEL_LANG` | `en` | Panel language (`en`, `ru`, `de`) |
| `JAVA_PATH` | *(auto)* | Path to Java executable |
| `JAVA_ARGS` | `-Xmx2G -Xms1G` | JVM arguments |
| `JAVA_ENCODING` | *(auto)* | Console encoding (`cp1251` for Windows Russian) |

---

## Windows Support

FizMine Panel works on both **Linux** and **Windows**:

- Auto-detects Java installations from common paths
- Uses `CREATE_NO_WINDOW` for process management
- RCON support for reliable command delivery on Windows
- Set `JAVA_ENCODING=cp1251` in `.env` for correct Russian console output

### Recommended: Enable RCON

For best results on Windows, enable RCON in `server.properties`:

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

## Screenshots

> Dashboard with server status and live console

---

## License

MIT License — use freely, modify freely.

---

<p align="center">
  Made with care for the Minecraft community
</p>
