# FizMine Panel

Minecraft server management panel.

## Screenshots

<img src="assets/Screen1.jpg" width="500">

<img src="assets/Screen2.jpg" width="500">

<img src="assets/Screen3.jpg" width="500">

## Quick Install

### Linux

```bash
curl -sLO https://raw.githubusercontent.com/fizyCH/FizMine/main/install.sh && bash install.sh
```

Replace `/minecraft` with your server directory path.

### Windows (PowerShell as Administrator)

```powershell
irm https://raw.githubusercontent.com/fizyCH/FizMine/main/install.ps1 | iex
```

Default install path: `C:\minecraft`

### Manual Install

1. Download from [Releases](https://github.com/fizyCH/FizMine/releases)
2. Extract to your Minecraft server directory
3. Run: `python panel.py` (or `python3 panel.py`)

## Requirements

- Python 3.7+
- Java 17+ (for Minecraft server)

## Usage

```bash
# Start panel
./ctl.sh start      # Linux
python panel.py     # All platforms

# Panel commands
./ctl.sh stop       # Stop panel
./ctl.sh restart    # Restart panel
./ctl.sh status     # Check status
./ctl.sh log        # View logs
```

## What's New

| Type | Feature | Description |
|------|---------|-------------|
| Fixed | Java Check on Startup | The panel now displays the installed Java version in the terminal during startup. |
| New | GitHub Update Checker | Added a **Check for Updates** button in **Settings → System** to check for new releases on GitHub. |
| New | Purpur Core Support | Added **Purpur** (Bukkit/Spigot hybrid) as a downloadable server core. |
| New | Arclight Core Support | Added **Arclight** (Forge + Bukkit hybrid) as a downloadable server core. |
| Fixed | Automatic Flask Installation | Improved automatic Flask installation with a `sudo pip` fallback and clearer error messages. |
| Beta | Update Checker | Introduced the first beta version of the update checking system. |
