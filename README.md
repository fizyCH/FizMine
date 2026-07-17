# FizMine Panel

Minecraft server management panel.

## Quick Install

### Linux / macOS

```bash
curl -sL https://raw.githubusercontent.com/fizyCH/FizMine/main/install.sh | sudo bash -s -- /minecraft
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
./ctl.sh start      # Linux/macOS
python panel.py     # All platforms

# Panel commands
./ctl.sh stop       # Stop panel
./ctl.sh restart    # Restart panel
./ctl.sh status     # Check status
./ctl.sh log        # View logs
```

## What's New

| Change | Description |
|--------|-------------|
| Authentication | Login with protection against settings tampering via /api/settings |
| Anti-brute-force | 5 failed attempts = 5 minute lockout |
| Password validation | Min 5 chars, common weak passwords rejected |
| Memory/Disk/CPU | Usage in %; click to open ring chart diagrams |
| Charts | Update in real-time; crash detection with sound alert |
| File manager | Sort by name (all languages) and size |
| File search | Recursive search across subdirectories |
| File upload | Upload files to any directory |
| File download | Download files; folders auto-zipped |
| File delete | Delete with confirmation |
| Server core | Replace core; choose files to keep; everything else deleted |
| Accent color | Color picker with presets in settings |
| Fireflies | Ambient particles with accent color breathing animation |
| Panel opacity | Slider 0–100% for panel background transparency |
| Auth settings | Toggle to enable/disable with collapsible panel |
| Backup | Panel backup + server backup download |
| Confirmation modals | Custom animated modals instead of browser `confirm()` |
| File editor | Edit `.json`, `.yml`, `.txt`, `.properties` directly in panel |
| Folder navigation | Browse server directories with breadcrumbs |
| 5 languages | English, Russian, German, French, Chinese |
| Crash detection | Sound notification when server crashes |
| UI overhaul | Animations, Minecraft-style FizMine logo |
| .env settings | Edit MC_DIR, PORT, JAVA_ARGS from the panel |
| Bug fixes | TPS display, ban/unban UUIDs for premium & offline accounts |
| Cross-platform | Linux + Windows, auto-install Flask, auto-detect Java |
