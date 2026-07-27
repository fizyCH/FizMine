#!/usr/bin/env python3
import io
import hashlib
import hmac
import json
import getpass
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

try:
    from fastapi import FastAPI, Request, UploadFile, Form as FastAPIForm, Query
    from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, RedirectResponse
    from starlette.middleware.sessions import SessionMiddleware
    import uvicorn
except ImportError:
    import subprocess
    print("Installing FastAPI + uvicorn...")
    for args in [
        [sys.executable, "-m", "pip", "install", "fastapi", "uvicorn", "python-multipart"],
        [sys.executable, "-m", "pip", "install", "--break-system-packages", "fastapi", "uvicorn", "python-multipart"],
        [sys.executable, "-m", "pip", "install", "--user", "fastapi", "uvicorn", "python-multipart"],
    ]:
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                print("FastAPI installed successfully")
                break
        except Exception as e:
            print(f"Error: {e}")
    try:
        from fastapi import FastAPI, Request, UploadFile, Form as FastAPIForm, Query
        from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, RedirectResponse
        from starlette.middleware.sessions import SessionMiddleware
        import uvicorn
    except ImportError:
        print("ERROR: Failed to install FastAPI. Try: pip3 install fastapi uvicorn python-multipart")
        sys.exit(1)

from fastapi import FastAPI, Request, UploadFile, Form as FastAPIForm, Query, Body
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
import uvicorn

from panel_modules.templates import LOGIN_HTML, HTML_TEMPLATE, TRANSLATIONS

PANEL_VERSION = "3.0"
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.urandom(32).hex())

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


def _get_panel_token():
    return os.environ.get("PANEL_TOKEN", _env.get("PANEL_TOKEN", ""))


def _set_panel_token(token):
    env_path = Path(__file__).parent / ".env"
    lines = []
    found = False
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("PANEL_TOKEN="):
                lines.append(f"PANEL_TOKEN={token}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"PANEL_TOKEN={token}")
    env_path.write_text("\n".join(lines) + "\n")
    _env["PANEL_TOKEN"] = token
    if token:
        os.environ["PANEL_TOKEN"] = token
    elif "PANEL_TOKEN" in os.environ:
        del os.environ["PANEL_TOKEN"]


# Accounts are deliberately kept separate from settings: this makes it harder to
# accidentally expose credentials through the settings API or a panel backup.
USERS_FILE = Path(__file__).parent / "users.json"
# Permissions are intentionally granular so an operator can receive only the
# parts of the panel they need.
PERMISSIONS = ("console", "mods", "properties", "files", "server_control", "players")


def _password_hash(password, salt=None):
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + digest.hex()


def _password_matches(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        check = _password_hash(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
        return hmac.compare_digest(check, digest_hex)
    except (ValueError, AttributeError):
        return False


def load_users():
    if not USERS_FILE.exists():
        return {}
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_users(users):
    USERS_FILE.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_default_admin():
    """Create the documented emergency admin when no accounts exist."""
    users = load_users()
    if users:
        return False
    password = secrets.token_urlsafe(12)
    save_users({"admin": {
        "role": "admin",
        "password_hash": _password_hash(password),
        "permissions": list(PERMISSIONS),
    }})
    print("\n+------------------------------------------+")
    print("| FizMine initial administrator           |")
    print("+------------------------------------------+")
    print("| Username: admin                         |")
    print(f"| Password: {password:<32}|")
    print("+------------------------------------------+\n")
    return True


def user_permissions(user):
    if user and user.get("role") == "admin":
        return list(PERMISSIONS)
    return [p for p in (user or {}).get("permissions", []) if p in PERMISSIONS]


def can(request, permission):
    username = request.session.get("username")
    user = load_users().get(username, {})
    return user.get("role") == "admin" or permission in user_permissions(user)


def forbidden():
    return JSONResponse({"error": "Permission denied"}, status_code=403)


def require(request, permission):
    return None if can(request, permission) else forbidden()


def api_permission(path):
    if path in {"/api/server"}:
        return "server_control"
    if path in {"/api/console", "/api/command"}:
        return "console"
    if path in {"/api/player", "/api/online", "/api/json"}:
        return "players"
    if path in {"/api/settings", "/api/env-info", "/api/save-env", "/api/set-token",
                "/api/remove-token", "/api/backup-panel", "/api/backup-server",
                "/api/check-update", "/api/do-update", "/api/upload-core", "/api/download-core",
                "/api/users"}:
        return "admin"
    if path in {"/api/properties"}:
        return "properties"
    if path in {"/api/plugins", "/api/mods", "/api/upload", "/api/delete", "/api/delete-all"}:
        return "mods"
    if path in {"/api/file-exists", "/api/files", "/api/file-read", "/api/search",
                "/api/file-write", "/api/file-upload", "/api/file-delete",
                "/api/file-mkdir", "/api/file-download"}:
        return "files"
    return None




@app.middleware("http")
async def check_auth(request: Request, call_next):
    # Accounts are mandatory. The old optional auth toggle and legacy token
    # must never provide an unauthenticated way into the panel.
    path = request.url.path
    if path in ("/login", "/logout"):
        return await call_next(request)
    if path.startswith("/static/"):
        return await call_next(request)
    if request.session.get("authenticated") and request.session.get("username"):
        permission = api_permission(path) if path.startswith("/api/") else None
        if permission and not can(request, permission):
            return forbidden()
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return RedirectResponse("/login")


# The auth middleware needs the session populated first. FastAPI stores
# decorator middleware ahead of middleware added with add_middleware(), so
# reverse the registration order once to keep SessionMiddleware outermost.
app.user_middleware.reverse()


_login_attempts = {}
_lockout_until = {}

WEAK_PASSWORDS = [
    "123", "123456", "12345678", "1234567890", "qwerty", "qwerty123",
    "admin", "password", "password1", "111111", "abc123",
    "letmein", "welcome", "monkey", "dragon", "master",
    "login", "princess", "football", "shadow", "sunshine",
    "trustno1", "iloveyou", "batman", "access", "hello",
    "charlie", "donald", "1234", "12345", "123456789",
]


def _check_lockout(ip):
    now = time.time()
    until = _lockout_until.get(ip, 0)
    if now < until:
        return int(until - now)
    if until > 0:
        _lockout_until.pop(ip, None)
        _login_attempts.pop(ip, None)
    return 0


def _record_failed(ip):
    _login_attempts[ip] = _login_attempts.get(ip, 0) + 1
    if _login_attempts[ip] >= 5:
        _lockout_until[ip] = time.time() + 300
        _login_attempts.pop(ip, None)


def _validate_password(pw):
    if len(pw) < 5:
        return False
    if pw.lower() in WEAK_PASSWORDS:
        return False
    return True


@app.api_route("/login", methods=["GET", "POST"])
async def login(request: Request):
    token = _get_panel_token()
    users = load_users()
    ip = request.client.host
    if request.method == "GET":
        if request.session.get("authenticated"):
            return RedirectResponse("/")
        remaining = _check_lockout(ip)
        settings = load_settings()
        accent = settings.get("accent", "#6c5ce7")
        r_val = int(accent[1:3], 16)
        g_val = int(accent[3:5], 16)
        b_val = int(accent[5:7], 16)
        lum = (0.299*r_val + 0.587*g_val + 0.114*b_val) / 255
        text_color = "#ffffff" if lum < 0.35 else "#e2e8f0"
        logo_color = "#000000" if lum > 0.5 else "#ffffff"
        page = LOGIN_HTML.replace("%ACCENT%", accent).replace("%ACCTR%", f"{r_val},{g_val},{b_val}")
        page = page.replace("%TEXT%", text_color).replace("%LOGOCOLOR%", logo_color)
        lang = settings.get("lang", "en")
        tr = TRANSLATIONS.get(lang, TRANSLATIONS["en"])
        login_user = {"en": "Username", "ru": "Логин", "de": "Benutzername", "fr": "Nom d'utilisateur", "zh": "用户名"}.get(lang, "Username")
        page = page.replace("%LOGIN_USER%", login_user)
        page = page.replace("%LOGIN_PH%", tr.get("login_password", "Password"))
        page = page.replace("%LOGIN_BTN%", tr.get("login_btn", "Login"))
        page = page.replace("%LOGIN_ERR%", tr.get("login_error", "Invalid password"))
        page = page.replace("%LOGIN_LOCK%", tr.get("login_locked", "Too many attempts. Try again later."))
        return HTMLResponse(page)
    if _check_lockout(ip) > 0:
        return RedirectResponse("/login?locked=1", status_code=303)
    form_data = await request.form()
    entered = form_data.get("token", "")
    username = form_data.get("username", "").strip().lower()
    user = users.get(username)
    valid = bool(user and _password_matches(entered, user.get("password_hash", "")))
    # Existing panels can use their former password once to become the admin.
    if not users and token and username == "admin" and hmac.compare_digest(entered, token):
        users = {"admin": {"role": "admin", "password_hash": _password_hash(entered)}}
        save_users(users)
        user = users["admin"]
        valid = True
    if valid:
        request.session["authenticated"] = True
        request.session["username"] = username
        _login_attempts.pop(ip, None)
        _lockout_until.pop(ip, None)
        # A 303 converts the login POST into a normal GET for the dashboard.
        # The default 307 repeats POST / on the target and causes a 405.
        return RedirectResponse("/", status_code=303)
    _record_failed(ip)
    return RedirectResponse("/login?error=1", status_code=303)

_default_mcdir = "C:\\minecraft" if IS_WINDOWS else "/minecraft"


def _find_mc_dir():
    env_mcdir = os.environ.get("MC_DIR") or _env.get("MC_DIR")
    if env_mcdir and Path(env_mcdir).exists():
        return Path(env_mcdir)
    script_dir = Path(__file__).parent
    if (script_dir / "server.jar").exists():
        return script_dir
    candidates = []
    if IS_WINDOWS:
        candidates = [
            Path("C:/minecraft"), Path("D:/minecraft"), Path("E:/minecraft"),
            Path.home() / "minecraft", Path.home() / "Desktop" / "minecraft",
            Path(os.environ.get("USERPROFILE", "")) / "minecraft",
        ]
    else:
        candidates = [
            Path("/minecraft"), Path("/home/minecraft"), Path("/opt/minecraft"),
            Path.home() / "minecraft", Path("/srv/minecraft"),
            Path("/var/minecraft"),
        ]
    for p in candidates:
        if (p / "server.jar").exists():
            return p
    for p in candidates:
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(_default_mcdir)


MC_DIR = _find_mc_dir()
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
        return f"Java {java_ver} found but server requires Java 17+."

    run_sh = MC_DIR / "run.sh"
    run_bat = MC_DIR / "run.bat"

    if IS_WINDOWS and run_bat.exists():
        java_cmd = ["cmd", "/c", str(run_bat), "nogui"]
    elif run_sh.exists():
        java_cmd = ["bash", str(run_sh), "nogui"]
    elif (MC_DIR / "server.jar").exists():
        java_cmd = java_bin.split() + java_args.split() + ["-jar", "server.jar", "nogui"]
    else:
        return "No server.jar found. Install a core first."

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
    _log_panel_message("[Panel] Server started")
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


def _log_panel_message(msg):
    try:
        log_path = MC_DIR / "logs"
        log_path.mkdir(parents=True, exist_ok=True)
        with open(log_path / "latest.log", "a", encoding="utf-8", errors="replace") as f:
            f.write(msg + "\n")
            f.flush()
    except Exception:
        pass


def stop_server():
    if not is_server_running():
        return "not running"

    send_command("stop")
    _log_panel_message("[Panel] Server stop command sent")
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
        if IS_WINDOWS:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty TotalVisibleMemorySize"],
                capture_output=True, text=True, timeout=5
            )
            total = 0
            try:
                total = int(r.stdout.strip()) // 1024
            except (ValueError, TypeError):
                pass
            r2 = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty FreePhysicalMemory"],
                capture_output=True, text=True, timeout=5
            )
            avail = 0
            try:
                avail = int(r2.stdout.strip()) // 1024
            except (ValueError, TypeError):
                pass
            if total > 0:
                info["memory"] = {
                    "total_mb": total,
                    "used_mb": total - avail,
                    "free_mb": avail
                }
        else:
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

    try:
        import shutil as _shutil
        disk_total, disk_used, disk_free = _shutil.disk_usage(str(MC_DIR))
        info["disk"] = {
            "total_mb": disk_total // (1024 * 1024),
            "used_mb": disk_used // (1024 * 1024),
            "free_mb": disk_free // (1024 * 1024),
            "percent": round(disk_used / disk_total * 100, 1) if disk_total > 0 else 0
        }
    except Exception:
        info["disk"] = {"total_mb": 0, "used_mb": 0, "free_mb": 0, "percent": 0}

    info["cpu_percent"] = 0
    if running:
        try:
            if IS_WINDOWS:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | Where-Object {$_.Name -eq 'java'} | Select-Object -ExpandProperty PercentProcessorTime"],
                    capture_output=True, text=True, timeout=5
                )
                cpus = [float(x.strip()) for x in r.stdout.strip().split("\n") if x.strip()]
                if cpus:
                    cores = os.cpu_count() or 1
                    info["cpu_percent"] = round(sum(cpus) / cores, 1)
            else:
                r = subprocess.run(["pgrep", "-x", "java"], capture_output=True, text=True, timeout=3)
                pids = [p.strip() for p in r.stdout.strip().split("\n") if p.strip()]
                if pids:
                    r2 = subprocess.run(["ps", "-p", ",".join(pids), "-o", "%cpu=", "--no-headers"], capture_output=True, text=True, timeout=3)
                    cpus = [float(x.strip()) for x in r2.stdout.strip().split("\n") if x.strip()]
                    if cpus:
                        cores = os.cpu_count() or 1
                        info["cpu_percent"] = round(sum(cpus) / cores, 1)
        except Exception:
            pass

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


SETTINGS_FILE = Path(__file__).parent / "settings.json"



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
    existing = {}
    if SETTINGS_FILE.exists():
        try:
            existing = json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
    existing.update(data)
    SETTINGS_FILE.write_text(json.dumps(existing, indent=2))







def _format_size(size):
    if size > 1024 * 1024:
        return f"{size / (1024*1024):.1f} MB"
    elif size > 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _dir_entries(base):
    entries = []
    try:
        for f in sorted(base.iterdir()):
            skip = f.name in ("panel", ".git", "__pycache__")
            if f.is_file():
                if skip:
                    continue
                size = f.stat().st_size
                entries.append({"name": f.name, "size": _format_size(size), "size_bytes": size, "type": "file"})
            elif f.is_dir():
                if skip:
                    continue
                entries.append({"name": f.name, "size": "-", "size_bytes": 0, "type": "dir"})
    except Exception:
        pass
    return entries


@app.api_route("/")
async def index(request: Request):
    settings = load_settings()
    accent = settings.get("accent", "#6c5ce7")
    fireflies = "true" if settings.get("fireflies") else "false"
    opacity = str(settings.get("panel_opacity", 100))
    username = request.session.get("username")
    user = load_users().get(username, {})
    # First-run panels retain full local access so the owner can create the
    # initial `admin` account from the new Users page.
    first_run = not load_users()
    permissions = json.dumps(list(PERMISSIONS) if first_run else user_permissions(user))
    is_admin = "true" if first_run or user.get("role") == "admin" else "false"
    page = HTML_TEMPLATE.replace("%ACCENT%", accent).replace("%FIREFLIES%", fireflies).replace("%OPACITY%", opacity)
    page = page.replace("%PERMISSIONS%", permissions).replace("%IS_ADMIN%", is_admin)
    return HTMLResponse(page)


@app.api_route("/api/status")
async def api_status(request: Request):
    info = get_server_info()
    jar_files = list(MC_DIR.glob("*.jar"))
    if jar_files:
        info["server_jar"] = jar_files[0].name
    return JSONResponse(info)


@app.api_route("/api/console")
async def api_console(request: Request):
    n = int(request.query_params.get("lines", 100))
    return JSONResponse({"lines": get_console_lines(n)})


@app.api_route("/api/server")
async def api_server(request: Request):
    action = request.query_params.get("action", "status")
    if action == "start":
        setup_server()
        msg = start_server()
        return JSONResponse({"message": msg})
    elif action == "stop":
        msg = stop_server()
        return JSONResponse({"message": msg})
    return JSONResponse({"running": is_server_running()})


@app.api_route("/api/json")
async def api_json(request: Request):
    file = request.query_params.get("file", "")
    if file:
        return JSONResponse(read_json_file(file))
    return JSONResponse([])


@app.api_route("/api/properties", methods=["GET", "POST"])
async def api_properties(request: Request):
    if request.method == "POST":
        body = await request.json()
        props = read_properties()
        props.update(body)
        write_properties(props)
        return JSONResponse({"message": "Properties saved. Restart server to apply."})
    return JSONResponse(read_properties())


@app.api_route("/api/plugins")
async def api_plugins(request: Request):
    return JSONResponse(list_plugins())


@app.api_route("/api/mods")
async def api_mods(request: Request):
    return JSONResponse(list_mods())


@app.api_route("/api/online")
async def api_online(request: Request):
    return JSONResponse(get_online_players())


@app.api_route("/api/file-exists")
async def api_file_exists(request: Request):
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
    return JSONResponse({"exists": exists})


@app.api_route("/api/settings", methods=["GET", "POST"])
async def api_settings(request: Request):
    if request.method == "POST":
        body = await request.json()
        save_settings(body)
        if "accent" in body:
            _env["ACCENT_COLOR"] = body["accent"]
        return JSONResponse({"message": "Settings saved."})
    return JSONResponse(load_settings())


@app.api_route("/api/lang")
async def api_lang(request: Request):
    settings = load_settings()
    lang = settings.get("lang", "en")
    return JSONResponse(TRANSLATIONS.get(lang, TRANSLATIONS["en"]))


@app.api_route("/api/me")
async def api_me(request: Request):
    username = request.session.get("username")
    user = load_users().get(username, {})
    return JSONResponse({"username": username, "role": user.get("role", "user"),
                         "permissions": user_permissions(user)})


@app.api_route("/api/users", methods=["GET", "POST", "PUT", "DELETE"])
async def api_users(request: Request):
    users = load_users()
    if request.method == "GET":
        return JSONResponse({"users": [
            {"username": name, "role": data.get("role", "user"),
             "permissions": user_permissions(data)}
            for name, data in sorted(users.items())
        ]})

    body = await request.json()
    username = str(body.get("username", "")).strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,32}", username):
        return JSONResponse({"error": "Username: 3–32 Latin letters, digits, ., _ or -"}, status_code=400)

    if not users and request.method == "POST" and username != "admin":
        return JSONResponse({"error": "Create the initial account with username admin"}, status_code=400)

    if request.method == "POST":
        password = str(body.get("password", ""))
        if username in users:
            return JSONResponse({"error": "User already exists"}, status_code=409)
        if not _validate_password(password):
            return JSONResponse({"error": "Password too weak (min 5 chars, no common words)"}, status_code=400)
        # The first account is always the administrator, otherwise a panel
        # could be permanently locked with no-one able to manage accounts.
        role = "admin" if not users or body.get("role") == "admin" else "user"
        requested = body.get("permissions", [])
        permissions = [p for p in requested if p in PERMISSIONS] if isinstance(requested, list) else []
        users[username] = {"role": role, "password_hash": _password_hash(password), "permissions": permissions}
        save_users(users)
        return JSONResponse({"message": "User created"})

    if username not in users:
        return JSONResponse({"error": "User not found"}, status_code=404)
    if request.method == "PUT":
        renamed_from = None
        new_username = str(body.get("new_username", "")).strip().lower()
        if new_username and new_username != username:
            if not re.fullmatch(r"[a-z0-9_.-]{3,32}", new_username):
                return JSONResponse({"error": "Username: 3–32 Latin letters, digits, ., _ or -"}, status_code=400)
            if new_username in users:
                return JSONResponse({"error": "User already exists"}, status_code=409)
            users[new_username] = users.pop(username)
            renamed_from = username
            username = new_username
        if "role" in body:
            users[username]["role"] = "admin" if body["role"] == "admin" else "user"
        if "permissions" in body and isinstance(body["permissions"], list):
            users[username]["permissions"] = [p for p in body["permissions"] if p in PERMISSIONS]
        password = str(body.get("password", ""))
        if password:
            if not _validate_password(password):
                return JSONResponse({"error": "Password too weak (min 5 chars, no common words)"}, status_code=400)
            users[username]["password_hash"] = _password_hash(password)
        save_users(users)
        if renamed_from and request.session.get("username") == renamed_from:
            request.session["username"] = username
            request.session["authenticated"] = True
        return JSONResponse({"message": "User updated"})

    if username == request.session.get("username"):
        return JSONResponse({"error": "You cannot delete your own account"}, status_code=400)
    if users[username].get("role") == "admin" and sum(u.get("role") == "admin" for u in users.values()) <= 1:
        return JSONResponse({"error": "At least one administrator is required"}, status_code=400)
    del users[username]
    save_users(users)
    return JSONResponse({"message": "User deleted"})


@app.api_route("/api/env-info")
async def api_env_info(request: Request):
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
    return JSONResponse({
        "mc_dir": _env.get("MC_DIR", str(MC_DIR)),
        "port": int(_env.get("PANEL_PORT", str(PANEL_PORT))),
        "lang": _env.get("PANEL_LANG", "en"),
        "platform": "Windows" if IS_WINDOWS else "Linux",
        "java_path": java_bin,
        "java_version": java_ver,
        "rcon_enabled": rcon["enabled"],
        "rcon_port": rcon["port"],
        "token_set": bool(_get_panel_token()),
        "encoding": _env.get("JAVA_ENCODING", "") or "auto",
        "java_args": _env.get("JAVA_ARGS", "")
    })


@app.api_route("/api/files")
async def api_files(request: Request):
    subpath = request.query_params.get("path", "")
    base = MC_DIR / subpath if subpath else MC_DIR
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": "Directory not found"}, status_code=404)
    return JSONResponse({"files": _dir_entries(base)})


@app.api_route("/api/file-read")
async def api_file_read(request: Request):
    name = request.query_params.get("name", "")
    if not name:
        return JSONResponse({"error": "No name"}, status_code=400)
    fpath = MC_DIR / name
    if not fpath.exists() or not fpath.is_file():
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        text = fpath.read_text(encoding="utf-8", errors="replace")
        return JSONResponse({"name": name, "content": text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.api_route("/api/search")
async def api_search(request: Request):
    query = request.query_params.get("q", "").strip().lower()
    subpath = request.query_params.get("path", "")
    if not query:
        return JSONResponse({"files": []})
    base = MC_DIR / subpath if subpath else MC_DIR
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": "Directory not found"}, status_code=404)
    skip_dirs = {"panel", ".git", "__pycache__"}
    results = []
    try:
        for f in base.rglob("*"):
            if len(results) >= 200:
                break
            if f.is_dir():
                if f.name in skip_dirs:
                    continue
                continue
            if query not in f.name.lower():
                continue
            rel = f.relative_to(MC_DIR)
            skip = rel.parts[0] in skip_dirs if rel.parts else False
            if skip:
                continue
            size = f.stat().st_size
            results.append({
                "name": f.name,
                "path": str(rel),
                "size": _format_size(size),
                "size_bytes": size,
                "type": "file"
            })
    except Exception:
        pass
    return JSONResponse({"files": results})


@app.api_route("/api/upload-core", methods=["POST"])
async def api_upload_core(request: Request):
    try:
        form = await request.form()
    except Exception as exc:
        return JSONResponse({"error": f"Unable to read upload: {exc}"}, status_code=400)
    if "file" not in form:
        return JSONResponse({"error": "No file"}, status_code=400)
    file_item = form["file"]
    filename = getattr(file_item, "filename", "")
    if not filename or not filename.lower().endswith(".jar"):
        return JSONResponse({"error": "File must be a .jar"}, status_code=400)
    try:
        data = await file_item.read()
    except Exception as exc:
        return JSONResponse({"error": f"Unable to read uploaded file: {exc}"}, status_code=400)
    if not data:
        return JSONResponse({"error": "Uploaded file is empty"}, status_code=400)

    # Write the new core first.  Replacing the file is atomic on the same
    # filesystem, so a failed upload never removes a working server.jar.
    temp_core = MC_DIR / ".server.jar.uploading"
    try:
        MC_DIR.mkdir(parents=True, exist_ok=True)
        temp_core.write_bytes(data)
        temp_core.replace(MC_DIR / "server.jar")
    except OSError as exc:
        try:
            temp_core.unlink(missing_ok=True)
        except OSError:
            pass
        return JSONResponse({"error": f"Could not save server core: {exc}"}, status_code=500)

    # Remove any remaining old core jars only after server.jar is safely saved.
    for old_jar in MC_DIR.glob("*.jar"):
        if old_jar.name == "server.jar":
            continue
        try:
            old_jar.unlink()
        except OSError:
            pass

    keep_map = {
        "world": ["world", "world_nether", "world_the_end"],
        "mods": ["mods"],
        "plugins": ["plugins"],
        "ops.json": ["ops.json"],
        "banned-players.json": ["banned-players.json"],
        "banned-ips.json": ["banned-ips.json"],
        "whitelist.json": ["whitelist.json"],
        "server.properties": ["server.properties"],
    }

    keep_str = form.get("keep_data", "{}")
    del_paths = set()
    try:
        del_paths = set(json.loads(keep_str).keys())
    except Exception:
        pass

    keep_paths = set()
    for path_key, targets in keep_map.items():
        if path_key not in del_paths:
            keep_paths.update(targets)

    protected = {"panel", ".git", "__pycache__", "server.jar", "eula.txt", "panel.tar", "README.md", ".env"}

    deleted = []
    try:
        for item in MC_DIR.iterdir():
            name = item.name
            if name in protected or name in keep_paths:
                continue
            if item.is_dir():
                shutil.rmtree(item)
                deleted.append(name + "/")
            elif item.is_file():
                item.unlink()
                deleted.append(name)
    except Exception:
        pass

    try:
        setup_server()
    except OSError as exc:
        return JSONResponse({"error": f"Core saved, but server setup failed: {exc}"}, status_code=500)

    msg = f"Core uploaded: {filename}. EULA accepted."
    if deleted:
        msg += f" Deleted: {', '.join(deleted)}"
    return JSONResponse({"message": msg, "ok": True})


@app.api_route("/api/upload", methods=["POST"])
async def api_upload(request: Request):
    form = await request.form()
    if "file" not in form:
        return JSONResponse({"error": "No file"}, status_code=400)
    file_item = form["file"]
    ftype = form.get("type", "")
    target = "mods" if ftype == "mods" else "plugins"
    if not file_item.filename:
        return JSONResponse({"error": "No file"}, status_code=400)
    data = await file_item.read()
    result = save_upload(target, file_item.filename, data)
    return JSONResponse({"message": f"Uploaded {result['name']} ({result['size']} bytes)", "ok": True})


@app.api_route("/api/delete", methods=["POST"])
async def api_delete(request: Request):
    body = await request.json()
    ftype = body.get("type")
    name = body.get("name")
    target = "mods" if ftype == "mods" else "plugins"
    if delete_file(target, name):
        return JSONResponse({"message": f"Deleted {name}"})
    return JSONResponse({"error": "File not found"}, status_code=404)


@app.api_route("/api/delete-all", methods=["POST"])
async def api_delete_all(request: Request):
    body = await request.json()
    ftype = body.get("type")
    target = "mods" if ftype == "mods" else "plugins"
    d = MC_DIR / target
    count = 0
    if d.exists():
        for f in d.iterdir():
            if f.is_file() and f.suffix == ".jar":
                f.unlink()
                count += 1
    return JSONResponse({"message": f"Deleted {count} files from {target}/"})


@app.api_route("/api/command", methods=["POST"])
async def api_command(request: Request):
    body = await request.json()
    cmd = body.get("cmd", "")
    if cmd:
        ok = send_command(cmd)
        return JSONResponse({"ok": ok})
    return JSONResponse({"ok": False})


@app.api_route("/api/player", methods=["POST"])
async def api_player(request: Request):
    body = await request.json()
    action = body.get("action")
    ptype = body.get("type")
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "Name required"})

    files = {"ops": "ops.json", "whitelist": "whitelist.json", "ban": "banned-players.json"}
    file = files.get(ptype)
    if not file:
        return JSONResponse({"error": "unknown type"})

    server_up = is_server_running()

    cmd_map_add = {"ops": "op", "ban": "ban", "whitelist": "whitelist add"}
    cmd_map_remove = {"ops": "deop", "ban": "pardon", "whitelist": "whitelist remove"}

    if action == "add":
        if server_up:
            cmd = f"{cmd_map_add[ptype]} {name}"
            send_command(cmd)
            return JSONResponse({"message": f"{name} added to {ptype} (command sent)"})
        else:
            data = read_json_file(file)
            if not isinstance(data, list):
                data = []
            exists = any(
                isinstance(p, dict) and p.get("name", "").lower() == name.lower()
                for p in data
            )
            if exists:
                return JSONResponse({"message": f"{name} already in {ptype}"})
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
            return JSONResponse({"message": f"{name} added to {ptype}"})

    elif action == "remove":
        if server_up:
            cmd = f"{cmd_map_remove[ptype]} {name}"
            send_command(cmd)
            return JSONResponse({"message": f"{name} removed from {ptype} (command sent)"})
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
                return JSONResponse({"message": f"{name} removed from {ptype}"})
            return JSONResponse({"message": f"{name} not found in {ptype}"})
    return JSONResponse({"error": "unknown action"})


@app.api_route("/api/save-env", methods=["POST"])
async def api_save_env(request: Request):
    body = await request.json()
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
    return JSONResponse({"message": "Config saved. Restart panel to apply."})


@app.api_route("/api/file-write", methods=["POST"])
async def api_file_write(request: Request):
    body = await request.json()
    name = body.get("name", "")
    content = body.get("content", "")
    if not name:
        return JSONResponse({"error": "No name"}, status_code=400)
    fpath = MC_DIR / name
    if ".." in name or name.startswith("/"):
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    try:
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
        return JSONResponse({"message": f"Saved {name}"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.api_route("/api/file-upload", methods=["POST"])
async def api_file_upload(request: Request):
    form = await request.form()
    if "file" not in form:
        return JSONResponse({"error": "No file"}, status_code=400)
    file_item = form["file"]
    target_dir = form.get("path", "")
    if not file_item.filename:
        return JSONResponse({"error": "No filename"}, status_code=400)
    if ".." in target_dir or target_dir.startswith("/"):
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    base = MC_DIR / target_dir if target_dir else MC_DIR
    base.mkdir(parents=True, exist_ok=True)
    fpath = base / file_item.filename
    fpath.write_bytes(await file_item.file.read())
    return JSONResponse({"message": f"Uploaded {file_item.filename}", "ok": True})


@app.api_route("/api/download-core", methods=["POST"])
async def api_download_core(request: Request):
    body = await request.json()
    url = body.get("url", "")
    keep_str = body.get("keep_data", "{}")
    if not url:
        return JSONResponse({"error": "No URL"}, status_code=400)
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "FizMinePanel/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()

        is_forge_installer = ("forge" in url.lower() or "neoforge" in url.lower()) and "installer" in url.lower()
        
        if is_forge_installer:
            for item in MC_DIR.iterdir():
                if item.name in ("libraries", "versions", "neoforge", "forge"):
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
            for f in MC_DIR.glob("user_jvm_args.txt"):
                f.unlink(missing_ok=True)
            
            installer_path = MC_DIR / "installer.jar"
            installer_path.write_bytes(data)
            java_bin = find_java()
            
            install_args = [java_bin, "-jar", str(installer_path), "--installServer"]
            
            log_file = MC_DIR / "installer.log"
            with open(log_file, "w") as lf:
                r = subprocess.run(
                    install_args,
                    cwd=str(MC_DIR), stdout=lf, stderr=subprocess.STDOUT, timeout=600
                )
            
            stdout_text = ""
            try:
                stdout_text = log_file.read_text(errors="replace")[-1000:]
            except:
                pass
            
            installer_path.unlink(missing_ok=True)
            log_file.unlink(missing_ok=True)
            
            if r.returncode != 0:
                return JSONResponse({"error": f"Installer failed (code {r.returncode}): {stdout_text[:1000]}"}, status_code=500)
            
            if not (MC_DIR / "libraries").exists():
                return JSONResponse({"error": f"No libraries. Java: {java_bin}. Output: {stdout_text[:500]}"}, status_code=500)
            
            eula_path = MC_DIR / "eula.txt"
            if not eula_path.exists():
                eula_path.write_text("eula=true\n")
            
            forge_jars = list(MC_DIR.glob("forge-*-server.jar"))
            if not forge_jars:
                forge_jars = list(MC_DIR.glob("neoforge-*-server.jar"))
            if not forge_jars:
                forge_jars = list(MC_DIR.glob("**/forge-*-server.jar"))
            if not forge_jars:
                forge_jars = list(MC_DIR.glob("**/neoforge-*-server.jar"))
            if not forge_jars:
                forge_jars = list(MC_DIR.glob("**/forge-*-universal.jar"))
            if not forge_jars:
                forge_jars = list(MC_DIR.glob("**/neoforge-*-universal.jar"))
            if not forge_jars:
                forge_jars = list(MC_DIR.glob("**/libraries/**/neoforge-*-server.jar"))
            if not forge_jars:
                forge_jars = list(MC_DIR.glob("**/libraries/**/forge-*-server.jar"))
            if not forge_jars:
                forge_jars = list(MC_DIR.glob("**/neoforge-*.jar"))
            if not forge_jars:
                forge_jars = list(MC_DIR.glob("**/forge-*.jar"))
            if forge_jars:
                target = forge_jars[0]
                if target != MC_DIR / "server.jar":
                    target.rename(MC_DIR / "server.jar")
            else:
                all_jars = list(MC_DIR.glob("**/*.jar"))
                jar_names = [j.name for j in all_jars[:10]]
                return JSONResponse({"error": f"No server jar found. Found: {jar_names}"}, status_code=500)
        else:
            fpath = MC_DIR / "server.jar"
            fpath.write_bytes(data)

        del_paths = set()
        try:
            kd = body.get("keep_data", "{}")
            if isinstance(kd, dict):
                del_paths = set(kd.keys())
            else:
                del_paths = set(json.loads(kd).keys())
        except Exception:
            pass

        keep_map = {
            "world": ["world", "world_nether", "world_the_end"],
            "mods": ["mods"],
            "plugins": ["plugins"],
            "ops.json": ["ops.json"],
            "banned-players.json": ["banned-players.json"],
            "banned-ips.json": ["banned-ips.json"],
            "whitelist.json": ["whitelist.json"],
            "server.properties": ["server.properties"],
        }
        keep_paths = set()
        for path_key, targets in keep_map.items():
            if path_key not in del_paths:
                keep_paths.update(targets)

        forge_names = set()
        for item in MC_DIR.iterdir():
            if item.name.startswith("forge-") or item.name.startswith("neoforge-") or item.name.startswith("minecraft_server"):
                forge_names.add(item.name)

        protected = {"panel", ".git", "__pycache__", "server.jar", "eula.txt", "panel.tar", "libraries", "run.sh", "run.bat", "user_jvm_args.txt", "forge"} | forge_names
        deleted = []
        for item in MC_DIR.iterdir():
            name = item.name
            if name in protected or name in keep_paths:
                continue
            if item.is_dir():
                shutil.rmtree(item)
                deleted.append(name + "/")
            elif item.is_file():
                item.unlink()
                deleted.append(name)

        setup_server()
        msg = f"Core downloaded: {url.split('/')[-1]}. EULA accepted."
        if deleted:
            msg += f" Deleted: {', '.join(deleted)}"
        return JSONResponse({"message": msg, "ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.api_route("/api/file-mkdir", methods=["POST"])
async def api_file_mkdir(request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    subpath = body.get("path", "")
    if not name:
        return JSONResponse({"error": "Folder name required"}, status_code=400)
    if ".." in name or "/" in name or name.startswith("."):
        return JSONResponse({"error": "Invalid folder name"}, status_code=400)
    if ".." in subpath or subpath.startswith("/"):
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    base = MC_DIR / subpath if subpath else MC_DIR
    fpath = base / name
    if fpath.exists():
        return JSONResponse({"error": "Already exists"}, status_code=400)
    try:
        fpath.mkdir(parents=True, exist_ok=True)
        return JSONResponse({"message": f"Created {name}", "ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.api_route("/api/file-download")
async def api_file_download(request: Request):
    name = request.query_params.get("name", "")
    if not name:
        return JSONResponse({"error": "No name"}, status_code=400)
    if ".." in name or name.startswith("/"):
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    fpath = MC_DIR / name
    if not fpath.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    if fpath.is_dir():
        import tempfile, zipfile
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        try:
            with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(str(fpath)):
                    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "panel")]
                    for file in files:
                        fp = Path(root) / file
                        arcname = str(fp.relative_to(fpath))
                        zf.write(str(fp), arcname)
            return FileResponse(tmp.name, filename=fpath.name + ".zip",
                             media_type="application/zip")
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
    return FileResponse(str(fpath), filename=fpath.name)


@app.api_route("/api/backup-panel")
async def api_backup_panel(request: Request):
    import tempfile, zipfile
    panel_dir = Path(__file__).parent
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(str(panel_dir)):
                dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
                for file in files:
                    fp = Path(root) / file
                    arcname = str(fp.relative_to(panel_dir))
                    zf.write(str(fp), arcname)
        return FileResponse(tmp.name, filename="panel-backup.zip",
                         media_type="application/zip")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.api_route("/api/backup-server")
async def api_backup_server(request: Request):
    import tempfile, zipfile
    skip = {"panel", ".git", "__pycache__", "logs", "cache", "crash-reports"}
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(str(MC_DIR)):
                dirs[:] = [d for d in dirs if d not in skip]
                for file in files:
                    fp = Path(root) / file
                    arcname = str(fp.relative_to(MC_DIR))
                    zf.write(str(fp), arcname)
        return FileResponse(tmp.name, filename="server-backup.zip",
                         media_type="application/zip")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.api_route("/api/set-token", methods=["POST"])
async def api_set_token(request: Request):
    body = await request.json()
    token = body.get("token", "").strip()
    if not token:
        return JSONResponse({"error": "Password required"}, status_code=400)
    if not _validate_password(token):
        return JSONResponse({"error": "Password too weak (min 5 chars, no common words)"}, status_code=400)
    _set_panel_token(token)
    return JSONResponse({"message": "Password saved"})


@app.api_route("/api/remove-token", methods=["POST"])
async def api_remove_token(request: Request):
    _set_panel_token("")
    request.session.pop("authenticated", None)
    return JSONResponse({"message": "Token removed"})


@app.api_route("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


@app.api_route("/api/file-delete", methods=["POST"])
async def api_file_delete(request: Request):
    body = await request.json()
    name = body.get("name", "")
    if not name:
        return JSONResponse({"error": "No name"}, status_code=400)
    if ".." in name or name.startswith("/"):
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    fpath = MC_DIR / name
    if not fpath.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    protected = {"panel", ".git", "__pycache__", "server.jar", "eula.txt", "panel.tar"}
    rel = str(fpath.relative_to(MC_DIR))
    top = rel.split("/")[0] if "/" in rel else rel
    if top in protected:
        return JSONResponse({"error": "Protected file"}, status_code=403)
    try:
        if fpath.is_dir():
            shutil.rmtree(fpath)
        else:
            fpath.unlink()
        return JSONResponse({"message": f"Deleted {name}", "ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.api_route("/api/check-update")
async def api_check_update(request: Request):
    import urllib.request
    try:
        local_ver = PANEL_VERSION
        
        req = urllib.request.Request(
            "https://raw.githubusercontent.com/fizyCH/FizMine/main/app.py?t=" + str(int(time.time())),
            headers={"User-Agent": "FizMinePanel/2.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            remote = resp.read(200000).decode("utf-8", errors="replace")
        remote_ver = re.search(r'PANEL_VERSION\s*=\s*"([^"]+)"', remote)
        remote_ver = remote_ver.group(1) if remote_ver else "0"
        
        return JSONResponse({"update": remote_ver != local_ver, "local": local_ver, "remote": remote_ver})
    except Exception as e:
        return JSONResponse({"update": False, "error": str(e)})


@app.api_route("/api/do-update", methods=["POST"])
async def api_do_update(request: Request):
    import urllib.request
    try:
        local_ver = PANEL_VERSION
        
        base_url = "https://raw.githubusercontent.com/fizyCH/FizMine/main/"
        def fetch_file(relative):
            req = urllib.request.Request(base_url + relative + "?t=" + str(int(time.time())), headers={"User-Agent": "FizMine-Panel"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        remote = fetch_file("app.py")
        
        remote_ver = re.search(r'PANEL_VERSION\s*=\s*"([^"]+)"', remote)
        remote_ver = remote_ver.group(1) if remote_ver else "0"
        
        if remote_ver == local_ver:
            return JSONResponse({"ok": True, "message": "Already up to date"})
        
        root = Path(__file__).resolve().parent
        updates = {"app.py": remote, "panel.py": fetch_file("panel.py")}
        for module in ("__init__.py", "auth.py", "users.py", "server.py", "files.py", "settings.py", "rcon.py", "backup.py", "templates.py"):
            updates[f"panel_modules/{module}"] = fetch_file(f"panel_modules/{module}")
        for relative, content in updates.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists(): shutil.copy2(str(target), str(target) + ".bak")
            target.write_text(content, encoding="utf-8")
        
        def restart():
            time.sleep(2)
            subprocess.Popen([sys.executable] + sys.argv, cwd=str(Path(__file__).parent))
            if IS_WINDOWS:
                os.kill(os.getpid(), signal.SIGTERM)
            else:
                os._exit(0)
        threading.Thread(target=restart, daemon=True).start()
        
        return JSONResponse({"ok": True, "message": "Updated! Restarting..."})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def setup_initial_account():
    """Interactive first-run account creation used by the installer script."""
    users = load_users()
    if users:
        return 0
    print("\nFizMine: создание администратора панели")
    username = os.environ.get("FIZMINE_ADMIN_USERNAME", "").strip().lower() or input("Логин: ").strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,32}", username):
        print("Ошибка: логин должен содержать 3–32 символа (a-z, 0-9, ., _, -)")
        return 1
    password = os.environ.get("FIZMINE_ADMIN_PASSWORD") or getpass.getpass("Пароль: ")
    if not _validate_password(password):
        print("Ошибка: пароль минимум 5 символов и не должен быть распространённым")
        return 1
    confirmation = os.environ.get("FIZMINE_ADMIN_PASSWORD_CONFIRM") or getpass.getpass("Повторите пароль: ")
    if password != confirmation:
        print("Ошибка: пароли не совпадают")
        return 1
    save_users({username: {"role": "admin", "password_hash": _password_hash(password), "permissions": list(PERMISSIONS)}})
    print(f"Администратор {username} создан.")
    return 0


def main():
    MC_DIR.mkdir(parents=True, exist_ok=True)
    ensure_default_admin()
    
    java_bin = find_java()
    java_ver = 0
    try:
        r = subprocess.run([java_bin, "-version"], capture_output=True, text=True, timeout=5)
        m = re.search(r'"(\d+)', (r.stderr + r.stdout))
        if m:
            java_ver = int(m.group(1))
    except Exception:
        pass
    
    if java_ver == 0:
        print("WARNING: Java not found! Server cores may not work.")
    elif java_ver < 17:
        print(f"WARNING: Java {java_ver} found, but 17+ recommended for modern versions.")
    else:
        print(f"Java {java_ver} detected")
    
    print(f"FizMine Panel starting on http://0.0.0.0:{PANEL_PORT}")
    print(f"Minecraft directory: {MC_DIR}")
    
    uvicorn.run(app, host="0.0.0.0", port=PANEL_PORT)


if __name__ == "__main__":
    if "--setup-account" in sys.argv:
        raise SystemExit(setup_initial_account())
    if "--ensure-account" in sys.argv:
        ensure_default_admin()
        raise SystemExit(0)
    main()
