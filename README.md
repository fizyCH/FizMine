# FizMine Panel

Powerful Minecraft server management panel built with Python/Flask.

## Screenshots

<img src="assets/Screen1.jpg" width="800">

<img src="assets/Screen2.jpg" width="800">

<img src="assets/Screen3.jpg" width="800">

## Quick Install

### Linux

```bash
curl -sLO https://raw.githubusercontent.com/fizyCH/FizMine/main/install.sh && bash install.sh
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/fizyCH/FizMine/main/install.ps1 | iex
```

### Manual Install

1. Download from [Releases](https://github.com/fizyCH/FizMine/releases)
2. Extract to your Minecraft server directory
3. Run: `python panel.py`

## Requirements

- Python 3.7+
- Java 17+ (for Minecraft server)

## Usage

```bash
./ctl.sh start      # Start panel
./ctl.sh stop       # Stop panel
./ctl.sh restart    # Restart panel
./ctl.sh status     # Check status
./ctl.sh log        # View logs
```

# Version 4.0

## What's New

| Feature                    | Description                                                                                                                                                                                                     |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Plugin System**          | The panel now loads plugins from the `plugin/` directory. Each plugin can include `manifest.json`, `plugin.css`, `plugin.html`, `plugin.js`, and an optional `backend.py` server-side module.                   |
| **Plugin Export**          | Every installed plugin can be downloaded as a reinstallable `.zip` archive using the new **Download** button next to **Delete**.                                                                                |
| **Plugin RPC**             | Plugins can communicate with their backend using `FizPlugin.rpc()`, which sends requests to `POST /api/panel-plugins/rpc`. The backend implements `handle(method, args)` inside `backend.py`.                   |
| **Permissions**            | Plugin management (install, enable/disable, syntax check, delete, download, and RPC) is restricted to **administrators**. Any authenticated user can view the list of installed plugins and read plugin assets. |
| **Server Properties Menu** | Redesigned the server properties menu — it is now more convenient, cleaner, and easier to use.                                                                                                                  |
| **User Online Status**     | Added the ability to see which users are currently online and which are offline in the **Users** tab.                                                                                                           |
| **Mobile Panel Fixes**     | Fixed issues with panel display and layout on mobile devices.                                                                                                                                                   |

