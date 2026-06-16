#!/usr/bin/env python3
import http.server
import io
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"


def load_env():
    env_path = Path(__file__).parent / ".env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


_env = load_env()

_default_mcdir = "C:\\minecraft" if IS_WINDOWS else "/minecraft"
MC_DIR = Path(os.environ.get("MC_DIR", _env.get("MC_DIR", _default_mcdir)))
LOG_FILE = MC_DIR / "logs" / "latest.log"
PANEL_PORT = int(os.environ.get("PANEL_PORT", _env.get("PANEL_PORT", "8080")))
JAVA_ENCODING = _env.get("JAVA_ENCODING", "")

_server_proc = None
_server_lock = threading.Lock()
_rcon_socket = None


def rcon_send(cmd, host="127.0.0.1", port=25575, password=""):
    import socket, struct
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((host, port))

        def rcon_packet(ptype, body):
            payload = body.encode("utf-8") + b"\x00"
            size = 4 + 4 + len(payload)
            return struct.pack("<i", size) + struct.pack("<i", ptype) + payload

        auth_pkt = rcon_packet(3, password)
        s.send(auth_pkt)
        resp = s.recv(4096)
        if len(resp) >= 12:
            resp_type = struct.unpack("<i", resp[4:8])[0]
            if resp_type == -1:
                s.close()
                return None

        cmd_pkt = rcon_packet(2, cmd)
        s.send(cmd_pkt)
        result = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            result += chunk
            if len(chunk) < 4096:
                break
        s.close()

        if len(result) >= 16:
            body = result[12:-1].decode("utf-8", errors="replace")
            return body
        return ""
    except Exception:
        return None


def _read_rcon_config():
    props = {}
    props_file = MC_DIR / "server.properties"
    if props_file.exists():
        try:
            for line in props_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    props[k.strip()] = v.strip()
        except Exception:
            pass
    return {
        "enabled": props.get("enable-rcon", "false") == "true",
        "port": int(props.get("rcon.port", "25575")),
        "password": props.get("rcon.password", "")
    }


def find_java():
    java_path = os.environ.get("JAVA_PATH") or _env.get("JAVA_PATH", "")
    if java_path and Path(java_path).exists():
        return java_path

    candidates = []

    if IS_WINDOWS:
        search = []
        for var in ["JAVA_HOME", "ProgramFiles", "ProgramFiles(x86)"]:
            base = os.environ.get(var, "")
            if base:
                search.append(Path(base))
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        search += [
            Path(program_data) / "Oracle" / "Java",
            Path(r"C:\Program Files\Java"),
            Path(r"C:\Program Files\Eclipse Adoptium"),
            Path(r"C:\Program Files\Microsoft"),
            Path(r"C:\Program Files\Zulu"),
            Path(r"C:\Program Files\Amazon Corretto"),
            Path(r"C:\Program Files\BellSoft"),
        ]
        for base in search:
            if not base.exists():
                continue
            try:
                for item in sorted(base.iterdir(), reverse=True):
                    exe = item / "bin" / "java.exe"
                    if exe.exists():
                        candidates.append(str(exe))
            except PermissionError:
                pass
        try:
            r = subprocess.run("where java", capture_output=True, text=True, shell=True, timeout=5)
            if r.returncode == 0:
                for line in r.stdout.strip().split("\n"):
                    p = line.strip()
                    if p:
                        candidates.append(p)
        except Exception:
            pass
    else:
        for base in ["/usr/lib/jvm", "/usr/local/lib/jvm", "/opt/java", "/usr/share/java"]:
            bp = Path(base)
            if bp.exists():
                try:
                    for item in sorted(bp.iterdir(), reverse=True):
                        exe = item / "bin" / "java"
                        if exe.exists():
                            candidates.append(str(exe))
                except PermissionError:
                    pass
        try:
            r = subprocess.run(["which", "java"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                candidates.append(r.stdout.strip())
        except Exception:
            pass

    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    best = None
    best_ver = 0
    for c in unique:
        try:
            r = subprocess.run([c, "-version"], capture_output=True, text=True, timeout=5)
            out = r.stderr + r.stdout
            m = re.search(r'"(\d+)', out)
            if m:
                ver = int(m.group(1))
                if ver >= 17 and (best is None or ver > best_ver):
                    best_ver = ver
                    best = c
        except Exception:
            pass

    if best:
        return best

    if unique:
        return unique[0]

    return "java"


def is_server_running():
    global _server_proc
    with _server_lock:
        if _server_proc is not None:
            if _server_proc.poll() is None:
                return True
            _server_proc = None
    if IS_WINDOWS:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq java.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True
        )
        return "java.exe" in r.stdout
    else:
        r = subprocess.run(["pgrep", "-f", "server.jar"], capture_output=True, text=True)
        return r.returncode == 0


def send_command(cmd):
    global _server_proc
    with _server_lock:
        if _server_proc is not None and _server_proc.poll() is None:
            try:
                stdin = _server_proc.stdin
                if stdin and not stdin.closed:
                    stdin.write((cmd + "\n").encode("utf-8", errors="replace"))
                    stdin.flush()
                    return True
            except Exception:
                pass

    rcon = _read_rcon_config()
    if rcon["enabled"]:
        result = rcon_send(cmd, port=rcon["port"], password=rcon["password"])
        if result is not None:
            return True

    if IS_WINDOWS:
        try:
            wmic = subprocess.run(
                ["wmic", "process", "where",
                 "CommandLine like '%server.jar%' and Name='java.exe'",
                 "get", "ProcessId"],
                capture_output=True, text=True, timeout=5
            )
            pids = [l.strip() for l in wmic.stdout.strip().split("\n")
                    if l.strip().isdigit()]
            if pids:
                pid = pids[0]
                ps_cmd = (
                    f'$proc = Get-Process -Id {pid} -ErrorAction SilentlyContinue; '
                    f'if($proc) {{ '
                    f'$proc.Refresh(); '
                    f'$h = [System.Runtime.InteropServices.Marshal]::GetStdHandle(-10); '
                    f'if($h -ne [IntPtr]::Zero) {{ '
                    f'[System.IO.StreamWriter]::new([System.IO.FileStream]::new($h, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)).WriteLine("{cmd}") '
                    f'}} '
                    f'}}'
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    capture_output=True, timeout=5
                )
                return True
        except Exception:
            pass

    for tool in ["screen", "tmux"]:
        try:
            r = subprocess.run(
                [tool, "-ls"], capture_output=True, text=True, timeout=3
            )
            if r.returncode == 0 and ("Attached" in r.stdout or "Detached" in r.stdout):
                session_name = "mcserv"
                for line in r.stdout.splitlines():
                    for sname in ["mcserv", "mcterm"]:
                        if sname in line:
                            for part in line.split():
                                if sname in part:
                                    session_name = part.split(".")[0] if "." in part else part
                                    break
                            break
                if tool == "screen":
                    subprocess.run(
                        ["screen", "-S", session_name, "-X", "stuff", cmd + "\r"],
                        capture_output=True, timeout=3
                    )
                else:
                    subprocess.run(
                        ["tmux", "send-keys", "-t", session_name, cmd, "Enter"],
                        capture_output=True, timeout=3
                    )
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    try:
        r = subprocess.run(
            ["pgrep", "-f", "server.jar"],
            capture_output=True, text=True, timeout=3
        )
        if r.stdout.strip():
            pid = r.stdout.strip().split("\n")[0]
            fd_path = f"/proc/{pid}/fd/0"
            if os.path.exists(fd_path):
                with open(fd_path, "w") as f:
                    f.write(cmd + "\n")
                    f.flush()
                return True
    except Exception:
        pass
    return False


def start_server():
    global _server_proc
    if is_server_running():
        return "already running"
    if not (MC_DIR / "server.jar").exists():
        return "no server.jar found"

    java_args = os.environ.get("JAVA_ARGS") or _env.get("JAVA_ARGS", "-Xmx2G -Xms1G")
    java_bin = find_java()

    java_ver = 0
    try:
        r = subprocess.run([java_bin, "-version"], capture_output=True, text=True, timeout=5)
        m = re.search(r'"(\d+)', (r.stderr + r.stdout))
        if m:
            java_ver = int(m.group(1))
    except Exception:
        pass

    if java_ver > 0 and java_ver < 17:
        return f"Java {java_ver} found but server requires Java 17+. Set JAVA_PATH in .env to a Java 17+ installation."

    java_cmd = java_bin.split() + java_args.split() + ["-jar", "server.jar", "nogui"]

    if IS_WINDOWS:
        _server_proc = subprocess.Popen(
            java_cmd,
            cwd=str(MC_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
            bufsize=1,
        )
    else:
        _server_proc = subprocess.Popen(
            java_cmd,
            cwd=str(MC_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
            bufsize=1,
        )

    threading.Thread(target=_pipe_output, daemon=True).start()
    return "started"


def _pipe_output():
    global _server_proc
    try:
        proc = _server_proc
        if proc is None or proc.stdout is None:
            return
        MC_DIR.mkdir(parents=True, exist_ok=True)
        log_path = MC_DIR / "logs"
        log_path.mkdir(parents=True, exist_ok=True)
        log_file = log_path / "latest.log"
        forced = _env.get("JAVA_ENCODING", "")
        encodings = [forced] if forced else ["utf-8", "cp1251", "cp1252", "latin-1"]
        with open(log_file, "a", encoding="utf-8", errors="replace") as f:
            for raw_line in iter(proc.stdout.readline, b""):
                if not raw_line:
                    break
                text = None
                for enc in encodings:
                    try:
                        text = raw_line.decode(enc)
                        if not forced and "\ufffd" not in text:
                            break
                    except (UnicodeDecodeError, LookupError):
                        continue
                if text is None:
                    text = raw_line.decode("utf-8", errors="replace")
                f.write(text)
                f.flush()
    except Exception:
        pass


def stop_server():
    if not is_server_running():
        return "not running"

    send_command("stop")
    threading.Thread(target=_wait_stop, daemon=True).start()
    return "stopping"


def _wait_stop():
    global _server_proc
    for _ in range(60):
        time.sleep(1)
        with _server_lock:
            if _server_proc is not None and _server_proc.poll() is not None:
                _server_proc = None
                return
        if not is_server_running():
            with _server_lock:
                _server_proc = None
            return

    with _server_lock:
        if _server_proc is not None:
            try:
                _server_proc.kill()
            except Exception:
                pass
            _server_proc = None

    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "java.exe", "/T"],
                capture_output=True, timeout=5
            )
        except Exception:
            pass
    else:
        subprocess.run(["pkill", "-f", "server.jar"], capture_output=True)


def get_server_info():
    running = is_server_running()
    has_jar = (MC_DIR / "server.jar").exists()
    has_eula = (MC_DIR / "eula.txt").exists()
    info = {"running": running, "has_jar": has_jar, "has_eula": has_eula,
            "players": [], "max_players": 0, "motd": "", "memory": {},
            "tps": "-", "uptime": "-"}

    props_file = MC_DIR / "server.properties"
    if props_file.exists():
        props = read_properties()
        info["max_players"] = int(props.get("max-players", 20))
        info["motd"] = props.get("motd", "")
        info["port"] = int(props.get("server-port", 25565))

    log_file = MC_DIR / "logs" / "latest.log"
    if log_file.exists() and running:
                try:
                    content = log_file.read_text(errors="replace")
                    lines = content.strip().split("\n")
                    for line in reversed(lines):
                        m = re.search(r"There are (\d+) of a max of (\d+) players online", line)
                        if m:
                            info["players_online"] = int(m.group(1))
                            info["max_players"] = int(m.group(2))
                            break
                    for line in reversed(lines):
                        m = re.search(r"Average tick time: ([\d.]+) ms", line)
                        if m:
                            tick = float(m.group(1))
                            info["tps"] = round(1000.0 / tick, 1) if tick > 0 else 20.0
                            break
                        m2 = re.search(r"Mean TPS: ([\d.]+)", line)
                        if m2:
                            info["tps"] = round(float(m2.group(1)), 1)
                            break
                        m3 = re.search(r"TPS from last 5s.*?:\s*([\d.]+)", line)
                        if m3:
                            info["tps"] = round(float(m3.group(1)), 1)
                            break
                        m4 = re.search(r"Mean tick time: ([\d.]+) ms", line)
                        if m4:
                            tick = float(m4.group(1))
                            info["tps"] = round(1000.0 / tick, 1) if tick > 0 else 20.0
                            break
                except Exception:
                    pass

    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                parts = line.split()
                if parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
                    mem[parts[0].rstrip(":")] = int(parts[1]) // 1024
            if "MemTotal" in mem and "MemAvailable" in mem:
                used = mem["MemTotal"] - mem["MemAvailable"]
                info["memory"] = {
                    "total_mb": mem["MemTotal"],
                    "used_mb": used,
                    "free_mb": mem["MemAvailable"]
                }
    except Exception:
        pass

    for key, fname in [("ops_count", "ops.json"),
                       ("whitelist_count", "whitelist.json"),
                       ("bans_count", "banned-players.json")]:
        fpath = MC_DIR / fname
        if fpath.exists():
            try:
                data = json.loads(fpath.read_text())
                info[key] = len(data) if isinstance(data, list) else 0
            except Exception:
                info[key] = 0
        else:
            info[key] = 0

    return info


def get_console_lines(n=100):
    if not LOG_FILE.exists():
        return []
    try:
        content = LOG_FILE.read_text(errors="replace")
        lines = content.strip().split("\n")
        return lines[-n:]
    except Exception:
        return []


def read_json_file(filename):
    fpath = MC_DIR / filename
    if fpath.exists():
        try:
            return json.loads(fpath.read_text())
        except Exception:
            return []
    return []


def write_json_file(filename, data):
    fpath = MC_DIR / filename
    fpath.write_text(json.dumps(data, indent=2))


def read_properties():
    props = {}
    fpath = MC_DIR / "server.properties"
    if fpath.exists():
        for line in fpath.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
    return props


def write_properties(props):
    fpath = MC_DIR / "server.properties"
    lines = ["#Minecraft server properties"]
    for k, v in props.items():
        lines.append(f"{k}={v}")
    tmp = fpath.with_suffix(".properties.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(str(tmp), str(fpath))


def list_plugins():
    d = MC_DIR / "plugins"
    if not d.exists():
        return []
    return [f.name for f in d.iterdir() if f.suffix == ".jar" and f.is_file()]


def list_mods():
    d = MC_DIR / "mods"
    if not d.exists():
        return []
    return [f.name for f in d.iterdir() if f.suffix == ".jar" and f.is_file()]


def fetch_uuid(username):
    import urllib.request
    try:
        url = f"https://api.mojang.com/users/profiles/minecraft/{username}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            raw = data.get("id", "")
            if len(raw) == 32:
                return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
            return raw
    except Exception:
        pass
    name_bytes = ("OfflinePlayer:" + username).encode("UTF-8")
    import hashlib
    md5 = hashlib.md5(name_bytes).digest()
    b = bytearray(md5)
    b[6] = (b[6] & 0x0f) | 0x30
    b[8] = (b[8] & 0x3f) | 0x80
    return "{:08x}-{:04x}-{:04x}-{:04x}-{:012x}".format(
        int.from_bytes(b[0:4], 'big'),
        int.from_bytes(b[4:6], 'big'),
        int.from_bytes(b[6:8], 'big'),
        int.from_bytes(b[8:10], 'big'),
        int.from_bytes(b[10:16], 'big')
    )


def get_online_players():
    if not is_server_running():
        return {"players": []}

    log_file = MC_DIR / "logs" / "latest.log"
    if not log_file.exists():
        return {"players": []}

    try:
        content = log_file.read_text(errors="replace")
        lines = content.strip().split("\n")

        for line in reversed(lines):
            m = re.search(
                r"There are (\d+) of a max of \d+ players online:\s*(.*)",
                line
            )
            if m:
                count = int(m.group(1))
                names_str = m.group(2).strip()
                if count == 0 or not names_str:
                    return {"players": []}
                names = [n.strip() for n in names_str.split(",") if n.strip()]
                players = [{"name": n, "uuid": "-", "ping": 0} for n in names]
                return {"players": players}

        return {"players": []}
    except Exception:
        return {"players": []}


def save_upload(target_dir, filename, data):
    d = MC_DIR / target_dir
    d.mkdir(exist_ok=True)
    fpath = d / filename
    fpath.write_bytes(data)
    return {"name": filename, "size": len(data)}


def delete_file(target_dir, filename):
    fpath = MC_DIR / target_dir / filename
    if fpath.exists() and fpath.is_file():
        fpath.unlink()
        return True
    return False


def delete_dir(target_dir):
    d = MC_DIR / target_dir
    if d.exists() and d.is_dir():
        shutil.rmtree(d)
        return True
    return False


def setup_server():
    eula_path = MC_DIR / "eula.txt"
    if not eula_path.exists():
        eula_path.write_text("eula=true\n")

    if not (MC_DIR / "server.properties").exists():
        props = {
            "level-name": "world",
            "server-port": "25565",
            "max-players": "20",
            "online-mode": "true",
            "gamemode": "survival",
            "difficulty": "normal",
            "pvp": "true",
            "motd": "A Minecraft Server",
            "white-list": "false",
            "view-distance": "10",
            "simulation-distance": "10",
            "spawn-protection": "16",
            "max-world-size": "29999984",
            "allow-flight": "false",
            "hardcore": "false",
            "enable-command-block": "false",
            "spawn-animals": "true",
            "spawn-monsters": "true",
            "spawn-npcs": "true",
            "generate-structures": "true",
            "level-seed": "",
            "level-type": "minecraft\\:normal",
            "server-ip": "",
            "network-compression-threshold": "256",
            "rate-limit": "0",
            "prevent-proxy-connections": "false",
            "use-native-transport": "true",
            "entity-broadcast-range-percentage": "100",
            "sync-chunk-writes": "true",
            "max-tick-time": "60000",
            "player-idle-timeout": "0",
            "allow-nether": "true",
            "enable-rcon": "false",
            "force-gamemode": "false",
            "spawn-npcs": "true",
        }
        write_properties(props)

    MC_DIR.mkdir(parents=True, exist_ok=True)


SETTINGS_FILE = MC_DIR / "panel" / "settings.json"

TRANSLATIONS = {
    "en": {
        "dashboard": "Dashboard", "setup": "Setup", "console": "Console",
        "players": "Players", "files": "Files", "plugins": "Plugins & Mods",
        "settings": "Settings", "properties": "Properties",
        "status": "Status", "online": "Online", "offline": "Offline",
        "memory_used": "Memory Used", "memory_total": "Memory Total",
        "tps": "TPS", "max_players": "Max Players",
        "server_control": "Server Control", "server_info": "Server Info",
        "recent_console": "Recent Console", "live_console": "Live Console",
        "online_players": "Online Players", "player_management": "Player Management",
        "no_players_online": "No players online", "no_players": "No players",
        "add_op": "+ Add OP", "add_whitelist": "+ Whitelist", "add_ban": "+ Ban",
        "ops": "OPS", "whitelist": "WHITELIST", "banned": "BANNED",
        "server_core": "Server Core", "current": "Current",
        "drop_jar": "Drop new .jar here", "replace_core": "Replace the server core",
        "keep_on_replace": "Keep on replace:", "toggle_off_delete": "Toggle OFF to delete",
        "upload_replace": "Upload & Replace", "upload_create": "Upload & Create Server",
        "setup_welcome": "Setup Minecraft Server",
        "setup_desc": "Upload a server .jar file to get started. EULA will be accepted automatically.",
        "drop_jar_here": "Drop server.jar here", "supported": "Spigot, Paper, Purpur, Forge, etc.",
        "quick_actions": "Quick Actions", "start": "Start", "restart": "Restart", "stop": "Stop",
        "server_properties": "server.properties", "save_properties": "Save Properties",
        "server_files": "Server Files", "plugins_tab": "Plugins", "mods_tab": "Mods",
        "drop_jar_files": "Drop .jar files here", "or_browse": "or click to browse",
        "no_plugins": "No plugins installed", "no_mods": "No mods installed",
        "not_found": "(not found)", "settings_saved": "Settings saved",
        "language": "Language", "panel_port": "Panel Port", "mc_directory": "Minecraft Directory",
        "screen_name": "Screen Name", "apply_restart": "Restart panel to apply",
        "enter_name": "Enter a name", "confirm_delete": "Delete",
        "motd": "MOTD", "ops_count": "OPs", "whitelist_count": "Whitelist", "bans_count": "Banned",
        "send": "Send", "enter_command": "Enter command...", "enter_username": "Enter username",
        "add_player": "Add Player", "player_name": "Player Name", "cancel": "Cancel", "add": "Add",
        "edit_env": "Edit .env file to change these values", "no_files": "No files",
        "only_jar": "Only .jar files", "select_file": "Select a file first", "server_restarting": "Server restarting...",
        "confirm_stop": "Stop the server?", "confirm_start": "Start the server?", "confirm_restart": "Restart server?",
        "confirm_delete_file": "Delete", "uploading": "Uploading...", "uploaded": "Uploaded",
        "enter_name": "Enter a name", "saved": "Saved", "removed": "Removed",
        "java_old": "Java 17+ required", "edit_env_hint": "Set JAVA_PATH in .env file",
        "accent_color": "Accent Color", "delete_all": "Delete All", "fireflies": "Fireflies",
        "confirm_delete_all": "Delete all", "confirm_delete": "Delete",
        "confirm_stop": "Stop the server?", "confirm_start": "Start the server?", "confirm_restart": "Restart server?",
        "server_crashed": "Server crashed!",
    },
    "ru": {
        "dashboard": "Главная", "setup": "Настройка", "console": "Консоль",
        "players": "Игроки", "files": "Файлы", "plugins": "Плагины и Моды",
        "settings": "Настройки панели", "properties": "Свойства",
        "status": "Статус", "online": "Онлайн", "offline": "Оффлайн",
        "memory_used": "Память использовано", "memory_total": "Память всего",
        "tps": "TPS", "max_players": "Макс. игроков",
        "server_control": "Управление сервером", "server_info": "Информация",
        "recent_console": "Последние логи", "live_console": "Живая консоль",
        "online_players": "Игроки онлайн", "player_management": "Управление игроками",
        "no_players_online": "Нет игроков онлайн", "no_players": "Нет игроков",
        "add_op": "+ Добавить OP", "add_whitelist": "+ В вайтлист", "add_ban": "+ Забанить",
        "ops": "ОПЕРАТОРЫ", "whitelist": "ВАЙТЛИСТ", "banned": "ЗАБАНЕНЫ",
        "server_core": "Ядро сервера", "current": "Текущее",
        "drop_jar": "Перетащите новый .jar", "replace_core": "Замена ядра сервера",
        "keep_on_replace": "Сохранить при замене:", "toggle_off_delete": "Выкл = удалить",
        "upload_replace": "Загрузить и заменить", "upload_create": "Загрузить и создать сервер",
        "setup_welcome": "Настройка Minecraft сервера",
        "setup_desc": "Загрузите .jar файл сервера для начала. EULA примется автоматически.",
        "drop_jar_here": "Перетащите server.jar сюда", "supported": "Spigot, Paper, Purpur, Forge и др.",
        "quick_actions": "Быстрые действия", "start": "Старт", "restart": "Перезапуск", "stop": "Стоп",
        "server_properties": "server.properties", "save_properties": "Сохранить",
        "server_files": "Файлы сервера", "plugins_tab": "Плагины", "mods_tab": "Моды",
        "drop_jar_files": "Перетащите .jar файлы", "or_browse": "или нажмите для выбора",
        "no_plugins": "Плагинов нет", "no_mods": "Модов нет",
        "not_found": "(не найдено)", "settings_saved": "Настройки сохранены",
        "language": "Язык", "panel_port": "Порт панели", "mc_directory": "Директория Minecraft",
        "screen_name": "Имя screen-сессии", "apply_restart": "Перезапустите панель для применения",
        "enter_name": "Введите имя", "confirm_delete": "Удалить",
        "motd": "MOTD", "ops_count": "Операторы", "whitelist_count": "Вайтлист", "bans_count": "Забанены",
        "send": "Отправить", "enter_command": "Введите команду...", "enter_username": "Введите имя",
        "add_player": "Добавить игрока", "player_name": "Имя игрока", "cancel": "Отмена", "add": "Добавить",
        "edit_env": "Отредактируйте .env для изменения значений", "no_files": "Нет файлов",
        "only_jar": "Только .jar файлы", "select_file": "Выберите файл", "server_restarting": "Сервер перезапускается...",
        "confirm_stop": "Остановить сервер?", "confirm_start": "Запустить сервер?", "confirm_restart": "Перезапустить сервер?",
        "confirm_delete_file": "Удалить", "uploading": "Загрузка...", "uploaded": "Загружено",
        "enter_name": "Введите имя", "saved": "Сохранено", "removed": "Удалено",
        "java_old": "Нужна Java 17+", "edit_env_hint": "Укажите JAVA_PATH в .env",
        "accent_color": "Цвет акцента", "delete_all": "Удалить всё", "fireflies": "Светлячки",
        "confirm_delete_all": "Удалить все", "confirm_delete": "Удалить",
        "confirm_stop": "Остановить сервер?", "confirm_start": "Запустить сервер?", "confirm_restart": "Перезапустить сервер?",
        "server_crashed": "Сервер упал!",
    },
    "de": {
        "dashboard": "Dashboard", "setup": "Einrichtung", "console": "Konsole",
        "players": "Spieler", "files": "Dateien", "plugins": "Plugins & Mods",
        "settings": "Einstellungen", "properties": "Eigenschaften",
        "status": "Status", "online": "Online", "offline": "Offline",
        "memory_used": "Speicher belegt", "memory_total": "Speicher gesamt",
        "tps": "TPS", "max_players": "Max. Spieler",
        "server_control": "Serversteuerung", "server_info": "Serverinfo",
        "recent_console": "Letzte Logs", "live_console": "Live-Konsole",
        "online_players": "Spieler online", "player_management": "Spielerverwaltung",
        "no_players_online": "Keine Spieler online", "no_players": "Keine Spieler",
        "add_op": "+ OP hinzufügen", "add_whitelist": "+ Whitelist", "add_ban": "+ Bannen",
        "ops": "OPERATOREN", "whitelist": "WHITELIST", "banned": "GEBANNT",
        "server_core": "Server-Core", "current": "Aktuell",
        "drop_jar": "Neue .jar hier ablegen", "replace_core": "Server-Core ersetzen",
        "keep_on_replace": "Behalten beim Ersetzen:", "toggle_off_delete": "AUS = löschen",
        "upload_replace": "Hochladen & Ersetzen", "upload_create": "Hochladen & Server erstellen",
        "setup_welcome": "Minecraft Server einrichten",
        "setup_desc": "Laden Sie eine .jar Datei hoch. EULA wird automatisch akzeptiert.",
        "drop_jar_here": "server.jar hier ablegen", "supported": "Spigot, Paper, Purpur, Forge usw.",
        "quick_actions": "Schnellaktionen", "start": "Start", "restart": "Neustart", "stop": "Stopp",
        "server_properties": "server.properties", "save_properties": "Speichern",
        "server_files": "Serverdateien", "plugins_tab": "Plugins", "mods_tab": "Mods",
        "drop_jar_files": "Jar-Dateien hier ablegen", "or_browse": "oder klicken zum Auswählen",
        "no_plugins": "Keine Plugins installiert", "no_mods": "Keine Mods installiert",
        "not_found": "(nicht gefunden)", "settings_saved": "Einstellungen gespeichert",
        "language": "Sprache", "panel_port": "Panel-Port", "mc_directory": "Minecraft-Verzeichnis",
        "screen_name": "Screen-Session-Name", "apply_restart": "Panel neu starten zum Anwenden",
        "enter_name": "Name eingeben", "confirm_delete": "Löschen",
        "motd": "MOTD", "ops_count": "Operatoren", "whitelist_count": "Whitelist", "bans_count": "Gebannt",
        "send": "Senden", "enter_command": "Befehl eingeben...", "enter_username": "Benutzername eingeben",
        "add_player": "Spieler hinzufügen", "player_name": "Spielername", "cancel": "Abbrechen", "add": "Hinzufügen",
        "edit_env": "Bearbeiten Sie .env zum Ändern dieser Werte", "no_files": "Keine Dateien",
        "only_jar": "Nur .jar Dateien", "select_file": "Datei auswählen", "server_restarting": "Server startet neu...",
        "confirm_stop": "Server stoppen?", "confirm_start": "Server starten?", "confirm_restart": "Server neu starten?",
        "confirm_delete_file": "Löschen", "uploading": "Hochladen...", "uploaded": "Hochgeladen",
        "enter_name": "Name eingeben", "saved": "Gespeichert", "removed": "Entfernt",
        "java_old": "Java 17+ benötigt", "edit_env_hint": "JAVA_PATH in .env setzen",
        "accent_color": "Akzentfarbe", "delete_all": "Alle löschen", "fireflies": "Glühwürmchen",
        "confirm_delete_all": "Alle löschen", "confirm_delete": "Löschen",
        "server_crashed": "Server abgestürzt!",
    },
    "fr": {
        "dashboard": "Tableau de bord", "setup": "Configuration", "console": "Console",
        "players": "Joueurs", "files": "Fichiers", "plugins": "Plugins & Mods",
        "settings": "Paramètres", "properties": "Propriétés",
        "status": "Statut", "online": "En ligne", "offline": "Hors ligne",
        "memory_used": "Mémoire utilisée", "memory_total": "Mémoire totale",
        "tps": "TPS", "max_players": "Max joueurs",
        "server_control": "Contrôle du serveur", "server_info": "Info serveur",
        "recent_console": "Console récente", "live_console": "Console en direct",
        "online_players": "Joueurs en ligne", "player_management": "Gestion des joueurs",
        "no_players_online": "Aucun joueur en ligne", "no_players": "Aucun joueur",
        "add_op": "+ Ajouter OP", "add_whitelist": "+ Whitelist", "add_ban": "+ Bannir",
        "ops": "OPS", "whitelist": "WHITELIST", "banned": "BANNIS",
        "server_core": "Noyau serveur", "current": "Actuel",
        "drop_jar": "Déposez un nouveau .jar ici", "replace_core": "Remplacer le noyau",
        "keep_on_replace": "Conserver lors du remplacement :", "toggle_off_delete": "Désactiver = supprimer",
        "upload_replace": "Télécharger et remplacer", "upload_create": "Télécharger et créer le serveur",
        "setup_welcome": "Configurer le serveur Minecraft",
        "setup_desc": "Téléchargez un fichier .jar pour commencer. L'EULA sera acceptée automatiquement.",
        "drop_jar_here": "Déposez server.jar ici", "supported": "Spigot, Paper, Purpur, Forge, etc.",
        "quick_actions": "Actions rapides", "start": "Démarrer", "restart": "Redémarrer", "stop": "Arrêter",
        "server_properties": "server.properties", "save_properties": "Enregistrer",
        "server_files": "Fichiers serveur", "plugins_tab": "Plugins", "mods_tab": "Mods",
        "drop_jar_files": "Déposez des fichiers .jar ici", "or_browse": "ou cliquez pour parcourir",
        "no_plugins": "Aucun plugin installé", "no_mods": "Aucun mod installé",
        "not_found": "(introuvable)", "settings_saved": "Paramètres enregistrés",
        "language": "Langue", "panel_port": "Port du panneau", "mc_directory": "Répertoire Minecraft",
        "apply_restart": "Redémarrez le panneau pour appliquer",
        "enter_name": "Entrez un nom", "confirm_delete": "Supprimer",
        "motd": "MOTD", "ops_count": "OPs", "whitelist_count": "Whitelist", "bans_count": "Bannis",
        "send": "Envoyer", "enter_command": "Entrez une commande...", "enter_username": "Entrez le nom",
        "add_player": "Ajouter un joueur", "player_name": "Nom du joueur", "cancel": "Annuler", "add": "Ajouter",
        "no_files": "Aucun fichier",
        "confirm_stop": "Arrêter le serveur ?", "confirm_start": "Démarrer le serveur ?", "confirm_restart": "Redémarrer le serveur ?",
        "server_crashed": "Serveur en panne !",
    },
    "zh": {
        "dashboard": "仪表盘", "setup": "设置", "console": "控制台",
        "players": "玩家", "files": "文件", "plugins": "插件和Mod",
        "settings": "面板设置", "properties": "属性",
        "status": "状态", "online": "在线", "offline": "离线",
        "memory_used": "已用内存", "memory_total": "总内存",
        "tps": "TPS", "max_players": "最大玩家数",
        "server_control": "服务器控制", "server_info": "服务器信息",
        "recent_console": "最近日志", "live_console": "实时控制台",
        "online_players": "在线玩家", "player_management": "玩家管理",
        "no_players_online": "没有玩家在线", "no_players": "没有玩家",
        "add_op": "+ 添加OP", "add_whitelist": "+ 白名单", "add_ban": "+ 封禁",
        "ops": "管理员", "whitelist": "白名单", "banned": "封禁列表",
        "server_core": "服务器核心", "current": "当前",
        "drop_jar": "拖放新的.jar文件", "replace_core": "替换服务器核心",
        "keep_on_replace": "替换时保留:", "toggle_off_delete": "关闭 = 删除",
        "upload_replace": "上传并替换", "upload_create": "上传并创建服务器",
        "setup_welcome": "设置Minecraft服务器",
        "setup_desc": "上传.jar文件开始。EULA将自动接受。",
        "drop_jar_here": "拖放server.jar到此处", "supported": "Spigot, Paper, Purpur, Forge等",
        "quick_actions": "快捷操作", "start": "启动", "restart": "重启", "stop": "停止",
        "server_properties": "server.properties", "save_properties": "保存",
        "server_files": "服务器文件", "plugins_tab": "插件", "mods_tab": "Mod",
        "drop_jar_files": "拖放.jar文件到此处", "or_browse": "或点击浏览",
        "no_plugins": "没有安装插件", "no_mods": "没有安装Mod",
        "not_found": "(未找到)", "settings_saved": "设置已保存",
        "language": "语言", "panel_port": "面板端口", "mc_directory": "Minecraft目录",
        "apply_restart": "重启面板以生效",
        "enter_name": "输入名称", "confirm_delete": "删除",
        "motd": "MOTD", "ops_count": "管理员", "whitelist_count": "白名单", "bans_count": "封禁",
        "send": "发送", "enter_command": "输入命令...", "enter_username": "输入用户名",
        "add_player": "添加玩家", "player_name": "玩家名", "cancel": "取消", "add": "添加",
        "no_files": "没有文件",
        "confirm_stop": "停止服务器？", "confirm_start": "启动服务器？", "confirm_restart": "重启服务器？",
        "server_crashed": "服务器崩溃了！",
    }
}


def load_settings():
    defaults = {"lang": _env.get("PANEL_LANG", "en"), "accent": _env.get("ACCENT_COLOR", "#6c5ce7"), "fireflies": False}
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text())
            defaults.update(data)
        except Exception:
            pass
    return defaults


def save_settings(data):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def parse_multipart(handler):
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        return None
    boundary = ""
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[9:].strip('"')
    if not boundary:
        return None
    length = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(length)
    sep = ("--" + boundary).encode()
    end = ("--" + boundary + "--").encode()
    parts = body.split(sep)
    fields = {}
    for part in parts:
        if not part or part == b"--" or part.strip() == b"":
            continue
        if part.endswith(end):
            part = part[:-len(end)]
        header_end = part.find(b"\r\n\r\n")
        if header_end < 0:
            continue
        headers_raw = part[:header_end].decode("utf-8", errors="replace")
        data = part[header_end + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]
        name = ""
        filename = None
        for line in headers_raw.split("\r\n"):
            if "Content-Disposition:" in line:
                for token in line.split(";"):
                    token = token.strip()
                    if token.startswith("name="):
                        name = token.split("=", 1)[1].strip('"')
                    if token.startswith("filename="):
                        filename = token.split("=", 1)[1].strip('"')
        if filename:
            fields[name] = type("UploadFile", (), {"filename": filename, "file": io.BytesIO(data)})()
        elif name:
            fields[name] = type("UploadField", (), {"value": data.decode("utf-8", errors="replace")})()
    return fields


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet">
<title>FizMine Panel</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0b0e14;--surface:#151922;--surface2:#1c2130;--surface3:#222838;
--accent:#6c5ce7;--accent2:#a855f7;--accent3:#7c3aed;
--text:#e2e8f0;--text2:#8892a4;--green:#22c55e;--red:#ef4444;--yellow:#eab308;--blue:#3b82f6;--cyan:#06b6d4;
--border:#2a2f3e;--border2:#1e2330;
--glow:0 0 20px rgba(108,92,231,.15);--logo-color:#fff;--accent-rgb:108,92,231}
body{font-family:'Inter','Segoe UI',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden}
::selection{background:var(--accent);color:#fff}
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:var(--text2)}

.sidebar{position:fixed;left:0;top:0;bottom:0;width:240px;background:var(--surface);border-right:1px solid var(--border);z-index:100;display:flex;flex-direction:column;backdrop-filter:blur(20px)}
.sidebar .logo{padding:24px 20px;text-align:center;border-bottom:1px solid var(--border)}
.sidebar .logo-icon{width:56px;height:56px;background:linear-gradient(135deg,var(--accent),var(--accent2));border-radius:14px;display:flex;align-items:center;justify-content:center;margin:0 auto 14px;box-shadow:0 4px 24px rgba(var(--accent-rgb),.35);transition:all .4s cubic-bezier(.16,1,.3,1);cursor:pointer}
.sidebar .logo-icon:hover{transform:scale(1.08) rotate(-3deg);box-shadow:0 6px 32px rgba(var(--accent-rgb),.5)}
.sidebar .logo-icon svg{width:34px;height:34px;fill:var(--logo-color);transition:fill .3s}
.sidebar .logo h1{font-family:'Press Start 2P',monospace;font-size:10px;font-weight:400;background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;letter-spacing:0;line-height:1.6}
.sidebar .logo span{font-size:10px;color:var(--text2);letter-spacing:.5px;font-family:'Press Start 2P',monospace}
.sidebar nav{flex:1;padding:16px 12px}
.sidebar nav a{display:flex;align-items:center;gap:12px;padding:11px 16px;color:var(--text2);transition:all .25s cubic-bezier(.16,1,.3,1);border-radius:10px;font-size:13.5px;font-weight:500;margin-bottom:2px;position:relative;overflow:hidden}
.sidebar nav a::before{content:'';position:absolute;inset:0;background:linear-gradient(135deg,var(--accent),var(--accent2));opacity:0;transition:opacity .25s;border-radius:10px}
.sidebar nav a:hover{color:var(--text);transform:translateX(4px)}
.sidebar nav a:hover::before{opacity:.06}
.sidebar nav a.active{color:var(--accent);box-shadow:inset 0 0 0 1px rgba(var(--accent-rgb),.25);transform:translateX(4px)}
.sidebar nav a.active::before{opacity:.1}
.sidebar nav a .icon{width:20px;text-align:center;opacity:.6;transition:all .25s;display:flex;align-items:center;justify-content:center}
.sidebar nav a .icon svg{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.sidebar nav a:hover .icon,.sidebar nav a.active .icon{opacity:1;transform:scale(1.1)}

.main{margin-left:240px;padding:28px 32px}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:28px}
.header h2{font-size:22px;font-weight:700;letter-spacing:-.3px;transition:color .3s}

.status-bar{display:flex;gap:14px;margin-bottom:24px;flex-wrap:wrap}
.status-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px 22px;flex:1;min-width:155px;position:relative;overflow:hidden;transition:all .35s cubic-bezier(.16,1,.3,1)}
.status-card:hover{border-color:rgba(var(--accent-rgb),.3);box-shadow:var(--glow);transform:translateY(-3px)}
.status-card .label{font-size:11px;color:var(--text2);margin-bottom:6px;text-transform:uppercase;letter-spacing:.8px;font-weight:600}
.status-card .value{font-size:24px;font-weight:800;letter-spacing:-.5px;transition:color .3s}
.status-card .value.green{color:var(--green)}
.status-card .value.red{color:var(--red)}
.status-card .value.blue{color:var(--blue)}
.status-card .value.yellow{color:var(--yellow)}
.status-card .value.cyan{color:var(--cyan)}

.btn{padding:9px 20px;border:none;border-radius:10px;cursor:pointer;font-size:13px;font-weight:600;transition:all .25s cubic-bezier(.16,1,.3,1);display:inline-flex;align-items:center;gap:7px;letter-spacing:-.2px;position:relative;overflow:hidden}
.btn:hover{transform:translateY(-2px);filter:brightness(1.1);box-shadow:0 4px 16px rgba(0,0,0,.2)}
.btn:active{transform:translateY(0);filter:brightness(.95)}
.btn-green{background:var(--green);color:#000}
.btn-red{background:var(--red);color:#fff}
.btn-blue{background:var(--blue);color:#fff}
.btn-yellow{background:var(--yellow);color:#000}
.btn-outline{background:transparent;border:1px solid var(--border);color:var(--text)}
.btn-outline:hover{border-color:var(--accent);color:var(--accent);background:rgba(var(--accent-rgb),.06)}
.btn-accent{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff}
.btn-sm{padding:6px 14px;font-size:12px;border-radius:8px}
.btn:disabled{opacity:.4;cursor:not-allowed;transform:none!important;filter:none!important}

.panel{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:22px;margin-bottom:20px;transition:all .35s cubic-bezier(.16,1,.3,1);position:relative;z-index:2}
.panel:hover{border-color:rgba(var(--accent-rgb),.15);box-shadow:0 4px 20px rgba(0,0,0,.15)}
.panel h3{font-size:15px;margin-bottom:14px;display:flex;align-items:center;gap:10px;font-weight:600}
.panel h3 .dot{width:8px;height:8px;border-radius:50%;display:inline-block;transition:background .3s}

.console-box{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px;font-family:'JetBrains Mono','Cascadia Code','Fira Code',monospace;font-size:12px;height:420px;overflow-y:auto;line-height:1.7;word-wrap:break-word}
.console-box .line{white-space:pre-wrap}
.console-box .line.info{color:#94a3b8}
.console-box .line.warn{color:var(--yellow)}
.console-box .line.error{color:var(--red)}
.console-box .line.chat{color:var(--green)}

.cmd-input{display:flex;gap:10px;margin-top:10px}
.cmd-input input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:11px 16px;color:var(--text);font-family:monospace;font-size:13px;outline:none;transition:all .25s}
.cmd-input input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(var(--accent-rgb),.15)}

table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:11px 14px;border-bottom:1px solid var(--border);font-size:13px}
th{color:var(--text2);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.8px}
tr{transition:background .2s}
tr:hover{background:rgba(var(--accent-rgb),.04)}

.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:11px;color:var(--text2);margin-bottom:6px;text-transform:uppercase;letter-spacing:.8px;font-weight:600}
.form-group input,.form-group select,.form-group textarea{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px 14px;color:var(--text);font-size:13px;outline:none;font-family:inherit;transition:all .25s}
.form-group input:focus,.form-group select:focus,.form-group textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(var(--accent-rgb),.12)}
.form-group textarea{min-height:60px;resize:vertical;font-family:monospace}

.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.grid-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}

.drop-zone{border:2px dashed var(--border);border-radius:14px;padding:36px;text-align:center;cursor:pointer;transition:all .35s cubic-bezier(.16,1,.3,1);margin-bottom:16px;position:relative;overflow:hidden}
.drop-zone:hover,.drop-zone.dragover{border-color:var(--accent);background:rgba(var(--accent-rgb),.05);box-shadow:0 0 30px rgba(var(--accent-rgb),.1)}
.drop-zone.dragover{border-style:solid;transform:scale(1.02)}
.drop-zone .drop-icon{font-size:36px;margin-bottom:10px;opacity:.5;transition:all .3s}
.drop-zone:hover .drop-icon{opacity:.8;transform:translateY(-4px)}
.drop-zone .drop-text{font-size:14px;color:var(--text2);margin-bottom:4px}
.drop-zone .drop-hint{font-size:11px;color:var(--text2);opacity:.6}
.drop-zone input[type=file]{display:none}

.plugin-item,.mod-item{display:flex;align-items:center;justify-content:space-between;padding:11px 16px;background:var(--bg);border:1px solid var(--border);border-radius:10px;margin-bottom:8px;font-size:13px;transition:all .25s}
.plugin-item:hover,.mod-item:hover{border-color:rgba(var(--accent-rgb),.3);transform:translateX(4px)}
.plugin-item .name,.mod-item .name{font-weight:600;font-family:monospace;font-size:12px}

.empty{color:var(--text2);text-align:center;padding:30px;font-size:13px}

.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,0);backdrop-filter:blur(0);z-index:200;display:flex;align-items:center;justify-content:center;opacity:0;pointer-events:none;transition:all .3s cubic-bezier(.16,1,.3,1)}
.modal-overlay.active{opacity:1;pointer-events:auto;background:rgba(0,0,0,.7);backdrop-filter:blur(4px)}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:28px;width:420px;max-width:90vw;box-shadow:0 25px 60px rgba(0,0,0,.4);transform:scale(.9) translateY(20px);opacity:0;transition:all .35s cubic-bezier(.16,1,.3,1)}
.modal-overlay.active .modal{transform:scale(1) translateY(0);opacity:1}
.modal h3{margin-bottom:20px;font-size:17px}

.toast{position:fixed;bottom:28px;right:28px;background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:14px 22px;font-size:13px;font-weight:500;z-index:300;animation:slideIn .4s cubic-bezier(.16,1,.3,1);box-shadow:0 10px 40px rgba(0,0,0,.3)}
@keyframes slideIn{from{transform:translateY(20px) scale(.95);opacity:0}to{transform:translateY(0) scale(1);opacity:1}}
@keyframes slideOut{to{transform:translateY(20px) scale(.95);opacity:0}}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.05)}}

.player-row{display:flex;align-items:center;gap:14px;padding:12px 16px;background:var(--bg);border:1px solid var(--border);border-radius:12px;margin-bottom:8px;transition:all .25s}
.player-row:hover{border-color:rgba(var(--accent-rgb),.3);transform:translateX(4px)}
.player-head{width:36px;height:36px;border-radius:8px;background:var(--surface2);image-rendering:pixelated;flex-shrink:0}
.player-info{flex:1;min-width:0}
.player-name{font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.player-uuid{font-size:11px;color:var(--text2);font-family:monospace}
.player-ping{font-size:12px;font-weight:600;padding:4px 10px;border-radius:6px;white-space:nowrap}
.ping-good{color:var(--green);background:rgba(34,197,94,.1)}
.ping-mid{color:var(--yellow);background:rgba(234,179,8,.1)}
.ping-bad{color:var(--red);background:rgba(239,68,68,.1)}
.ping-none{color:var(--text2);background:var(--surface2)}

.online-section{margin-bottom:20px}
.online-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.online-count{font-size:13px;color:var(--text2)}
.online-count span{color:var(--green);font-weight:700}

.toggle-row{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--border);font-size:13px;transition:all .2s}
.toggle-row:last-child{border-bottom:none}
.toggle-row:hover{padding-left:4px}
.toggle-row .toggle-label{display:flex;flex-direction:column;gap:2px}
.toggle-row .toggle-label span{color:var(--text2);font-size:11px}
.toggle{position:relative;width:42px;height:22px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.toggle .slider{position:absolute;inset:0;background:var(--surface3);border:1px solid var(--border);border-radius:22px;cursor:pointer;transition:all .3s cubic-bezier(.16,1,.3,1)}
.toggle .slider::before{content:'';position:absolute;width:16px;height:16px;left:2px;top:2px;background:var(--text2);border-radius:50%;transition:all .3s cubic-bezier(.16,1,.3,1)}
.toggle input:checked+.slider{background:var(--accent);border-color:var(--accent)}
.toggle input:checked+.slider::before{transform:translateX(20px);background:#fff}
.toggle input:disabled+.slider{opacity:.3;cursor:not-allowed}

.setup-card{background:var(--surface);border:2px dashed var(--border);border-radius:16px;padding:40px;text-align:center;margin-bottom:20px}
.setup-card h3{font-size:18px;margin-bottom:10px}
.setup-card p{color:var(--text2);font-size:13px;margin-bottom:20px}

.color-grid{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.color-swatch{width:36px;height:36px;border-radius:10px;cursor:pointer;border:2px solid transparent;transition:all .25s cubic-bezier(.16,1,.3,1)}
.color-swatch:hover{transform:scale(1.15);box-shadow:0 4px 12px rgba(0,0,0,.3)}
.color-swatch.active{border-color:var(--text);box-shadow:0 0 12px rgba(255,255,255,.2)}
.color-input{display:flex;align-items:center;gap:8px}
.color-input input[type=color]{width:40px;height:34px;border:none;border-radius:8px;cursor:pointer;background:transparent;padding:0}
.color-input input[type=color]::-webkit-color-swatch-wrapper{padding:2px}
.color-input input[type=color]::-webkit-color-swatch{border-radius:6px;border:none}

.custom-select{position:relative;width:100%;cursor:pointer}
.custom-select-selected{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px 14px;color:var(--text);font-size:13px;display:flex;align-items:center;justify-content:space-between;transition:all .25s}
.custom-select-selected::after{content:'';width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-top:5px solid var(--text2);transition:transform .2s;margin-left:8px}
.custom-select.open .custom-select-selected{border-color:var(--accent);box-shadow:0 0 0 3px rgba(var(--accent-rgb),.12)}
.custom-select.open .custom-select-selected::after{transform:rotate(180deg)}
.custom-select-options{position:absolute;top:100%;left:0;right:0;background:var(--surface);border:1px solid var(--border);border-radius:10px;margin-top:4px;overflow:hidden;z-index:10;opacity:0;pointer-events:none;transform:translateY(-8px);transition:all .25s cubic-bezier(.16,1,.3,1);box-shadow:0 10px 30px rgba(0,0,0,.3)}
.custom-select.open .custom-select-options{opacity:1;pointer-events:auto;transform:translateY(0)}
.custom-select-option{padding:10px 14px;font-size:13px;color:var(--text);cursor:pointer;transition:all .15s}
.custom-select-option:hover{background:rgba(var(--accent-rgb),.1);color:var(--accent)}
.custom-select-option.selected{background:rgba(var(--accent-rgb),.15);color:var(--accent);font-weight:600}

.tab-enter{animation:fadeIn .3s cubic-bezier(.16,1,.3,1)}

#fireflies-canvas{position:fixed;inset:0;z-index:0;pointer-events:none;opacity:0;transition:opacity 1s}
#fireflies-canvas.active{opacity:1}
#fireflies-bg{position:fixed;width:120%;height:120%;top:-10%;left:-10%;z-index:-1;pointer-events:none;opacity:0;transition:opacity 1.5s;background:radial-gradient(ellipse at 25% 40%,rgba(var(--accent-rgb),.07) 0%,transparent 45%),radial-gradient(ellipse at 75% 70%,rgba(var(--accent-rgb),.06) 0%,transparent 40%);animation:rotateBg 40s linear infinite}
#fireflies-bg.active{opacity:1}
@keyframes rotateBg{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
.main{position:relative;z-index:1}
.sidebar{position:fixed;z-index:10}

@media(max-width:768px){
.sidebar{width:64px}.sidebar .logo h1,.sidebar .logo span,.sidebar nav a span{display:none}
.sidebar nav a{justify-content:center;padding:12px}
.main{margin-left:64px;padding:16px}
.grid-2,.grid-3{grid-template-columns:1fr}
.status-bar{flex-direction:column}
}
</style>
</head>
<body>
<canvas id="fireflies-canvas" style="display:none"></canvas>
<div id="fireflies-bg"></div>

<div class="sidebar">
 <div class="logo">
  <div class="logo-icon">
   <svg viewBox="0 0 28 22" shape-rendering="crispEdges" style="width:40px;height:32px">
    <g fill="rgba(0,0,0,.25)" transform="translate(1.5,1.5)">
     <rect x="1" y="2" width="3" height="17"/>
     <rect x="4" y="2" width="7" height="3"/>
     <rect x="4" y="9" width="5" height="3"/>
     <rect x="13" y="2" width="3" height="17"/>
     <rect x="16" y="5" width="2" height="3"/>
     <rect x="18" y="8" width="2" height="3"/>
     <rect x="20" y="5" width="2" height="3"/>
     <rect x="22" y="2" width="3" height="17"/>
    </g>
    <g fill="currentColor">
     <rect x="1" y="1" width="3" height="17"/>
     <rect x="4" y="1" width="7" height="3"/>
     <rect x="4" y="8" width="5" height="3"/>
     <rect x="13" y="1" width="3" height="17"/>
     <rect x="16" y="4" width="2" height="3"/>
     <rect x="18" y="7" width="2" height="3"/>
     <rect x="20" y="4" width="2" height="3"/>
     <rect x="22" y="1" width="3" height="17"/>
    </g>
    <g fill="rgba(255,255,255,.15)">
     <rect x="1" y="1" width="3" height="2"/>
     <rect x="4" y="1" width="7" height="1"/>
     <rect x="13" y="1" width="3" height="2"/>
     <rect x="22" y="1" width="3" height="2"/>
    </g>
   </svg>
  </div>
  <h1>FizMine Panel</h1>
  <div style="display:flex;align-items:center;justify-content:center;gap:6px;margin-top:4px">
   <span style="font-family:'Press Start 2P',monospace;font-size:7px;color:var(--text2);letter-spacing:.5px">Minecraft Server</span>
   <img src="https://static.wikia.nocookie.net/minecraft_gamepedia/images/c/c7/Grass_Block.png" alt="grass" onclick="playOof()" style="width:18px;height:18px;cursor:pointer;transition:transform .15s;image-rendering:pixelated;filter:drop-shadow(0 1px 2px rgba(0,0,0,.3));flex-shrink:0" onmouseover="this.style.transform='scale(1.3)'" onmouseout="this.style.transform='scale(1)'" onerror="this.outerHTML='<svg onclick=\\'playOof()\\' viewBox=\\'0 0 16 16\\' shape-rendering=\\'crispEdges\\' style=\\'width:16px;height:16px;cursor:pointer;flex-shrink:0\\'><rect x=\\'0\\' y=\\'0\\' width=\\'16\\' height=\\'4\\' fill=\\'#5a9e3e\\'/><rect x=\\'0\\' y=\\'4\\' width=\\'16\\' height=\\'12\\' fill=\\'#8b6b3e\\'/></svg>'">
  </div>
 </div>
 <nav>
   <a href="#" onclick="showTab('dashboard')" id="nav-dashboard" class="active"><span class="icon"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg></span><span data-i18n="dashboard">Dashboard</span></a>
   <a href="#" onclick="showTab('setup')" id="nav-setup"><span class="icon"><svg viewBox="0 0 24 24"><path d="M12 15V3m0 12l-4-4m4 4l4-4"/><path d="M2 17l.621 2.485A2 2 0 0 0 4.561 21h14.878a2 2 0 0 0 1.94-1.515L22 17"/></svg></span><span data-i18n="setup">Setup</span></a>
   <a href="#" onclick="showTab('console')" id="nav-console"><span class="icon"><svg viewBox="0 0 24 24"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg></span><span data-i18n="console">Console</span></a>
   <a href="#" onclick="showTab('players')" id="nav-players"><span class="icon"><svg viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg></span><span data-i18n="players">Players</span></a>
   <a href="#" onclick="showTab('files')" id="nav-files"><span class="icon"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg></span><span data-i18n="properties">Properties</span></a>
   <a href="#" onclick="showTab('filebrowser')" id="nav-filebrowser"><span class="icon"><svg viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg></span><span data-i18n="files">Files</span></a>
   <a href="#" onclick="showTab('plugins')" id="nav-plugins"><span class="icon"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/></svg></span><span data-i18n="plugins">Plugins & Mods</span></a>
   <a href="#" onclick="showTab('settings')" id="nav-settings"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></span><span data-i18n="settings">Settings</span></a>
  </nav>
</div>

<div class="main">
 <div class="header">
  <h2 id="page-title" data-i18n="dashboard">Dashboard</h2>
  <div id="header-actions"></div>
 </div>

 <div id="tab-dashboard">
  <div class="status-bar" id="status-bar"></div>
  <div class="grid-2">
   <div class="panel">
    <h3><span class="dot" id="status-dot"></span> <span data-i18n="server_control">Server Control</span></h3>
    <div style="display:flex;gap:10px;flex-wrap:wrap" id="control-btns"></div>
   </div>
   <div class="panel">
    <h3>&#128202; <span data-i18n="server_info">Server Info</span></h3>
    <div id="quick-info"></div>
   </div>
  </div>
  <div class="panel">
   <h3>&#128196; <span data-i18n="recent_console">Recent Console</span></h3>
   <div class="console-box" id="dash-console"></div>
  </div>
 </div>

 <div id="tab-setup" style="display:none">
  <div id="setup-view"></div>
 </div>

 <div id="tab-console" style="display:none">
  <div class="panel">
   <h3>&#128196; <span data-i18n="live_console">Live Console</span></h3>
   <div class="console-box" id="live-console"></div>
   <div class="cmd-input">
    <input type="text" id="cmd-input" data-i18n-ph="enter_command" placeholder="Enter command..." onkeydown="if(event.key==='Enter')sendCmd()">
    <button class="btn btn-accent" onclick="sendCmd()">&#9654; <span data-i18n="send">Send</span></button>
   </div>
  </div>
 </div>

 <div id="tab-players" style="display:none">
  <div class="online-section">
   <div class="online-header">
    <h3>&#9823; <span data-i18n="online_players">Online Players</span></h3>
    <div class="online-count"><span data-i18n="online">Online</span>: <span id="online-count">0</span></div>
   </div>
   <div id="online-players"></div>
  </div>
  <div class="grid-3" style="margin-bottom:20px">
   <div class="panel" style="text-align:center">
    <div style="color:var(--text2);font-size:11px;margin-bottom:4px;text-transform:uppercase;letter-spacing:.8px;font-weight:600"><span data-i18n="ops">OPS</span></div>
    <div style="font-size:28px;font-weight:800" id="ops-count">0</div>
   </div>
   <div class="panel" style="text-align:center">
    <div style="color:var(--text2);font-size:11px;margin-bottom:4px;text-transform:uppercase;letter-spacing:.8px;font-weight:600"><span data-i18n="whitelist">WHITELIST</span></div>
    <div style="font-size:28px;font-weight:800;color:var(--blue)" id="wl-count">0</div>
   </div>
   <div class="panel" style="text-align:center">
    <div style="color:var(--text2);font-size:11px;margin-bottom:4px;text-transform:uppercase;letter-spacing:.8px;font-weight:600"><span data-i18n="banned">BANNED</span></div>
    <div style="font-size:28px;font-weight:800;color:var(--red)" id="bans-count">0</div>
   </div>
  </div>
  <div class="panel">
   <h3>&#9823; <span data-i18n="player_management">Player Management</span></h3>
   <div style="display:flex;gap:10px;margin-bottom:18px;flex-wrap:wrap">
    <button class="btn btn-blue btn-sm" onclick="showPlayerModal('ops')"><span data-i18n="add_op">+ Add OP</span></button>
    <button class="btn btn-green btn-sm" onclick="showPlayerModal('whitelist')"><span data-i18n="add_whitelist">+ Whitelist</span></button>
    <button class="btn btn-red btn-sm" onclick="showPlayerModal('ban')"><span data-i18n="add_ban">+ Ban</span></button>
   </div>
   <div id="player-tabs" style="display:flex;gap:6px;margin-bottom:14px">
    <button class="btn btn-sm btn-outline active" onclick="showPlayerList('ops',this)"><span data-i18n="ops">OPs</span></button>
    <button class="btn btn-sm btn-outline" onclick="showPlayerList('whitelist',this)"><span data-i18n="whitelist">Whitelist</span></button>
    <button class="btn btn-sm btn-outline" onclick="showPlayerList('banned',this)"><span data-i18n="banned">Banned</span></button>
   </div>
   <div id="player-list"></div>
  </div>
 </div>

 <div id="tab-files" style="display:none">
  <div class="panel">
   <h3>&#9998; server.properties</h3>
   <div id="props-form"></div>
   <button class="btn btn-accent" onclick="saveProperties()" style="margin-top:14px"><span data-i18n="save_properties">Save Properties</span></button>
  </div>
 </div>

 <div id="tab-filebrowser" style="display:none">
  <div class="panel">
   <h3>&#128193; <span data-i18n="files">Files</span></h3>
   <div id="file-breadcrumb" style="margin-bottom:12px;font-size:13px;color:var(--text2)"></div>
   <div id="file-list"></div>
  </div>
 </div>

 <div class="modal-overlay" id="file-editor-overlay" onclick="if(event.target===this)closeFileEditor()">
  <div class="modal" style="width:700px;max-width:90vw;max-height:80vh;display:flex;flex-direction:column">
   <h3 id="file-editor-title" style="margin-bottom:12px">Edit File</h3>
   <textarea id="file-editor-content" style="flex:1;min-height:400px;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px;color:var(--text);font-family:'JetBrains Mono','Cascadia Code',monospace;font-size:13px;resize:none;outline:none;tab-size:2"></textarea>
   <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
    <button class="btn btn-outline" onclick="closeFileEditor()"><span data-i18n="cancel">Cancel</span></button>
    <button class="btn btn-accent" onclick="saveFileEdit()"><span data-i18n="save_properties">Save</span></button>
   </div>
  </div>
 </div>

 <div class="modal-overlay" id="confirm-overlay" onclick="if(event.target===this)confirmCancel()">
  <div class="modal" style="width:380px">
   <h3 id="confirm-title" style="margin-bottom:16px">Confirm</h3>
   <p id="confirm-message" style="color:var(--text2);font-size:14px;margin-bottom:24px;line-height:1.5"></p>
   <div style="display:flex;gap:10px;justify-content:flex-end">
    <button class="btn btn-outline" onclick="confirmCancel()"><span data-i18n="cancel">Cancel</span></button>
    <button class="btn btn-red" id="confirm-ok-btn" onclick="confirmOk()">Confirm</button>
   </div>
  </div>
 </div>

 <div id="tab-plugins" style="display:none">
  <div class="grid-2">
   <div class="panel">
    <h3>&#10070; <span data-i18n="plugins_tab">Plugins</span></h3>
    <div class="drop-zone" id="drop-plugins" ondragover="handleDrag(event,this)" ondragleave="handleDragLeave(this)" ondrop="handleDrop(event,'plugins',this)" onclick="this.querySelector('input').click()">
     <input type="file" accept=".jar" onchange="uploadFile('plugins',this.files[0]);this.value=''">
     <div class="drop-icon">&#128228;</div>
     <div class="drop-text" data-i18n="drop_jar_files">Drop .jar files here</div>
     <div class="drop-hint" data-i18n="or_browse">or click to browse</div>
    </div>
    <div id="plugins-list"></div>
   </div>
   <div class="panel">
    <h3>&#10070; <span data-i18n="mods_tab">Mods</span></h3>
    <div class="drop-zone" id="drop-mods" ondragover="handleDrag(event,this)" ondragleave="handleDragLeave(this)" ondrop="handleDrop(event,'mods',this)" onclick="this.querySelector('input').click()">
     <input type="file" accept=".jar" onchange="uploadFile('mods',this.files[0]);this.value=''">
     <div class="drop-icon">&#128228;</div>
     <div class="drop-text" data-i18n="drop_jar_files">Drop .jar files here</div>
     <div class="drop-hint" data-i18n="or_browse">or click to browse</div>
    </div>
    <div id="mods-list"></div>
   </div>
  </div>
 </div>

 <div id="tab-settings" style="display:none">
  <div class="grid-2">
   <div class="panel">
    <h3><svg viewBox="0 0 24 24" style="width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.8"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg> <span data-i18n="settings">Settings</span></h3>
     <div class="form-group">
      <label data-i18n="language">Language</label>
      <div class="custom-select" id="lang-select" onclick="toggleCustomSelect(this)">
       <div class="custom-select-selected" id="lang-selected">English</div>
       <div class="custom-select-options" id="lang-options">
        <div class="custom-select-option" data-value="en" onclick="selectLang('en','English')">English</div>
        <div class="custom-select-option" data-value="ru" onclick="selectLang('ru','Русский')">Русский</div>
        <div class="custom-select-option" data-value="de" onclick="selectLang('de','Deutsch')">Deutsch</div>
        <div class="custom-select-option" data-value="fr" onclick="selectLang('fr','Français')">Français</div>
        <div class="custom-select-option" data-value="zh" onclick="selectLang('zh','中文')">中文</div>
       </div>
      </div>
     </div>
    <div class="form-group">
     <label>MC_DIR</label>
     <input type="text" id="set-mcdir">
    </div>
    <div class="form-group">
     <label>PANEL_PORT</label>
     <input type="text" id="set-port">
    </div>
    <div class="form-group">
     <label>JAVA_ARGS</label>
     <input type="text" id="set-javaargs" placeholder="-Xmx2G -Xms1G">
    </div>
    <div class="form-group">
     <label>JAVA_ENCODING</label>
     <input type="text" id="set-encoding" placeholder="auto">
    </div>
    <button class="btn btn-accent" onclick="saveSettings()"><span data-i18n="save_properties">Save</span></button>
    <p style="color:var(--text2);font-size:12px;margin-top:8px" data-i18n="apply_restart">Restart panel to apply</p>
   </div>
   <div>
    <div class="panel">
     <h3><svg viewBox="0 0 24 24" style="width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.8"><circle cx="13.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="10.5" r="2.5"/><circle cx="8.5" cy="7.5" r="2.5"/><circle cx="6.5" cy="12" r="2.5"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.9 0 1.7-.8 1.7-1.7 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2 0-.9.8-1.7 1.7-1.7H16c3.3 0 6-2.7 6-6 0-5.5-4.5-9.8-10-9.8z"/></svg> <span data-i18n="accent_color">Accent Color</span></h3>
     <div class="color-grid" id="color-presets"></div>
     <div class="color-input">
      <input type="color" id="color-picker" value="#6c5ce7">
      <span style="color:var(--text2);font-size:12px" id="color-hex">#6c5ce7</span>
     </div>
     <div class="toggle-row" style="margin-top:14px;border:none;padding:0">
      <div class="toggle-label"><span data-i18n="fireflies">Fireflies</span><span style="color:var(--text2);font-size:10px">ambient particles</span></div>
      <label class="toggle"><input type="checkbox" id="fireflies-toggle" onchange="toggleFireflies(this.checked)"><span class="slider"></span></label>
     </div>
    </div>
    <div class="panel">
     <h3>System</h3>
     <div class="form-group"><label>Platform</label><input type="text" id="set-screen" disabled style="opacity:.5"></div>
     <div class="form-group"><label>Java</label><input type="text" id="set-java" disabled style="opacity:.5"></div>
     <div class="form-group"><label>RCON</label><input type="text" id="set-rcon" disabled style="opacity:.5"></div>
    </div>
   </div>
  </div>
 </div>
</div>

<div class="modal-overlay" id="modal-overlay" onclick="if(event.target===this)closeModal()">
 <div class="modal">
  <h3 id="modal-title" data-i18n="add_player">Add Player</h3>
  <div class="form-group">
   <label data-i18n="player_name">Player Name</label>
   <input type="text" id="modal-player-name" data-i18n-ph="enter_username" placeholder="Enter username">
  </div>
  <div style="display:flex;gap:10px;justify-content:flex-end">
   <button class="btn btn-outline" onclick="closeModal()"><span data-i18n="cancel">Cancel</span></button>
   <button class="btn btn-accent" onclick="modalConfirm()"><span data-i18n="add">Add</span></button>
  </div>
 </div>
</div>

<script>
let currentTab='dashboard';
let currentList='ops';
let consoleLines=[];
let modalAction='';
let serverReady=false;
let lastWasRunning=null;

function playCrashSound(){
 try{
  const ctx=new(window.AudioContext||window.webkitAudioContext)();
  const now=ctx.currentTime;
  [440,350,260].forEach((freq,i)=>{
   const osc=ctx.createOscillator();
   const gain=ctx.createGain();
   osc.type='square';
   osc.frequency.setValueAtTime(freq,now+i*.2);
   gain.gain.setValueAtTime(.3,now+i*.2);
   gain.gain.exponentialRampToValueAtTime(.001,now+i*.2+.3);
   osc.connect(gain);gain.connect(ctx.destination);
   osc.start(now+i*.2);osc.stop(now+i*.2+.3);
  });
 }catch(e){}
}
let T={};

function t(key){return T[key]||key;}

async function loadLang(){
 try{
  const r=await fetch('/api/lang');
  T=await r.json();
 }catch(e){T={};}
 try{
  const sr=await fetch('/api/settings');
  const sd=await sr.json();
  if(sd.accent)applyAccent(sd.accent);
  if(sd.fireflies){firefliesOn=true;setTimeout(()=>{document.getElementById('fireflies-bg').classList.add('active');},500);}
 }catch(e){}
 applyTranslations();
}

function applyTranslations(){
 document.querySelectorAll('[data-i18n]').forEach(el=>{
  const key=el.getAttribute('data-i18n');
  if(T[key])el.textContent=T[key];
 });
 document.querySelectorAll('[data-i18n-ph]').forEach(el=>{
  const key=el.getAttribute('data-i18n-ph');
  if(T[key])el.placeholder=T[key];
 });
 document.getElementById('page-title').textContent=t(currentTab);
}

function showTab(tab){
 if(currentTab==='settings'&&tab!=='settings')restoreAccent();
 document.querySelectorAll('[id^="tab-"]').forEach(e=>e.style.display='none');
 document.querySelectorAll('.sidebar nav a').forEach(e=>e.classList.remove('active'));
 const tabEl=document.getElementById('tab-'+tab);
 tabEl.style.display='block';
 tabEl.classList.remove('tab-enter');
 void tabEl.offsetWidth;
 tabEl.classList.add('tab-enter');
 document.getElementById('nav-'+tab).classList.add('active');
 currentTab=tab;
 document.getElementById('page-title').textContent=t(tab);
 if(tab==='dashboard')refreshDashboard();
 if(tab==='setup')loadSetup();
  if(tab==='console')loadConsole();
  if(tab==='players')loadPlayers();
  if(tab==='files')loadProperties();
  if(tab==='filebrowser')loadFiles();
  if(tab==='plugins')loadPlugins();
  if(tab==='settings')loadSettingsPage();
}

function toast(msg){
 let el=document.createElement('div');
 el.className='toast';el.textContent=msg;
 document.body.appendChild(el);
 setTimeout(()=>{el.style.animation='slideOut .3s ease forwards';setTimeout(()=>el.remove(),300);},3000);
}

async function api(path,opts={}){
 try{
  const r=await fetch('/api/'+path,opts);
  return await r.json();
 }catch(e){return {error:e.message};}
}

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}

function playOof(){
 try{
  const a=new Audio('https://www.myinstants.com/media/sounds/classic_hurt.mp3');
  a.volume=.7;
  a.play();
 }catch(e){}
}

let confirmCallback=null;
function confirmAction(msg,action,type,name){
 document.getElementById('confirm-message').textContent=msg;
 document.getElementById('confirm-overlay').classList.add('active');
 confirmCallback=()=>{document.getElementById('confirm-overlay').classList.remove('active');executeAction(action,type,name);};
}
function confirmOk(){if(confirmCallback)confirmCallback();}
function confirmCancel(){
 document.getElementById('confirm-overlay').classList.remove('active');
 confirmCallback=null;
}

async function executeAction(action,type,name){
 if(action==='deleteAll'){
  const r=await api('delete-all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type})});
  toast(r.message||'Deleted');loadPlugins();
 }else if(action==='deleteItem'){
  const r=await api('delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type,name})});
  toast(r.message||'Deleted');loadPlugins();
 }else if(action==='removePlayerConfirm'){
  removePlayerConfirm(type,name);
 }else if(action==='stop'){
  const r=await api('server?action=stop');toast(r.message||'Stopped');setTimeout(refreshDashboard,2000);
 }else if(action==='start'){
  const r=await api('server?action=start');toast(r.message||'Started');setTimeout(refreshDashboard,2000);
 }else if(action==='restart'){
  await api('server?action=stop');setTimeout(async()=>{await api('server?action=start');toast(t('server_restarting'));setTimeout(refreshDashboard,3000);},2000);
 }
}
function lineClass(l){
 if(l.includes('WARN'))return'warn';
 if(l.includes('ERROR')||l.includes('Exception'))return'error';
 if(l.includes('<')&&l.includes('>'))return'chat';
 return'info';
}

 async function refreshDashboard(){
  const info=await api('status');
  if(info.error)return;
  serverReady=info.has_jar;

  if(lastWasRunning===true&&info.running===false){
   const cl=await api('console?lines=30');
   const crashed=cl.lines&&cl.lines.some(l=>/ERROR|Exception|crash|SIGTERM|OutOfMemory|killed/i.test(l));
   if(crashed){playCrashSound();toast(t('server_crashed'));}
  }
  lastWasRunning=info.running;

 let statusHtml='';
 statusHtml+=`<div class="status-card"><div class="label">${t('status')}</div><div class="value ${info.running?'green':'red'}">${info.running?t('online'):t('offline')}</div></div>`;
 statusHtml+=`<div class="status-card"><div class="label">${t('memory_used')}</div><div class="value blue">${info.memory.used_mb||0} MB</div></div>`;
 statusHtml+=`<div class="status-card"><div class="label">${t('memory_total')}</div><div class="value">${info.memory.total_mb||0} MB</div></div>`;
 statusHtml+=`<div class="status-card"><div class="label">${t('tps')}</div><div class="value yellow">${info.tps||'-'}</div></div>`;
 statusHtml+=`<div class="status-card"><div class="label">${t('max_players')}</div><div class="value cyan">${info.max_players}</div></div>`;
 document.getElementById('status-bar').innerHTML=statusHtml;

 document.getElementById('status-dot').style.background=info.running?'var(--green)':'var(--red)';

 let btns='';
 if(info.running){
  btns+=`<button class="btn btn-red" onclick="serverAction('stop')">&#9632; ${t('stop')}</button>`;
  btns+=`<button class="btn btn-yellow" onclick="serverAction('restart')">&#8635; ${t('restart')}</button>`;
 }else{
  btns+=`<button class="btn btn-green" onclick="serverAction('start')">&#9654; ${t('start')}</button>`;
 }
 document.getElementById('control-btns').innerHTML=btns;

 let qi='';
 qi+=`<div style="display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--border)"><span style="color:var(--text2)">${t('motd')}</span><span>${esc(info.motd||'-')}</span></div>`;
 qi+=`<div style="display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--border)"><span style="color:var(--text2)">${t('ops_count')}</span><span>${info.ops_count||0}</span></div>`;
 qi+=`<div style="display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--border)"><span style="color:var(--text2)">${t('whitelist_count')}</span><span>${info.whitelist_count||0}</span></div>`;
 qi+=`<div style="display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--border)"><span style="color:var(--text2)">${t('bans_count')}</span><span>${info.bans_count||0}</span></div>`;
 document.getElementById('quick-info').innerHTML=qi;

  const cl=await api('console?lines=15');
  if(cl.lines){
   const box=document.getElementById('dash-console');
   box.innerHTML=cl.lines.map(l=>'<div class="line '+lineClass(l)+'">'+esc(l)+'</div>').join('');
   box.scrollTop=box.scrollHeight;
  }
}

async function serverAction(action){
 if(action==='restart'){confirmAction(t('confirm_restart'),'restart');}
 else if(action==='stop'){confirmAction(t('confirm_stop'),'stop');}
 else{confirmAction(t('confirm_start'),'start');}
}

async function loadSetup(){
 const info=await api('status');
 const el=document.getElementById('setup-view');
 const jarName=info.server_jar||'server.jar';

 const fileChecks=[
  {id:'keep-world',label:'world/',path:'world',isDir:true},
  {id:'keep-mods',label:'mods/',path:'mods',isDir:true},
  {id:'keep-plugins',label:'plugins/',path:'plugins',isDir:true},
  {id:'keep-ops',label:'ops.json',path:'ops.json',isDir:false},
  {id:'keep-bans',label:'banned-players.json',path:'banned-players.json',isDir:false},
  {id:'keep-bansip',label:'banned-ips.json',path:'banned-ips.json',isDir:false},
  {id:'keep-wl',label:'whitelist.json',path:'whitelist.json',isDir:false},
  {id:'keep-props',label:'server.properties',path:'server.properties',isDir:false},
 ];

 const existsData=await api('file-exists');
 const exists=existsData.exists||{};

 let togglesHtml='';
 fileChecks.forEach(fc=>{
  const has=exists[fc.path]||false;
  const disabled=has?'':'disabled';
  const checked=has?'checked':'';
  const hint=has?'':`<span style="color:var(--yellow);font-size:10px">${t('not_found')}</span>`;
  togglesHtml+=`
   <div class="toggle-row">
    <div class="toggle-label">
     <span>${esc(fc.label)}</span>
     ${hint}
    </div>
    <label class="toggle">
     <input type="checkbox" id="${fc.id}" ${checked} ${disabled}>
     <span class="slider"></span>
    </label>
   </div>`;
 });

 if(info.has_jar){
  el.innerHTML=`
  <div class="grid-2">
   <div class="panel">
    <h3>&#9881; ${t('server_core')}</h3>
    <p style="color:var(--text2);font-size:13px;margin-bottom:14px">${t('current')}: <strong style="color:var(--text)">${esc(jarName)}</strong></p>
    <div class="drop-zone" id="drop-core" ondragover="handleDrag(event,this)" ondragleave="handleDragLeave(this)" ondrop="handleDropCore(event,this)" onclick="this.querySelector('input').click()">
     <input type="file" accept=".jar" onchange="uploadCore(this.files[0]);this.value=''">
     <div class="drop-icon">&#128228;</div>
     <div class="drop-text">${t('drop_jar')}</div>
     <div class="drop-hint">${t('replace_core')}</div>
    </div>
    <div id="core-replace-opts" style="display:none">
     <h3 style="margin-bottom:4px">${t('keep_on_replace')}</h3>
     <p style="color:var(--text2);font-size:11px;margin-bottom:10px">${t('toggle_off_delete')}</p>
     ${togglesHtml}
    </div>
    <button class="btn btn-accent" onclick="uploadCoreConfirm()" id="btn-core-upload" style="display:none;margin-top:14px">${t('upload_replace')}</button>
   </div>
   <div class="panel">
    <h3>&#128193; ${t('quick_actions')}</h3>
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px">
     <button class="btn btn-green btn-sm" onclick="serverAction('start')">&#9654; ${t('start')}</button>
     <button class="btn btn-yellow btn-sm" onclick="serverAction('restart')">&#8635; ${t('restart')}</button>
    </div>
    <div id="setup-files"></div>
   </div>
  </div>`;
  loadSetupFiles();
 }else{
  el.innerHTML=`
  <div class="setup-card">
   <h3>&#127775; ${t('setup_welcome')}</h3>
   <p>${t('setup_desc')}</p>
   <div class="drop-zone" id="drop-core" ondragover="handleDrag(event,this)" ondragleave="handleDragLeave(this)" ondrop="handleDropCore(event,this)" onclick="this.querySelector('input').click()">
    <input type="file" accept=".jar" onchange="uploadCore(this.files[0]);this.value=''">
    <div class="drop-icon">&#128228;</div>
    <div class="drop-text">${t('drop_jar_here')}</div>
    <div class="drop-hint">${t('supported')}</div>
   </div>
   <button class="btn btn-accent" onclick="uploadCoreConfirm()" id="btn-core-upload" style="display:none;margin-top:12px">${t('upload_create')}</button>
  </div>`;
 }
}

let pendingCoreFile=null;
async function uploadCore(file){
 if(!file||!file.name.endsWith('.jar')){toast('Only .jar files');return;}
 pendingCoreFile=file;
 document.getElementById('btn-core-upload').style.display='inline-flex';
 const info=await api('status');
 if(info.has_jar){
  document.getElementById('core-replace-opts').style.display='block';
 }
 toast('File selected: '+file.name);
}

async function uploadCoreConfirm(){
 if(!pendingCoreFile){toast('Select a file first');return;}
 const fd=new FormData();
 fd.append('file',pendingCoreFile);
 fd.append('type','core');
 const keepMap={
  'keep-world':['world','world_nether','world_the_end'],
  'keep-mods':['mods'],
  'keep-plugins':['plugins'],
  'keep-ops':['ops.json'],
  'keep-bans':['banned-players.json'],
  'keep-bansip':['banned-ips.json'],
  'keep-wl':['whitelist.json'],
  'keep-props':['server.properties'],
 };
 const dels={};
 for(const[key,paths]of Object.entries(keepMap)){
  const el=document.getElementById(key);
  if(el&&!el.disabled&&!el.checked){
   paths.forEach(p=>{dels[p]=true;});
  }
 }
 fd.append('keep_data',JSON.stringify(dels));
 toast('Uploading '+pendingCoreFile.name+'...');
 const r=await fetch('/api/upload-core',{method:'POST',body:fd});
 const d=await r.json();
 toast(d.message||'Uploaded');
 pendingCoreFile=null;
 document.getElementById('btn-core-upload').style.display='none';
 loadSetup();
}

async function handleDropCore(e,el){
 e.preventDefault();e.stopPropagation();el.classList.remove('dragover');
 const files=e.dataTransfer.files;
 for(let i=0;i<files.length;i++){
  if(files[i].name.endsWith('.jar'))uploadCore(files[i]);
  else toast('Only .jar files: '+files[i].name);
 }
}

async function loadSetupFiles(){
 const data=await api('files');
 if(!data||data.error)return;
 let html='';
 (data.files||[]).forEach(f=>{
  html+=`<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px"><span>${esc(f.name)}</span><span style="color:var(--text2)">${f.size}</span></div>`;
 });
 document.getElementById('setup-files').innerHTML=html||`<div class="empty">${t('no_files')}</div>`;
}

let consolePoll;
async function loadConsole(){
 const cl=await api('console?lines=200');
 if(cl.lines){consoleLines=cl.lines;renderConsole();}
 if(consolePoll)clearInterval(consolePoll);
 consolePoll=setInterval(async()=>{
  const cl=await api('console?lines=200');
  if(cl.lines){consoleLines=cl.lines;if(currentTab==='console')renderConsole();}
 },2000);
}

let lastConsoleCount=0;
function renderConsole(){
 const box=document.getElementById('live-console');
 if(!box)return;
 const wasAtBottom=box.scrollHeight-box.scrollTop-box.clientHeight<50;
 box.innerHTML=consoleLines.map(l=>'<div class="line '+lineClass(l)+'">'+esc(l)+'</div>').join('');
 lastConsoleCount=consoleLines.length;
 if(wasAtBottom)box.scrollTop=box.scrollHeight;
}

async function sendCmd(){
 const input=document.getElementById('cmd-input');
 const cmd=input.value.trim();
 if(!cmd)return;
 input.value='';
 await api('command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd})});
 toast('Command sent: '+cmd);
 setTimeout(loadConsole,1000);
}

async function loadPlayers(){
 const info=await api('status');
 document.getElementById('ops-count').textContent=info.ops_count||0;
 document.getElementById('wl-count').textContent=info.whitelist_count||0;
 document.getElementById('bans-count').textContent=info.bans_count||0;
 await loadOnlinePlayers();
 showPlayerList(currentList);
}

async function loadOnlinePlayers(){
 const data=await api('online');
 const el=document.getElementById('online-players');
 const countEl=document.getElementById('online-count');
 if(!data||!data.players||data.players.length===0){
  el.innerHTML=`<div class="empty">${t('no_players_online')}</div>`;
  countEl.textContent='0';
  return;
 }
 countEl.textContent=data.players.length;
 el.innerHTML=data.players.map(p=>{
  const pingClass=p.ping<100?'ping-good':p.ping<250?'ping-mid':'ping-bad';
  const headUrl='https://mc-heads.net/avatar/'+encodeURIComponent(p.name)+'/36';
  return`<div class="player-row">
   <img class="player-head" src="${headUrl}" alt="${esc(p.name)}" onerror="this.style.display='none'">
   <div class="player-info">
    <div class="player-name">${esc(p.name)}</div>
    <div class="player-uuid">${esc(p.uuid)}</div>
   </div>
   <div class="player-ping ${pingClass}">${p.ping} ms</div>
  </div>`;
 }).join('');
}

async function showPlayerList(type,btn){
 currentList=type;
 document.querySelectorAll('#player-tabs .btn').forEach(b=>b.classList.remove('active'));
 if(btn)btn.classList.add('active');
 let file=type==='ops'?'ops.json':type==='whitelist'?'whitelist.json':'banned-players.json';
 const data=await api('json?file='+file);
 if(!data||!Array.isArray(data)||data.length===0){
  document.getElementById('player-list').innerHTML=`<div class="empty">${t('no_players')}</div>`;
  return;
 }
 let html=`<table><tr><th>${t('player_name')}</th><th>UUID</th><th>${t('confirm_delete')}</th></tr>`;
 data.forEach(p=>{
  const name=typeof p==='string'?p:(p.name||'');
  const uuid=typeof p==='string'?'-':(p.uuid||'-');
  html+=`<tr><td><div style="display:flex;align-items:center;gap:10px"><img src="https://mc-heads.net/avatar/${encodeURIComponent(name)}/24" style="width:24px;height:24px;border-radius:4px" onerror="this.style.display='none'"><span>${esc(name)}</span></div></td><td style="color:var(--text2);font-size:11px;font-family:monospace">${esc(uuid)}</td><td><button class="btn btn-red btn-sm" onclick="removePlayer('${type}','${esc(name).replace(/'/g,"\\'")}')">Remove</button></td></tr>`;
 });
 html+='</table>';
 document.getElementById('player-list').innerHTML=html;
}

function showPlayerModal(type){
 modalAction=type;
 const titles={ops:t('add_op'),whitelist:t('add_whitelist'),ban:t('add_ban')};
 document.getElementById('modal-title').textContent=titles[type]||t('add_player');
 document.getElementById('modal-player-name').value='';
 document.getElementById('modal-overlay').classList.add('active');
 setTimeout(()=>document.getElementById('modal-player-name').focus(),100);
}
function closeModal(){document.getElementById('modal-overlay').classList.remove('active');}

async function modalConfirm(){
 const name=document.getElementById('modal-player-name').value.trim();
 if(!name){toast('Enter a name');return;}
 const r=await api('player',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'add',type:modalAction,name})});
 toast(r.message||'Done');closeModal();loadPlayers();
}

async function removePlayer(type,name){
 const apiType=type==='banned'?'ban':type;
 confirmAction(t('confirm_delete')+': '+name+'?','removePlayerConfirm',apiType,name);
}

async function removePlayerConfirm(type,name){
 const r=await api('player',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'remove',type,name})});
 toast(r.message||t('removed'));loadPlayers();
}

async function loadProperties(){
 const data=await api('properties');
 if(!data||data.error)return;
 const important=['server-port','server-ip','max-players','motd','gamemode','difficulty','pvp','white-list','online-mode','level-name','view-distance','simulation-distance','spawn-protection','max-world-size','level-seed','allow-flight','hardcore','enable-command-block'];
 let html='';
 important.forEach(k=>{
  if(!(k in data))return;
  const v=data[k];const isBool=['true','false'].includes(v);
  html+=`<div class="form-group"><label>${k}</label>`;
  if(isBool)html+=`<select data-key="${k}"><option value="true"${v==='true'?' selected':''}>true</option><option value="false"${v==='false'?' selected':''}>false</option></select>`;
  else html+=`<input type="text" data-key="${k}" value="${esc(v)}"${k==='level-name'?' data-orig="'+esc(v)+'"':''}>`;
  html+=`</div>`;
 });
 document.getElementById('props-form').innerHTML=html;
}

async function saveProperties(){
 const inputs=document.querySelectorAll('#props-form [data-key]');
 const props={};inputs.forEach(inp=>{props[inp.dataset.key]=inp.value;});
 if(props['level-name']){
  const old=document.querySelector('#props-form [data-key="level-name"]');
  if(old&&old.dataset.orig&&old.dataset.orig!==props['level-name']){
   if(!confirm('Changing level-name will create a new world. Continue?')){
    old.value=old.dataset.orig;return;
   }
  }
 }
 const r=await api('properties',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(props)});
 toast(r.message||'Properties saved');
}

const EDITABLE_EXTS=['.json','.txt','.yml','.yaml','.properties','.cfg','.conf','.ini','.xml','.toml','.sh','.bat','.py','.js','.log','.md','.csv','.env'];
let currentEditFile='';
let currentDir='';

async function loadFiles(path){
 currentDir=path||'';
 const url=currentDir?'files?path='+encodeURIComponent(currentDir):'files';
 const data=await api(url);
 if(!data||data.error)return;
 const bc=document.getElementById('file-breadcrumb');
 if(bc){
  const parts=currentDir?currentDir.split('/').filter(Boolean):[];
  let html='<span style="cursor:pointer;color:var(--accent)" onclick="loadFiles()">/</span>';
  let accumulated='';
  parts.forEach((p,i)=>{
   accumulated+=(accumulated?'/':'')+p;
   const cp=accumulated;
   html+=` / <span style="cursor:pointer;color:var(--accent)" onclick="loadFiles('${esc(cp)}')">${esc(p)}</span>`;
  });
  bc.innerHTML=html;
 }
 let html='<table><tr><th>Name</th><th>Size</th></tr>';
 if(currentDir){
  const parent=currentDir.split('/').slice(0,-1).join('/');
  html+=`<tr style="cursor:pointer" onclick="loadFiles('${esc(parent)}')"><td style="color:var(--accent)">&#128193; ..</td><td style="color:var(--text2)">-</td></tr>`;
 }
 (data.files||[]).forEach(f=>{
  const isDir=f.type==='dir';
  const ext=f.name.includes('.')?'.'+f.name.split('.').pop().toLowerCase():'';
  const editable=!isDir&&EDITABLE_EXTS.includes(ext);
  if(isDir){
   const dirPath=currentDir?currentDir+'/'+f.name.replace('/',''):f.name.replace('/','');
   html+=`<tr style="cursor:pointer" onclick="loadFiles('${esc(dirPath)}')"><td style="color:var(--accent)">&#128193; ${esc(f.name)}</td><td style="color:var(--text2)">${f.size}</td></tr>`;
  }else if(editable){
   const filePath=currentDir?currentDir+'/'+f.name:f.name;
   html+=`<tr style="cursor:pointer" onclick="openFileEditor('${esc(filePath).replace(/'/g,"\\'")}')"><td style="color:var(--accent);font-weight:600">${esc(f.name)}</td><td style="color:var(--text2)">${f.size}</td></tr>`;
  }else{
   html+=`<tr><td>${esc(f.name)}</td><td style="color:var(--text2)">${f.size}</td></tr>`;
  }
 });
 html+='</table>';
 document.getElementById('file-list').innerHTML=html||`<div class="empty">${t('no_files')}</div>`;
}

async function openFileEditor(name){
 currentEditFile=name;
 document.getElementById('file-editor-title').textContent=name;
 const r=await api('file-read?name='+encodeURIComponent(name));
 if(r.error){toast(r.error);return;}
 document.getElementById('file-editor-content').value=r.content;
 document.getElementById('file-editor-overlay').classList.add('active');
}

function closeFileEditor(){
 document.getElementById('file-editor-overlay').classList.remove('active');
 currentEditFile='';
}

async function saveFileEdit(){
 if(!currentEditFile)return;
 const content=document.getElementById('file-editor-content').value;
 const r=await api('file-write',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:currentEditFile,content})});
 toast(r.message||'Saved');
 closeFileEditor();
}

async function loadPlugins(){
 const plugins=await api('plugins');
 const mods=await api('mods');
 if(plugins&&plugins.length){
  let html='';
  if(plugins.length>2)html+=`<div style="margin-bottom:10px"><button class="btn btn-red btn-sm" onclick="confirmAction('${t('confirm_delete_all')} plugins?','deleteAll','plugins')">${t('delete_all')} (${plugins.length})</button></div>`;
  html+=plugins.map(p=>`<div class="plugin-item"><span class="name">${esc(p)}</span><button class="btn btn-red btn-sm" onclick="confirmAction('${t('confirm_delete')}: ${esc(p).replace(/'/g,"\\'")}?','deleteItem','plugins','${esc(p).replace(/'/g,"\\'")}')">&#128465;</button></div>`).join('');
  document.getElementById('plugins-list').innerHTML=html;
 }else{
  document.getElementById('plugins-list').innerHTML=`<div class="empty">${t('no_plugins')}</div>`;
 }
 if(mods&&mods.length){
  let html='';
  if(mods.length>2)html+=`<div style="margin-bottom:10px"><button class="btn btn-red btn-sm" onclick="confirmAction('${t('confirm_delete_all')} mods?','deleteAll','mods')">${t('delete_all')} (${mods.length})</button></div>`;
  html+=mods.map(m=>`<div class="mod-item"><span class="name">${esc(m)}</span><button class="btn btn-red btn-sm" onclick="confirmAction('${t('confirm_delete')}: ${esc(m).replace(/'/g,"\\'")}?','deleteItem','mods','${esc(m).replace(/'/g,"\\'")}')">&#128465;</button></div>`).join('');
  document.getElementById('mods-list').innerHTML=html;
 }else{
  document.getElementById('mods-list').innerHTML=`<div class="empty">${t('no_mods')}</div>`;
 }
}

function handleDrag(e,el){e.preventDefault();e.stopPropagation();el.classList.add('dragover');}
function handleDragLeave(el){el.classList.remove('dragover');}
function handleDrop(e,type,el){
 e.preventDefault();e.stopPropagation();el.classList.remove('dragover');
 const files=e.dataTransfer.files;
 for(let i=0;i<files.length;i++){
  if(files[i].name.endsWith('.jar'))uploadFile(type,files[i]);
  else toast('Only .jar files: '+files[i].name);
 }
}

async function uploadFile(type,file){
 if(!file||!file.name.endsWith('.jar')){toast('Only .jar files');return;}
 const fd=new FormData();fd.append('file',file);fd.append('type',type);
 toast('Uploading '+file.name+'...');
 const r=await fetch('/api/upload',{method:'POST',body:fd});
 const d=await r.json();
 toast(d.message||'Uploaded');
 loadPlugins();
}

async function deleteItem(type,name){
 if(!confirm(t('confirm_delete')+': '+name+'?'))return;
 const r=await api('delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type,name})});
 if(r.error){toast(r.error);return;}
 toast(name+' '+t('removed'));
 loadPlugins();
}

const ACCENT_PRESETS=['#6c5ce7','#a855f7','#3b82f6','#06b6d4','#22c55e','#eab308','#f97316','#ef4444','#ec4899','#8b5cf6'];

let currentLang='en';
function toggleCustomSelect(el){
 el.classList.toggle('open');
 document.querySelectorAll('.custom-select').forEach(s=>{if(s!==el)s.classList.remove('open');});
}
function selectLang(code,label){
 currentLang=code;
 document.getElementById('lang-selected').textContent=label;
 document.getElementById('lang-options').querySelectorAll('.custom-select-option').forEach(o=>o.classList.toggle('selected',o.dataset.value===code));
 document.querySelectorAll('.custom-select').forEach(s=>s.classList.remove('open'));
}
document.addEventListener('click',e=>{
 if(!e.target.closest('.custom-select'))document.querySelectorAll('.custom-select').forEach(s=>s.classList.remove('open'));
});

async function loadSettingsPage(){
 const data=await api('settings');
 if(data.lang){
  currentLang=data.lang;
  const langMap={en:'English',ru:'Русский',de:'Deutsch',fr:'Français',zh:'中文'};
  document.getElementById('lang-selected').textContent=langMap[data.lang]||'English';
  document.getElementById('lang-options').querySelectorAll('.custom-select-option').forEach(o=>o.classList.toggle('selected',o.dataset.value===data.lang));
 }
 if(data.accent){
  savedAccent=data.accent;
  document.getElementById('color-picker').value=data.accent;
  document.getElementById('color-hex').textContent=data.accent;
 }
 if(data.fireflies){
  firefliesOn=true;
  document.getElementById('fireflies-toggle').checked=true;
  document.getElementById('fireflies-bg').classList.add('active');
 }
 const env=await api('env-info').catch(()=>({}));
 if(env.mc_dir)document.getElementById('set-mcdir').value=env.mc_dir;
 if(env.port)document.getElementById('set-port').value=env.port;
 if(env.platform)document.getElementById('set-screen').value=env.platform;
 if(env.java_path)document.getElementById('set-java').value=env.java_path+' (v'+env.java_version+')';
 const rconStatus=env.rcon_enabled?`Enabled (port ${env.rcon_port})`:'Disabled';
 document.getElementById('set-rcon').value=rconStatus;
 document.getElementById('set-javaargs').value=env.java_args||'';
 document.getElementById('set-encoding').value=env.encoding||'auto';
 initColorPresets();
}

function initColorPresets(){
 const el=document.getElementById('color-presets');
 const cur=document.getElementById('color-picker').value;
 el.innerHTML=ACCENT_PRESETS.map(c=>`<div class="color-swatch${c===cur?' active':''}" style="background:${c}" onclick="setAccent('${c}')"></div>`).join('');
}

function setAccent(hex){
 document.getElementById('color-picker').value=hex;
 document.getElementById('color-hex').textContent=hex;
 document.querySelectorAll('.color-swatch').forEach(s=>s.classList.toggle('active',s.style.background===hex||rgbToHex(s.style.background)===hex));
 applyAccent(hex);
}

function rgbToHex(rgb){
 if(rgb.startsWith('#'))return rgb;
 const m=rgb.match(/\d+/g);
 if(!m)return rgb;
 return '#'+m.slice(0,3).map(x=>(+x).toString(16).padStart(2,'0')).join('');
}

function getContrastColor(hex){
 const r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
 return(0.299*r+0.587*g+0.114*b)/255>0.5?'#000000':'#ffffff';
}

function applyAccent(hex){
 const r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
 document.documentElement.style.setProperty('--accent',hex);
 document.documentElement.style.setProperty('--accent2',`rgb(${Math.min(255,r+40)},${Math.min(255,g+20)},${Math.min(255,b+60)})`);
 document.documentElement.style.setProperty('--accent3',`rgb(${Math.min(255,r+20)},${Math.min(255,g+10)},${Math.min(255,b+40)})`);
 document.documentElement.style.setProperty('--accent-rgb',`${r},${g},${b}`);
 document.documentElement.style.setProperty('--glow',`0 0 20px rgba(${r},${g},${b},.15)`);
 document.documentElement.style.setProperty('--logo-color',getContrastColor(hex));
 const lum=(0.299*r+0.587*g+0.114*b)/255;
 if(lum<.35){
  document.documentElement.style.setProperty('--text','#ffffff');
  document.documentElement.style.setProperty('--text2','#b0b8c8');
 }else{
  document.documentElement.style.setProperty('--text','#e2e8f0');
  document.documentElement.style.setProperty('--text2','#8892a4');
 }
}

function restoreAccent(){
 applyAccent(savedAccent);
 document.getElementById('color-picker').value=savedAccent;
 document.getElementById('color-hex').textContent=savedAccent;
 document.querySelectorAll('.color-swatch').forEach(s=>s.classList.toggle('active',rgbToHex(s.style.background)===savedAccent));
}

 async function saveSettings(){
  const lang=currentLang;
  const mcdir=document.getElementById('set-mcdir').value.trim();
  const port=document.getElementById('set-port').value.trim();
  const javaargs=document.getElementById('set-javaargs').value.trim();
  const encoding=document.getElementById('set-encoding').value.trim();
  const accent=document.getElementById('color-picker').value;
  const ff=document.getElementById('fireflies-toggle').checked;
  await api('settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lang,accent,fireflies:ff})});
  const envData={MC_DIR:mcdir||'/minecraft',PANEL_PORT:port||'8080',JAVA_ARGS:javaargs,JAVA_ENCODING:encoding};
  await api('save-env',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(envData)});
  savedAccent=accent;
  firefliesOn=ff;
  T=await (await fetch('/api/lang')).json();
  applyTranslations();
  showTab(currentTab);
  toast(t('saved'));
 }

let savedAccent='#6c5ce7';
let firefliesOn=false;

function toggleFireflies(on){
 firefliesOn=on;
 const bg=document.getElementById('fireflies-bg');
 if(on)bg.classList.add('active');
 else bg.classList.remove('active');
}

refreshDashboard();
loadLang();
setInterval(()=>{if(currentTab==='dashboard')refreshDashboard();},5000);
setInterval(()=>{if(currentTab==='players')loadOnlinePlayers();},3000);
</script>
</body>
</html>"""


class PanelHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, content):
        body = content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return self.rfile.read(length)
        return b""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "":
            self._html(HTML_TEMPLATE)
            return

        if path == "/api/status":
            info = get_server_info()
            jar_files = list(MC_DIR.glob("*.jar"))
            if jar_files:
                info["server_jar"] = jar_files[0].name
            self._json(info)
            return

        if path == "/api/console":
            n = int(params.get("lines", [100])[0])
            self._json({"lines": get_console_lines(n)})
            return

        if path == "/api/server":
            action = params.get("action", ["status"])[0]
            if action == "start":
                setup_server()
                msg = start_server()
                self._json({"message": msg})
            elif action == "stop":
                msg = stop_server()
                self._json({"message": msg})
            else:
                self._json({"running": is_server_running()})
            return

        if path == "/api/json":
            file = params.get("file", [""])[0]
            if file:
                self._json(read_json_file(file))
            else:
                self._json([])
            return

        if path == "/api/properties":
            self._json(read_properties())
            return

        if path == "/api/plugins":
            self._json(list_plugins())
            return

        if path == "/api/mods":
            self._json(list_mods())
            return

        if path == "/api/online":
            self._json(get_online_players())
            return

        if path == "/api/file-exists":
            check_paths = [
                "world", "mods", "plugins",
                "ops.json", "banned-players.json",
                "banned-ips.json", "whitelist.json",
                "server.properties"
            ]
            exists = {}
            for p in check_paths:
                f = MC_DIR / p
                exists[p] = f.exists()
            self._json({"exists": exists})
            return

        if path == "/api/settings":
            self._json(load_settings())
            return

        if path == "/api/lang":
            settings = load_settings()
            lang = settings.get("lang", "en")
            self._json(TRANSLATIONS.get(lang, TRANSLATIONS["en"]))
            return

        if path == "/api/env-info":
            java_bin = find_java()
            java_ver = "?"
            try:
                r = subprocess.run([java_bin, "-version"], capture_output=True, text=True, timeout=5)
                m = re.search(r'"([\d._]+)', (r.stderr + r.stdout))
                if m:
                    java_ver = m.group(1)
            except Exception:
                pass
            rcon = _read_rcon_config()
            self._json({
                "mc_dir": _env.get("MC_DIR", str(MC_DIR)),
                "port": int(_env.get("PANEL_PORT", str(PANEL_PORT))),
                "lang": _env.get("PANEL_LANG", "en"),
                "platform": "Windows" if IS_WINDOWS else "Linux",
                "java_path": java_bin,
                "java_version": java_ver,
                "rcon_enabled": rcon["enabled"],
                "rcon_port": rcon["port"],
                "encoding": _env.get("JAVA_ENCODING", "") or "auto",
                "java_args": _env.get("JAVA_ARGS", "")
            })
            return

        if path == "/api/files":
            subpath = params.get("path", [""])[0]
            base = MC_DIR / subpath if subpath else MC_DIR
            if not base.exists() or not base.is_dir():
                self._json({"error": "Directory not found"}, 404)
                return
            entries = []
            try:
                for f in sorted(base.iterdir()):
                    skip = f.name in ("panel", ".git", "__pycache__")
                    if f.is_file():
                        if skip:
                            continue
                        size = f.stat().st_size
                        if size > 1024 * 1024:
                            sz = f"{size / (1024*1024):.1f} MB"
                        elif size > 1024:
                            sz = f"{size / 1024:.1f} KB"
                        else:
                            sz = f"{size} B"
                        entries.append({"name": f.name, "size": sz, "type": "file"})
                    elif f.is_dir():
                        if skip:
                            continue
                        entries.append({"name": f.name, "size": "-", "type": "dir"})
            except Exception:
                pass
            self._json({"files": entries})
            return

        if path == "/api/file-read":
            name = params.get("name", [""])[0]
            if not name:
                self._json({"error": "No name"}, 400)
                return
            fpath = MC_DIR / name
            if not fpath.exists() or not fpath.is_file():
                self._json({"error": "Not found"}, 404)
                return
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
                self._json({"name": name, "content": text})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_type = self.headers.get("Content-Type", "")

        if path == "/api/upload-core" and "multipart/form-data" in content_type:
            form = parse_multipart(self)
            if form and "file" in form:
                file_item = form["file"]
                if file_item.filename and file_item.filename.endswith(".jar"):
                    data = file_item.file.read()

                    old_jar = list(MC_DIR.glob("*.jar"))
                    for j in old_jar:
                        j.unlink()

                    fpath = MC_DIR / "server.jar"
                    fpath.write_bytes(data)

                    del_map = {
                        "world": ["world", "world_nether", "world_the_end"],
                        "mods": ["mods"],
                        "plugins": ["plugins"],
                        "ops.json": ["ops.json"],
                        "banned-players.json": ["banned-players.json"],
                        "banned-ips.json": ["banned-ips.json"],
                        "whitelist.json": ["whitelist.json"],
                        "server.properties": ["server.properties"],
                    }

                    keep_field = form.get("keep_data")
                    del_paths = set()
                    if keep_field and hasattr(keep_field, 'value'):
                        try:
                            del_paths = set(json.loads(keep_field.value).keys())
                        except Exception:
                            pass

                    deleted = []
                    for del_key, targets in del_map.items():
                        if del_key in del_paths:
                            for t in targets:
                                f = MC_DIR / t
                                if f.is_dir():
                                    shutil.rmtree(f)
                                    deleted.append(t + "/")
                                elif f.is_file():
                                    f.unlink()
                                    deleted.append(t)

                    setup_server()

                    msg = f"Core uploaded: {file_item.filename}. EULA accepted."
                    if deleted:
                        msg += f" Deleted: {', '.join(deleted)}"
                    self._json({"message": msg, "ok": True})
                else:
                    self._json({"error": "File must be a .jar"}, 400)
            else:
                self._json({"error": "No file"}, 400)
            return

        if path == "/api/upload" and "multipart/form-data" in content_type:
            form = parse_multipart(self)
            if form and "file" in form:
                file_item = form["file"]
                ftype = form.get("type")
                target = "mods" if (ftype and ftype.value == "mods") else "plugins"
                if file_item.filename:
                    data = file_item.file.read()
                    result = save_upload(target, file_item.filename, data)
                    self._json({"message": f"Uploaded {result['name']} ({result['size']} bytes)", "ok": True})
                else:
                    self._json({"error": "No file"}, 400)
            else:
                self._json({"error": "No file"}, 400)
            return

        body = json.loads(self._read_body())

        if path == "/api/delete":
            ftype = body.get("type")
            name = body.get("name")
            target = "mods" if ftype == "mods" else "plugins"
            if delete_file(target, name):
                self._json({"message": f"Deleted {name}"})
            else:
                self._json({"error": "File not found"}, 404)
            return

        if path == "/api/delete-all":
            ftype = body.get("type")
            target = "mods" if ftype == "mods" else "plugins"
            d = MC_DIR / target
            count = 0
            if d.exists():
                for f in d.iterdir():
                    if f.is_file() and f.suffix == ".jar":
                        f.unlink()
                        count += 1
            self._json({"message": f"Deleted {count} files from {target}/"})
            return

        if path == "/api/command":
            cmd = body.get("cmd", "")
            if cmd:
                ok = send_command(cmd)
                self._json({"ok": ok})
            else:
                self._json({"ok": False})
            return

        if path == "/api/player":
            action = body.get("action")
            ptype = body.get("type")
            name = body.get("name", "").strip()
            if not name:
                self._json({"error": "Name required"})
                return

            files = {"ops": "ops.json", "whitelist": "whitelist.json", "ban": "banned-players.json"}
            file = files.get(ptype)
            if not file:
                self._json({"error": "unknown type"})
                return

            server_up = is_server_running()

            cmd_map_add = {"ops": "op", "ban": "ban", "whitelist": "whitelist add"}
            cmd_map_remove = {"ops": "deop", "ban": "pardon", "whitelist": "whitelist remove"}

            if action == "add":
                if server_up:
                    cmd = f"{cmd_map_add[ptype]} {name}"
                    send_command(cmd)
                    self._json({"message": f"{name} added to {ptype} (command sent)"})
                else:
                    data = read_json_file(file)
                    if not isinstance(data, list):
                        data = []
                    exists = any(
                        isinstance(p, dict) and p.get("name", "").lower() == name.lower()
                        for p in data
                    )
                    if exists:
                        self._json({"message": f"{name} already in {ptype}"})
                        return
                    uuid = fetch_uuid(name)
                    if ptype == "ban":
                        entry = {
                            "uuid": uuid, "name": name,
                            "created": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000"),
                            "source": "Server", "expires": "forever",
                            "reason": "Banned by an operator."
                        }
                    elif ptype == "ops":
                        entry = {"uuid": uuid, "name": name, "level": 4, "bypassesPlayerLimit": False}
                    else:
                        entry = {"uuid": uuid, "name": name}
                    data.append(entry)
                    write_json_file(file, data)
                    self._json({"message": f"{name} added to {ptype}"})

            elif action == "remove":
                if server_up:
                    cmd = f"{cmd_map_remove[ptype]} {name}"
                    send_command(cmd)
                    self._json({"message": f"{name} removed from {ptype} (command sent)"})
                else:
                    data = read_json_file(file)
                    if not isinstance(data, list):
                        data = []
                    new_data = [p for p in data if not (
                        isinstance(p, dict) and p.get("name", "").lower() == name.lower()
                    )]
                    removed = len(data) - len(new_data)
                    write_json_file(file, new_data)
                    if removed > 0:
                        self._json({"message": f"{name} removed from {ptype}"})
                    else:
                        self._json({"message": f"{name} not found in {ptype}"})
            return

        if path == "/api/properties":
            props = read_properties()
            props.update(body)
            write_properties(props)
            self._json({"message": "Properties saved. Restart server to apply."})
            return

        if path == "/api/settings":
            save_settings(body)
            if "accent" in body:
                _env["ACCENT_COLOR"] = body["accent"]
            self._json({"message": "Settings saved."})
            return

        if path == "/api/save-env":
            env_path = Path(__file__).parent / ".env"
            lines = []
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if "=" in line and not line.startswith("#"):
                        k = line.split("=", 1)[0].strip()
                        if k in body:
                            lines.append(f"{k}={body[k]}")
                        else:
                            lines.append(line)
                    else:
                        lines.append(line)
            for k, v in body.items():
                if not any(l.split("=", 1)[0].strip() == k for l in lines if "=" in l):
                    lines.append(f"{k}={v}")
            env_path.write_text("\n".join(lines) + "\n")
            for k, v in body.items():
                _env[k] = v
            self._json({"message": "Config saved. Restart panel to apply."})
            return

        if path == "/api/file-write":
            name = body.get("name", "")
            content = body.get("content", "")
            if not name:
                self._json({"error": "No name"}, 400)
                return
            fpath = MC_DIR / name
            if ".." in name or name.startswith("/"):
                self._json({"error": "Invalid path"}, 400)
                return
            try:
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(content, encoding="utf-8")
                self._json({"message": f"Saved {name}"})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        self.send_error(404)


def main():
    MC_DIR.mkdir(parents=True, exist_ok=True)
    print(f"FizMine Panel starting on http://0.0.0.0:{PANEL_PORT}")
    print(f"Minecraft directory: {MC_DIR}")
    server = http.server.HTTPServer(("0.0.0.0", PANEL_PORT), PanelHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPanel stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
