#!/usr/bin/env python3
import io
import hashlib
import hmac
import json
import getpass
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

PANEL_VERSION = "2.1"
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


LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FizMine Panel - Login</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#050505;color:#e0e0e0;min-height:100vh;display:flex;justify-content:center;align-items:center;position:relative;overflow:hidden}
.login-card{position:relative;z-index:1;background:#111;border:1px solid #222;border-radius:16px;padding:3rem 3rem;width:100%;max-width:480px;animation:slideUp .6s ease-out;box-shadow:0 20px 60px rgba(%ACCTR%,0.2)}
@keyframes slideUp{from{opacity:0;transform:translateY(30px)}to{opacity:1;transform:translateY(0)}}
.logo{text-align:center;margin-bottom:2rem}
.logo h1{font-family:'Press Start 2P',monospace;font-size:2.5rem;font-weight:bold;background:linear-gradient(135deg,%ACCENT%,%ACCENT%);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.logo p{color:#888;font-size:.9rem;margin-top:.5rem}
.form-input{width:100%;padding:1rem;background:#050505;border:2px solid #222;border-radius:8px;color:#e0e0e0;font-size:1rem;margin-bottom:1rem;font-family:'Courier New',monospace;transition:all .3s}
.form-input:focus{outline:none;border-color:%ACCENT%;box-shadow:0 0 20px rgba(%ACCTR%,0.3)}
.btn{width:100%;padding:1rem;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:1rem;background:linear-gradient(135deg,%ACCENT%,%ACCENT%);color:#fff;transition:all .3s}
.btn:hover{transform:translateY(-2px);box-shadow:0 10px 30px rgba(%ACCTR%,0.4)}
.error{background:rgba(239,68,68,0.1);border:1px solid #ef4444;color:#ef4444;padding:1rem;border-radius:8px;margin-bottom:1rem;display:none}
.error.show{display:block}
</style>
</head>
<body>
<div class="login-card">
<div style="height:48px;display:flex;align-items:center;justify-content:center;margin-bottom:1.2rem">
 <span id="greeting" style="font-size:1.5rem;font-weight:600;color:%ACCENT%;text-shadow:0 0 20px rgba(%ACCTR%,0.5),0 0 40px rgba(%ACCTR%,0.2);transition:opacity .5s;text-transform:uppercase;letter-spacing:2px"></span>
</div>
<div class="logo"><div style="width:80px;height:80px;background:linear-gradient(135deg,%ACCENT%,%ACCENT%);border-radius:18px;display:flex;align-items:center;justify-content:center;margin:0 auto 14px;box-shadow:0 4px 30px rgba(%ACCTR%,.4)"><svg viewBox="0 0 28 22" shape-rendering="crispEdges" style="width:48px;height:38px;fill:%LOGOCOLOR%"><g fill="rgba(0,0,0,.25)" transform="translate(1.5,1.5)"><rect x="1" y="2" width="3" height="17"/><rect x="4" y="2" width="7" height="3"/><rect x="4" y="9" width="5" height="3"/><rect x="13" y="2" width="3" height="17"/><rect x="16" y="5" width="2" height="3"/><rect x="18" y="8" width="2" height="3"/><rect x="20" y="5" width="2" height="3"/><rect x="22" y="2" width="3" height="17"/></g><g fill="currentColor"><rect x="1" y="1" width="3" height="17"/><rect x="4" y="1" width="7" height="3"/><rect x="4" y="8" width="5" height="3"/><rect x="13" y="1" width="3" height="17"/><rect x="16" y="4" width="2" height="3"/><rect x="18" y="7" width="2" height="3"/><rect x="20" y="4" width="2" height="3"/><rect x="22" y="1" width="3" height="17"/></g><g fill="rgba(255,255,255,.15)"><rect x="1" y="1" width="3" height="2"/><rect x="4" y="1" width="7" height="1"/><rect x="13" y="1" width="3" height="2"/><rect x="22" y="1" width="3" height="2"/></g></svg></div>
<p style="font-family:'Press Start 2P',monospace;font-size:0.85rem;margin-top:1.2rem;letter-spacing:3px;color:%TEXT%">Minecraft Panel</p></div>
<div class="error" id="errorBox"></div>
<form method="POST" action="/login">
<input type="text" name="username" class="form-input" placeholder="%LOGIN_USER%" required autofocus autocomplete="username">
<input type="password" name="token" class="form-input" placeholder="%LOGIN_PH%" required autocomplete="current-password" id="token-input">
<button type="submit" class="btn" id="login-btn">%LOGIN_BTN%</button>
</form>
</div>
<canvas id="login-fireflies" style="position:fixed;inset:0;z-index:0;pointer-events:none"></canvas>
<script>var LOGIN_ACCENT='%ACCENT%';</script>
<script>
(function(){
 var c=document.getElementById('login-fireflies');
 c.width=window.innerWidth;c.height=window.innerHeight;
 var dots=[];var N=20;
for(var i=0;i<N;i++)dots.push({x:Math.random()*c.width,y:Math.random()*c.height,r:Math.random()*2+1,dx:(Math.random()-.5)*.4,dy:(Math.random()-.5)*.4,a:Math.random(),da:-0.004});
  var accent=LOGIN_ACCENT||'#6c5ce7';
  var ar=parseInt(accent.slice(1,3),16),ag=parseInt(accent.slice(3,5),16),ab=parseInt(accent.slice(5,7),16);
  function draw(){
   var ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);
   for(var i=0;i<dots.length;i++){
    var d=dots[i];
    d.x+=d.dx;d.y+=d.dy;d.a+=d.da;
    if(d.a>=1){d.da=-0.004;}
    if(d.a<=0){d.da=0.0015;d.a=0;}
    if(d.x<0||d.x>c.width)d.dx*=-1;
    if(d.y<0||d.y>c.height)d.dy*=-1;
   var glow=ctx.createRadialGradient(d.x,d.y,0,d.x,d.y,d.r*12);
   glow.addColorStop(0,'rgba('+ar+','+ag+','+ab+','+d.a+')');
   glow.addColorStop(.15,'rgba('+ar+','+ag+','+ab+','+d.a*.7+')');
   glow.addColorStop(.4,'rgba('+ar+','+ag+','+ab+','+d.a*.25+')');
   glow.addColorStop(1,'rgba('+ar+','+ag+','+ab+',0)');
   ctx.beginPath();ctx.arc(d.x,d.y,d.r,0,Math.PI*2);
   ctx.fillStyle='rgba('+ar+','+ag+','+ab+','+d.a+')';ctx.fill();
   ctx.beginPath();ctx.arc(d.x,d.y,d.r*12,0,Math.PI*2);
   ctx.fillStyle=glow;ctx.fill();
  }
  requestAnimationFrame(draw);
 }
 draw();
 window.addEventListener('resize',function(){c.width=window.innerWidth;c.height=window.innerHeight;});
})();
</script>
<script>
(function(){
 var greetings=['Welcome','Добро пожаловать','Willkommen','Bienvenue','欢迎'];
 var el=document.getElementById('greeting');
 var i=0;
 function show(){el.style.opacity='0';setTimeout(function(){el.textContent=greetings[i];el.style.opacity='1';i=(i+1)%greetings.length;},500);}
 show();setInterval(show,3000);
})();
</script>
<script>
var params=new URLSearchParams(window.location.search);
if(params.get('error')){
 document.getElementById('errorBox').textContent='%LOGIN_ERR%';
 document.getElementById('errorBox').classList.add('show');
}
if(params.get('locked')){
 document.getElementById('errorBox').textContent='%LOGIN_LOCK%';
 document.getElementById('errorBox').classList.add('show');
 document.getElementById('token-input').disabled=true;
 document.getElementById('login-btn').disabled=true;
}
</script>
</body>
</html>"""


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
        return RedirectResponse("/login?locked=1")
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
    return RedirectResponse("/login?error=1")

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

TRANSLATIONS = {
    "en": {
        "dashboard": "Dashboard", "setup": "Core Settings", "console": "Console",
        "players": "Players", "files": "Files", "plugins": "Plugins & Mods",
        "settings": "Settings", "properties": "Properties",
        "status": "Status", "online": "Online", "offline": "Offline",
        "memory_used": "Memory Used", "memory_total": "Memory Total",
        "disk_usage": "Disk",         "cpu_load": "CPU load(server.jar)", "click_to_detail": "Click to details",
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
        "drop_jar_here": "Drop server.jar here", "supported": "Spigot, Paper, Purpur, Purpur, etc.",
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
        "confirm": "Confirm", "edit_file": "Edit File", "warning_title": "Warning!",
        "confirm_replace_core": "Replace server core",
        "backup_panel": "Backup Panel", "backup_panel_desc": "Download a zip archive of all panel files", "download_backup": "backup", "check_updates": "Check for updates", "check_update": "Check Update", "update_available": "Update available", "install_update": "Install Update", "up_to_date": "Up to date", "update_error": "Error checking updates",
        "backup_server": "server backup",
        "login_password": "Password", "login_btn": "Login", "login_error": "Invalid password", "login_locked": "Too many attempts. Try again later.",
        "auth_settings": "Authorization", "auth_settings_desc": "Set or change access password", "auth_token": "Password",
        "auth_set": "Set Token", "auth_remove": "Remove", "auth_status_set": "Token is set", "auth_status_unset": "No token set",
        "enter_name": "Enter a name", "saved": "Saved", "removed": "Removed",
        "java_old": "Java 17+ required", "edit_env_hint": "Set JAVA_PATH in .env file",
        "accent_color": "Customization", "delete_all": "Delete All", "fireflies": "Fireflies",
        "panel_opacity": "Panel Opacity",
        "confirm_delete_all": "Delete all", "confirm_delete": "Delete",
        "server_crashed": "Server crashed!",
        "sort_by_name": "Name", "sort_by_size": "Size",
        "search_files": "Search files...", "search_recursive": "Include subfolders",
        "upload_file": "Upload", "create_folder": "New Folder",
        "download_core": "Download a ready server core:",
        "logout": "Logout",
        "users": "Users", "add_user": "Add user", "user_list": "Users", "username": "Username", "password": "Password", "user_role": "Role", "user": "User", "administrator": "Administrator", "create_user": "Create user", "save_permissions": "Save permissions", "delete_user": "Delete user", "account": "Account",
        "perm_console": "Console and commands", "perm_mods": "Add/remove mods and plugins", "perm_properties": "Edit server properties", "perm_files": "Server file browser", "perm_server_control": "Start, stop and restart server", "perm_players": "OP, ban and whitelist", "no_users": "No users yet", "confirm_delete_user": "Delete user", "user_created": "User created", "user_updated": "Permissions saved", "user_deleted": "User deleted",
        "install_core": "Install",
    },
    "ru": {
        "dashboard": "Главная", "setup": "Настройки ядра", "console": "Консоль",
        "players": "Игроки", "files": "Файлы", "plugins": "Плагины и Моды",
        "settings": "Настройки панели", "properties": "Свойства",
        "status": "Статус", "online": "Онлайн", "offline": "Оффлайн",
        "memory_used": " Использование памяти", "memory_total": "Память всего",
        "disk_usage": "Диск",         "cpu_load": "Нагрузка на ЦП(server.jar)", "click_to_detail": "Нажмите для подробностей",
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
        "drop_jar_here": "Перетащите server.jar сюда", "supported": "Spigot, Paper, Purpur, Purpur и др.",
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
        "confirm": "Подтвердить", "edit_file": "Редактировать файл", "warning_title": "Внимание!",
        "confirm_replace_core": "Заменить ядро сервера",
        "backup_panel": "Резервная копия", "backup_panel_desc": "Скачать архив со всеми файлами панели", "download_backup": "backup", "check_updates": "Проверить обновления", "check_update": "Проверить", "update_available": "Доступно обновление", "install_update": "Установить", "up_to_date": "Обновлена", "update_error": "Ошибка проверки обновлений",
        "backup_server": "бэкап сервера",
        "login_password": "Пароль", "login_btn": "Войти", "login_error": "Неверный пароль", "login_locked": "Слишком много попыток. Попробуйте позже.",
        "auth_settings": "Авторизация", "auth_settings_desc": "Установить или сменить пароль доступа", "auth_token": "Пароль",
        "auth_set": "Установить", "auth_remove": "Удалить", "auth_status_set": "Пароль установлен", "auth_status_unset": "Пароль не установлен",
        "enter_name": "Введите имя", "saved": "Сохранено", "removed": "Удалено",
        "java_old": "Нужна Java 17+", "edit_env_hint": "Укажите JAVA_PATH в .env",
        "accent_color": "Кастомизация", "delete_all": "Удалить всё", "fireflies": "Светлячки",
        "panel_opacity": "Прозрачность панелей",
        "confirm_delete_all": "Удалить все", "confirm_delete": "Удалить",
        "confirm_stop": "Остановить сервер?", "confirm_start": "Запустить сервер?", "confirm_restart": "Перезапустить сервер?",
        "server_crashed": "Сервер упал!",
        "sort_by_name": "Имя", "sort_by_size": "Размер",
        "search_files": "Поиск файлов...", "search_recursive": "Включая подпапки",
        "upload_file": "Загрузить", "create_folder": "Новая папка",
        "download_core": "Скачать готовое ядро сервера:",
        "logout": "Выйти",
        "users": "Пользователи", "add_user": "Добавить пользователя", "user_list": "Пользователи", "username": "Логин", "password": "Пароль", "user_role": "Роль", "user": "Пользователь", "administrator": "Администратор", "create_user": "Создать пользователя", "save_permissions": "Сохранить права", "delete_user": "Удалить пользователя", "account": "Аккаунт",
        "perm_console": "Просмотр консоли и команды", "perm_mods": "Добавление и удаление модов/плагинов", "perm_properties": "Редактирование свойств сервера", "perm_files": "Файловый браузер сервера", "perm_server_control": "Запуск, остановка и перезапуск сервера", "perm_players": "OP, бан и вайтлист", "no_users": "Пользователей пока нет", "confirm_delete_user": "Удалить пользователя", "user_created": "Пользователь создан", "user_updated": "Права сохранены", "user_deleted": "Пользователь удалён",
        "install_core": "Установить",
    },
    "de": {
        "dashboard": "Dashboard", "setup": "Kerneinstellungen", "console": "Konsole",
        "players": "Spieler", "files": "Dateien", "plugins": "Plugins & Mods",
        "settings": "Einstellungen", "properties": "Eigenschaften",
        "status": "Status", "online": "Online", "offline": "Offline",
        "memory_used": "Speicher belegt", "memory_total": "Speicher gesamt",
        "disk_usage": "Festplatte",         "cpu_load": "CPU-Auslastung(server.jar)", "click_to_detail": "Klicken für Details",
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
        "drop_jar_here": "server.jar hier ablegen", "supported": "Spigot, Paper, Purpur, Purpur usw.",
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
        "confirm": "Bestätigen", "edit_file": "Datei bearbeiten", "warning_title": "Achtung!",
        "confirm_replace_core": "Server-Kern ersetzen",
        "backup_panel": "Backup", "backup_panel_desc": "Alle Panel-Dateien als ZIP herunterladen", "download_backup": "backup", "check_updates": "Nach Updates suchen", "check_update": "Prüfen", "update_available": "Update verfügbar", "install_update": "Installieren", "up_to_date": "Aktuell", "update_error": "Fehler beim Prüfen",
        "backup_server": "Server-Backup",
        "login_password": "Passwort", "login_btn": "Anmelden", "login_error": "Falsches Passwort", "login_locked": "Zu viele Versuche. Bitte später versuchen.",
        "auth_settings": "Autorisierung", "auth_settings_desc": "Zugangspasswort setzen oder ändern", "auth_token": "Zugangstoken",
        "auth_set": "Token setzen", "auth_remove": "Entfernen", "auth_status_set": "Token gesetzt", "auth_status_unset": "Kein Token gesetzt",
        "enter_name": "Name eingeben", "saved": "Gespeichert", "removed": "Entfernt",
        "java_old": "Java 17+ benötigt", "edit_env_hint": "JAVA_PATH in .env setzen",
        "accent_color": "Anpassung", "delete_all": "Alle löschen", "fireflies": "Glühwürmchen",
        "panel_opacity": "Panel-Transparenz",
        "confirm_delete_all": "Alle löschen", "confirm_delete": "Löschen",
        "server_crashed": "Server abgestürzt!",
        "sort_by_name": "Name", "sort_by_size": "Größe",
        "search_files": "Dateien suchen...", "search_recursive": "Unterverzeichnisse einbeziehen",
        "upload_file": "Hochladen", "create_folder": "Neuer Ordner",
        "download_core": "Fertigen Server-Kern herunterladen:",
        "logout": "Abmelden",
        "users": "Benutzer", "add_user": "Benutzer hinzufügen", "user_list": "Benutzer", "username": "Benutzername", "password": "Passwort", "user_role": "Rolle", "user": "Benutzer", "administrator": "Administrator", "create_user": "Benutzer erstellen", "save_permissions": "Berechtigungen speichern", "delete_user": "Benutzer löschen", "account": "Konto",
        "perm_console": "Konsole und Befehle anzeigen", "perm_mods": "Mods und Plugins hinzufügen/löschen", "perm_properties": "Servereigenschaften bearbeiten", "perm_files": "Server-Dateibrowser", "perm_server_control": "Server starten, stoppen und neu starten", "perm_players": "OP, Bann und Whitelist", "no_users": "Noch keine Benutzer", "confirm_delete_user": "Benutzer löschen", "user_created": "Benutzer erstellt", "user_updated": "Berechtigungen gespeichert", "user_deleted": "Benutzer gelöscht",
        "install_core": "Installieren",
    },
    "fr": {
        "dashboard": "Tableau de bord", "setup": "Paramètres du noyau", "console": "Console",
        "players": "Joueurs", "files": "Fichiers", "plugins": "Plugins & Mods",
        "settings": "Paramètres", "properties": "Propriétés",
        "status": "Statut", "online": "En ligne", "offline": "Hors ligne",
        "memory_used": "Mémoire utilisée", "memory_total": "Mémoire totale",
        "disk_usage": "Disque",         "cpu_load": "Utilisation du CPU(server.jar)", "click_to_detail": "Cliquez pour les détails",
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
        "drop_jar_here": "Déposez server.jar ici", "supported": "Spigot, Paper, Purpur, Purpur, etc.",
        "quick_actions": "Actions rapides", "start": "Démarrer", "restart": "Redémarrer", "stop": "Arrêter",
        "server_properties": "server.properties", "save_properties": "Enregistrer",
        "server_files": "Fichiers serveur", "plugins_tab": "Plugins", "mods_tab": "Mods",
        "drop_jar_files": "Déposez des fichiers .jar ici", "or_browse": "ou cliquez pour parcourir",
        "no_plugins": "Aucun plugin installé", "no_mods": "Aucun mod installé",
        "not_found": "(introuvable)", "settings_saved": "Paramètres enregistrés",
        "language": "Langue", "panel_port": "Port du panneau", "mc_directory": "Répertoire Minecraft",
        "screen_name": "Nom de la session screen", "apply_restart": "Redémarrez le panneau pour appliquer",
        "enter_name": "Entrez un nom", "confirm_delete": "Supprimer",
        "motd": "MOTD", "ops_count": "OPs", "whitelist_count": "Whitelist", "bans_count": "Bannis",
        "send": "Envoyer", "enter_command": "Entrez une commande...", "enter_username": "Entrez le nom",
        "add_player": "Ajouter un joueur", "player_name": "Nom du joueur", "cancel": "Annuler", "add": "Ajouter",
        "edit_env": "Modifiez le fichier .env pour changer ces valeurs", "no_files": "Aucun fichier",
        "only_jar": "Uniquement les fichiers .jar", "select_file": "Sélectionnez d'abord un fichier",
        "server_restarting": "Redémarrage du serveur...",
        "confirm_stop": "Arrêter le serveur ?", "confirm_start": "Démarrer le serveur ?", "confirm_restart": "Redémarrer le serveur ?",
        "confirm_delete_file": "Supprimer", "uploading": "Téléchargement...", "uploaded": "Téléchargé",
        "confirm": "Confirmer", "edit_file": "Modifier le fichier", "warning_title": "Attention !",
        "confirm_replace_core": "Remplacer le noyau du serveur",
        "backup_panel": "Sauvegarde", "backup_panel_desc": "Télécharger une archive ZIP de tous les fichiers du panneau", "download_backup": "backup", "check_updates": "Vérifier les mises à jour", "check_update": "Vérifier", "update_available": "Mise à jour disponible", "install_update": "Installer", "up_to_date": "À jour", "update_error": "Erreur de vérification",
        "backup_server": "sauvegarde serveur",
        "login_password": "Mot de passe", "login_btn": "Connexion", "login_error": "Mot de passe incorrect", "login_locked": "Trop de tentatives. Réessayez plus tard.",
        "auth_settings": "Autorisation", "auth_settings_desc": "Définir ou changer le mot de passe", "auth_token": "Jeton d'accès",
        "auth_set": "Définir", "auth_remove": "Supprimer", "auth_status_set": "Jeton défini", "auth_status_unset": "Aucun jeton",
        "saved": "Enregistré", "removed": "Supprimé",
        "java_old": "Java 17+ requis", "edit_env_hint": "Définissez JAVA_PATH dans le fichier .env",
        "accent_color": "Personnalisation", "delete_all": "Tout supprimer", "fireflies": "Lucioles",
        "panel_opacity": "Opacité des panneaux",
        "confirm_delete_all": "Tout supprimer", "confirm_delete": "Supprimer",
        "server_crashed": "Serveur en panne !",
        "sort_by_name": "Nom", "sort_by_size": "Taille",
        "search_files": "Rechercher des fichiers...", "search_recursive": "Inclure les sous-dossiers",
        "upload_file": "Téléverser", "create_folder": "Nouveau dossier",
        "download_core": "Télécharger un noyau de serveur prêt :",
        "logout": "Déconnexion",
        "users": "Utilisateurs", "add_user": "Ajouter un utilisateur", "user_list": "Utilisateurs", "username": "Nom d'utilisateur", "password": "Mot de passe", "user_role": "Rôle", "user": "Utilisateur", "administrator": "Administrateur", "create_user": "Créer l'utilisateur", "save_permissions": "Enregistrer les droits", "delete_user": "Supprimer l'utilisateur", "account": "Compte",
        "perm_console": "Voir la console et les commandes", "perm_mods": "Ajouter/supprimer mods et plugins", "perm_properties": "Modifier les propriétés du serveur", "perm_files": "Explorateur de fichiers serveur", "perm_server_control": "Démarrer, arrêter et redémarrer le serveur", "perm_players": "OP, bannissement et whitelist", "no_users": "Aucun utilisateur", "confirm_delete_user": "Supprimer l'utilisateur", "user_created": "Utilisateur créé", "user_updated": "Droits enregistrés", "user_deleted": "Utilisateur supprimé",
        "install_core": "Installer",
    },
    "zh": {
        "dashboard": "仪表盘", "setup": "核心设置", "console": "控制台",
        "players": "玩家", "files": "文件", "plugins": "插件和Mod",
        "settings": "面板设置", "properties": "属性",
        "status": "状态", "online": "在线", "offline": "离线",
        "memory_used": "已用内存", "memory_total": "总内存",
        "disk_usage": "磁盘",         "cpu_load": "CPU 使用率(server.jar)", "click_to_detail": "点击查看详情",
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
        "drop_jar_here": "拖放server.jar到此处", "supported": "Spigot, Paper, Purpur, Purpur等",
        "quick_actions": "快捷操作", "start": "启动", "restart": "重启", "stop": "停止",
        "server_properties": "server.properties", "save_properties": "保存",
        "server_files": "服务器文件", "plugins_tab": "插件", "mods_tab": "Mod",
        "drop_jar_files": "拖放.jar文件到此处", "or_browse": "或点击浏览",
        "no_plugins": "没有安装插件", "no_mods": "没有安装Mod",
        "not_found": "(未找到)", "settings_saved": "设置已保存",
        "language": "语言", "panel_port": "面板端口", "mc_directory": "Minecraft目录",
        "screen_name": "Screen会话名称", "apply_restart": "重启面板以生效",
        "enter_name": "输入名称", "confirm_delete": "删除",
        "motd": "MOTD", "ops_count": "管理员", "whitelist_count": "白名单", "bans_count": "封禁",
        "send": "发送", "enter_command": "输入命令...", "enter_username": "输入用户名",
        "add_player": "添加玩家", "player_name": "玩家名", "cancel": "取消", "add": "添加",
        "edit_env": "编辑.env文件以更改这些值", "no_files": "没有文件",
        "only_jar": "仅限.jar文件", "select_file": "请先选择文件",
        "server_restarting": "服务器重启中...",
        "confirm_stop": "停止服务器？", "confirm_start": "启动服务器？", "confirm_restart": "重启服务器？",
        "confirm_delete_file": "删除", "uploading": "上传中...", "uploaded": "已上传",
        "confirm": "确认", "edit_file": "编辑文件", "warning_title": "注意！",
        "confirm_replace_core": "替换服务器核心",
        "backup_panel": "备份面板", "backup_panel_desc": "下载包含所有面板文件的ZIP压缩包", "download_backup": "backup", "check_updates": "检查更新", "check_update": "检查", "update_available": "有可用更新", "install_update": "安装更新", "up_to_date": "已是最新", "update_error": "检查更新出错",
        "backup_server": "服务器备份",
        "login_password": "密码", "login_btn": "登录", "login_error": "密码错误", "login_locked": "尝试次数过多，请稍后再试。",
        "auth_settings": "授权", "auth_settings_desc": "设置或修改访问密码", "auth_token": "密碼",
        "auth_set": "设置密碼", "auth_remove": "删除", "auth_status_set": "密碼已設定", "auth_status_unset": "未设置密碼",
        "saved": "已保存", "removed": "已移除",
        "java_old": "需要Java 17+", "edit_env_hint": "在.env文件中设置JAVA_PATH",
        "accent_color": "自定义", "delete_all": "删除全部", "fireflies": "萤火虫",
        "panel_opacity": "面板透明度",
        "confirm_delete_all": "删除全部", "confirm_delete": "删除",
        "server_crashed": "服务器崩溃了！",
        "sort_by_name": "名称", "sort_by_size": "大小",
        "search_files": "搜索文件...", "search_recursive": "包含子目录",
        "upload_file": "上传", "create_folder": "新建文件夹",
        "download_core": "下载现成的服务器核心：",
        "logout": "退出登录",
        "users": "用户", "add_user": "添加用户", "user_list": "用户", "username": "用户名", "password": "密码", "user_role": "角色", "user": "用户", "administrator": "管理员", "create_user": "创建用户", "save_permissions": "保存权限", "delete_user": "删除用户", "account": "账户",
        "perm_console": "查看控制台和命令", "perm_mods": "添加/删除模组和插件", "perm_properties": "编辑服务器属性", "perm_files": "服务器文件浏览器", "perm_server_control": "启动、停止和重启服务器", "perm_players": "OP、封禁和白名单", "no_users": "暂无用户", "confirm_delete_user": "删除用户", "user_created": "用户已创建", "user_updated": "权限已保存", "user_deleted": "用户已删除",
        "install_core": "安装",
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
    existing = {}
    if SETTINGS_FILE.exists():
        try:
            existing = json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
    existing.update(data)
    SETTINGS_FILE.write_text(json.dumps(existing, indent=2))





HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" type="image/png" href="https://raw.githubusercontent.com/fizyCH/FizMine/main/assets/FizMinebig.png">
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
.status-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px 22px;flex:1;min-width:155px;position:relative;overflow:hidden;transition:border-color .35s,box-shadow .35s,transform .35s cubic-bezier(.16,1,.3,1)}
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

.panel{background:var(--panel-bg,#151922);border:1px solid var(--border);border-radius:14px;padding:22px;margin-bottom:20px;transition:border-color .35s,box-shadow .35s;position:relative;z-index:2}
.panel:hover{border-color:rgba(var(--accent-rgb),.15);box-shadow:0 4px 20px rgba(0,0,0,.15)}
.panel h3{font-size:15px;margin-bottom:14px;display:flex;align-items:center;gap:10px;font-weight:600}
.panel h3 .dot{width:8px;height:8px;border-radius:50%;display:inline-block;transition:background .3s}
.ico{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0;vertical-align:middle}
.ico-sm{width:14px;height:14px}

.console-box{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px;font-family:'JetBrains Mono','Cascadia Code','Fira Code',monospace;font-size:12px;height:420px;overflow-y:auto;line-height:1.7;word-wrap:break-word}
.console-box .line{white-space:pre-wrap}
.console-box .line.info{color:#94a3b8}
.console-box .line.warn{color:var(--yellow)}
.console-box .line.error{color:var(--red)}
.console-box .line.chat{color:var(--green)}

.cmd-input{display:flex;gap:10px;margin-top:10px}
.cmd-input input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:11px 16px;color:var(--text);font-family:monospace;font-size:13px;outline:none;transition:all .25s}
.cmd-input input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(var(--accent-rgb),.15)}
#file-search:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(var(--accent-rgb),.15)}

table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:11px 14px;border-bottom:1px solid var(--border);font-size:13px}
th{color:var(--text2);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.8px}
tr{transition:background .2s}
tr:hover{background:rgba(var(--accent-rgb),.04)}

.form-group{margin-bottom:16px;position:relative}
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
.ring-chart{position:relative;display:inline-flex;align-items:center;justify-content:center}
.ring-chart .ring-value{position:absolute;font-size:18px;font-weight:800;letter-spacing:-.5px}
.ring-chart svg{transform:rotate(-90deg)}
.status-card.clickable{cursor:pointer}
.status-card.clickable::after{content:'';position:absolute;top:8px;right:8px;width:0;height:0;border-left:4px solid transparent;border-right:4px solid transparent;border-top:4px solid var(--text2);opacity:.4}
.status-card.clickable:hover::after{opacity:.8}
.chart-modal-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;align-items:center}
.chart-modal-item{text-align:center}
.chart-modal-label{font-size:13px;color:var(--text2);margin-top:10px}
.chart-modal-detail{font-size:12px;color:var(--text2);margin-top:4px}

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

.setup-card{background:var(--panel-bg,#151922);border:2px dashed var(--border);border-radius:16px;padding:40px;text-align:center;margin-bottom:20px}
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

#fireflies-canvas{position:fixed;inset:0;z-index:0;pointer-events:none;opacity:0;transition:opacity 1.5s}
#fireflies-canvas.active{opacity:1}
#core-versions-panel{display:none;position:absolute;top:100%;left:0;right:0;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:0 10px 40px rgba(0,0,0,.5);z-index:50;margin-top:8px;overflow:hidden}
#core-versions-panel h4{margin:0;font-size:14px;color:var(--text)}
#core-versions-panel .cv-item{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;border-bottom:1px solid var(--border);transition:background .15s}
#core-versions-panel .cv-item:last-child{border-bottom:none}
#core-versions-panel .cv-item:hover{background:rgba(var(--accent-rgb),.08)}
#core-versions-panel .cv-item.disabled{opacity:.4;cursor:default}
#core-versions-panel .cv-item.disabled:hover{background:none}
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
@keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
.spin{animation:spin 1s linear infinite}
</style>
</head>
<body>
<canvas id="fireflies-canvas"></canvas>
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
    <div id="account-badge" style="margin-top:auto;padding:12px 16px 4px;color:var(--text2);font-size:12px"></div>
    <a href="/logout" style="display:flex;align-items:center;gap:12px;padding:11px 16px;color:var(--red);border-radius:10px;font-size:13.5px;font-weight:500;transition:all .25s;text-decoration:none" onmouseover="this.style.background='rgba(239,68,68,.1)'" onmouseout="this.style.background='transparent'"><span class="icon"><svg viewBox="0 0 24 24" style="width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 12"/><line x1="21" y1="12" x2="9" y2="12"/></svg></span><span data-i18n="logout">Logout</span></a>
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
     <h3><svg class="ico" viewBox="0 0 24 24"><rect x="3" y="12" width="4" height="9" rx="1"/><rect x="10" y="7" width="4" height="14" rx="1"/><rect x="17" y="3" width="4" height="18" rx="1"/></svg> <span data-i18n="server_info">Server Info</span></h3>
    <div id="quick-info"></div>
   </div>
  </div>
  <div class="panel">
     <h3><svg class="ico" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14,2 14,8 20,8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="12" y2="17"/></svg> <span data-i18n="recent_console">Recent Console</span></h3>
   <div class="console-box" id="dash-console"></div>
  </div>
 </div>

 <div id="tab-setup" style="display:none">
  <div id="setup-view"></div>
 </div>

 <div id="tab-console" style="display:none">
  <div class="panel">
     <h3><svg class="ico" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14,2 14,8 20,8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="12" y2="17"/></svg> <span data-i18n="live_console">Live Console</span></h3>
   <div class="console-box" id="live-console"></div>
   <div class="cmd-input">
    <input type="text" id="cmd-input" data-i18n-ph="enter_command" placeholder="Enter command..." onkeydown="if(event.key==='Enter')sendCmd()">
     <button class="btn btn-accent" onclick="sendCmd()"><svg class="ico" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="6,3 20,12 6,21"/></svg> <span data-i18n="send">Send</span></button>
   </div>
  </div>
 </div>

 <div id="tab-players" style="display:none">
  <div class="online-section">
   <div class="online-header">
     <h3><svg class="ico" viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/></svg> <span data-i18n="online_players">Online Players</span></h3>
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
    <h3><svg class="ico" viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/></svg> <span data-i18n="player_management">Player Management</span></h3>
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
    <h3><svg class="ico" viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> server.properties</h3>
   <div id="props-form"></div>
   <button class="btn btn-accent" onclick="saveProperties()" style="margin-top:14px"><span data-i18n="save_properties">Save Properties</span></button>
  </div>
 </div>

 <div id="tab-filebrowser" style="display:none">
  <div class="panel">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
     <h3 style="margin:0;flex:1"><svg class="ico" viewBox="0 0 24 24"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg> <span data-i18n="files">Files</span></h3>
      <button class="btn btn-accent btn-sm" onclick="document.getElementById('file-upload-input').click()"><svg class="ico ico-sm" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17,8 12,3 7,8"/><line x1="12" y1="3" x2="12" y2="15"/></svg> <span data-i18n="upload_file">Upload</span></button>
      <button class="btn btn-green btn-sm" onclick="createFolder()"><svg class="ico ico-sm" viewBox="0 0 24 24"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg> <span data-i18n="create_folder">New Folder</span></button>
      <button class="btn btn-accent btn-sm" id="create-file-btn" onclick="createFile()">Create File</button>
      <input type="file" id="file-upload-input" style="display:none" onchange="uploadFileToDir(this.files[0]);this.value=''">
    </div>
   <div id="file-breadcrumb" style="margin-bottom:12px;font-size:13px;color:var(--text2)"></div>
   <div style="position:relative;margin-bottom:12px">
    <svg style="position:absolute;left:12px;top:50%;transform:translateY(-50%);width:16px;height:16px;stroke:var(--text2);fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;pointer-events:none" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    <input type="text" id="file-search" data-i18n-ph="search_files" oninput="filterFiles()" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px 14px 10px 38px;color:var(--text);font-size:13px;outline:none;transition:border-color .25s,box-shadow .25s">
   </div>
   <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px">
    <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:12px;color:var(--text2)">
     <input type="checkbox" id="file-search-recursive" onchange="filterFiles()" style="accent-color:var(--accent)">
     <span data-i18n="search_recursive">Include subfolders</span>
    </label>
   </div>
   <div id="file-list"></div>
  </div>
 </div>

 <div class="modal-overlay" id="file-editor-overlay" onclick="if(event.target===this)closeFileEditor()">
  <div class="modal" style="width:700px;max-width:90vw;max-height:80vh;display:flex;flex-direction:column">
    <h3 id="file-editor-title" style="margin-bottom:12px" data-i18n="edit_file">Edit File</h3>
   <textarea id="file-editor-content" style="flex:1;min-height:400px;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px;color:var(--text);font-family:'JetBrains Mono','Cascadia Code',monospace;font-size:13px;resize:none;outline:none;tab-size:2"></textarea>
   <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
    <button class="btn btn-outline" onclick="closeFileEditor()"><span data-i18n="cancel">Cancel</span></button>
    <button class="btn btn-accent" onclick="saveFileEdit()"><span data-i18n="save_properties">Save</span></button>
   </div>
  </div>
 </div>

 <div class="modal-overlay" id="confirm-overlay" onclick="if(event.target===this)confirmCancel()">
  <div class="modal" style="width:380px">
    <h3 id="confirm-title" style="margin-bottom:16px;color:var(--yellow)" data-i18n="warning_title">Warning!</h3>
   <p id="confirm-message" style="color:var(--text2);font-size:14px;margin-bottom:24px;line-height:1.5"></p>
   <div style="display:flex;gap:10px;justify-content:flex-end">
    <button class="btn btn-outline" onclick="confirmCancel()"><span data-i18n="cancel">Cancel</span></button>
    <button class="btn btn-red" id="confirm-ok-btn" onclick="confirmOk()" data-i18n="confirm">Confirm</button>
   </div>
  </div>
  </div>

   <div class="modal-overlay" id="chart-overlay" onclick="if(event.target===this)closeChartModal()">
    <div class="modal" style="width:480px">
     <h3 id="chart-title" style="margin-bottom:20px"></h3>
     <div class="chart-modal-grid" id="chart-content"></div>
     <div style="text-align:right;margin-top:20px">
      <button class="btn btn-outline" onclick="closeChartModal()"><span data-i18n="cancel">Cancel</span></button>
     </div>
    </div>
   </div>

  <div id="tab-plugins" style="display:none">
  <div class="grid-2">
   <div class="panel">
     <h3><svg class="ico" viewBox="0 0 24 24"><path d="M20.5 11H19V7a2 2 0 00-2-2h-4V3.5a2.5 2.5 0 00-5 0V5H4a2 2 0 00-2 2v3.8h1.5a2.5 2.5 0 010 5H2V20a2 2 0 002 2h3.8v-1.5a2.5 2.5 0 015 0V22H17a2 2 0 002-2v-4h1.5a2.5 2.5 0 100-5z"/></svg> <span data-i18n="plugins_tab">Plugins</span></h3>
     <div class="drop-zone" id="drop-plugins" ondragover="handleDrag(event,this)" ondragleave="handleDragLeave(this)" ondrop="handleDrop(event,'plugins',this)" onclick="this.querySelector('input').click()">
      <input type="file" accept=".jar" onchange="uploadFile('plugins',this.files[0]);this.value=''">
       <div class="drop-icon"><svg class="ico" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17,8 12,3 7,8"/><line x1="12" y1="3" x2="12" y2="15"/></svg></div>
       <div class="drop-text" data-i18n="drop_jar_files">Drop .jar files here</div>
       <div class="drop-hint" data-i18n="or_browse">or click to browse</div>
      </div>
     <div id="plugins-list"></div>
    </div>
    <div class="panel">
      <h3><svg class="ico" viewBox="0 0 24 24"><path d="M20.5 11H19V7a2 2 0 00-2-2h-4V3.5a2.5 2.5 0 00-5 0V5H4a2 2 0 00-2 2v3.8h1.5a2.5 2.5 0 010 5H2V20a2 2 0 002 2h3.8v-1.5a2.5 2.5 0 015 0V22H17a2 2 0 002-2v-4h1.5a2.5 2.5 0 100-5z"/></svg> <span data-i18n="mods_tab">Mods</span></h3>
      <div class="drop-zone" id="drop-mods" ondragover="handleDrag(event,this)" ondragleave="handleDragLeave(this)" ondrop="handleDrop(event,'mods',this)" onclick="this.querySelector('input').click()">
       <input type="file" accept=".jar" onchange="uploadFile('mods',this.files[0]);this.value=''">
      <div class="drop-icon"><svg class="ico" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17,8 12,3 7,8"/><line x1="12" y1="3" x2="12" y2="15"/></svg></div>
      <div class="drop-text" data-i18n="drop_jar_files">Drop .jar files here</div>
      <div class="drop-hint" data-i18n="or_browse">or click to browse</div>
     </div>
     <div id="mods-list"></div>
    </div>
  </div>
 </div>

 <div id="tab-users" style="display:none">
  <div class="grid-2">
   <div class="panel">
    <h3 data-i18n="add_user">Добавить пользователя</h3>
    <div class="form-group"><label data-i18n="username">Логин</label><input id="new-user-name" maxlength="32" placeholder="nickname"></div>
    <div class="form-group"><label data-i18n="password">Пароль</label><input id="new-user-password" type="password" data-i18n-ph="login_password" placeholder="Пароль"><small data-i18n="password_hint" style="display:block;color:var(--text2);font-size:11px;margin-top:5px">Минимум 5 символов</small></div>
    <div class="form-group"><label data-i18n="user_role">Роль</label><div class="custom-select" id="role-select" onclick="toggleCustomSelect(this)"><div class="custom-select-selected" id="role-selected" data-value="user">Пользователь</div><div class="custom-select-options"><div class="custom-select-option selected" data-value="user" onclick="event.stopPropagation();selectUserRole('user')" data-i18n="user">Пользователь</div><div class="custom-select-option" data-value="admin" onclick="event.stopPropagation();selectUserRole('admin')" data-i18n="administrator">Администратор</div></div></div><select id="new-user-role" aria-hidden="true" tabindex="-1" style="display:none"><option value="user">Пользователь</option><option value="admin">Администратор</option></select></div>
    <div id="new-user-perms" style="display:grid;gap:10px;margin:14px 0;color:var(--text2);font-size:13px">
     <label><input type="checkbox" value="console"><span data-i18n="perm_console">Просмотр консоли и команды</span></label>
     <label><input type="checkbox" value="mods"><span data-i18n="perm_mods">Добавление и удаление модов/плагинов</span></label>
     <label><input type="checkbox" value="properties"><span data-i18n="perm_properties">Редактирование свойств сервера</span></label>
     <label><input type="checkbox" value="files"><span data-i18n="perm_files">Файловый браузер и редактирование файлов</span></label>
     <label><input type="checkbox" value="server_control"><span data-i18n="perm_server_control">Запуск, остановка и перезапуск сервера</span></label>
     <label><input type="checkbox" value="players"><span data-i18n="perm_players">OP, бан и вайтлист игроков</span></label>
    </div>
    <button class="btn btn-accent" onclick="createUser()" data-i18n="create_user">Создать пользователя</button>
   </div>
   <div class="panel"><h3 data-i18n="user_list">Пользователи</h3><div id="users-list"></div></div>
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
      <div style="margin-top:14px">
       <div style="display:flex;justify-content:space-between;margin-bottom:6px">
        <span style="font-size:12px;color:var(--text2)" data-i18n="panel_opacity">Panel Opacity</span>
        <span style="font-size:12px;color:var(--text)" id="opacity-val">100%</span>
       </div>
       <input type="range" min="0" max="100" value="100" id="opacity-slider" oninput="setPanelOpacity(this.value)" style="width:100%;accent-color:var(--accent)">
      </div>
    </div>
    <div class="panel">
     <h3>System</h3>
     <div class="form-group"><label>Platform</label><input type="text" id="set-screen" disabled style="opacity:.5"></div>
     <div class="form-group"><label>Java</label><input type="text" id="set-java" disabled style="opacity:.5"></div>
     <div class="form-group"><label>RCON</label><input type="text" id="set-rcon" disabled style="opacity:.5"></div>
     <div style="margin-top:14px;border-top:1px solid var(--border);padding-top:14px">
      <p style="color:var(--text2);font-size:12px;margin-bottom:8px" data-i18n="check_updates">Check for updates</p>
      <button class="btn btn-accent btn-sm" onclick="checkForUpdates()" id="btn-check-update">
       <svg viewBox="0 0 24 24" style="width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
       <span data-i18n="check_update">Check Update</span>
      </button>
      <div id="update-result" style="margin-top:10px;display:none"></div>
     </div>
    </div>
    <div class="panel">
     <h3><svg class="ico" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14,2 14,8 20,8"/></svg> <span data-i18n="backup_panel">Backup Panel</span></h3>
     <p style="color:var(--text2);font-size:12px;margin-bottom:12px" data-i18n="backup_panel_desc">Download a zip archive of all panel files</p>
     <a href="/api/backup-panel" class="btn btn-accent btn-sm" download><svg class="ico ico-sm" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14,2 14,8 20,8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="12" y2="17"/></svg> <span data-i18n="download_backup">backup</span></a>
     <a href="/api/backup-server" class="btn btn-green btn-sm" download><svg class="ico ico-sm" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7,10 12,15 17,10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> <span data-i18n="backup_server">server backup</span></a>
    </div>
    <div class="panel" id="auth-panel" style="display:none">
     <div style="display:flex;align-items:center;justify-content:space-between">
      <h3 style="margin:0"><svg class="ico" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg> <span data-i18n="auth_settings">Authorization</span></h3>
      <label class="toggle"><input type="checkbox" id="auth-toggle" onchange="toggleAuth(this.checked)"><span class="slider"></span></label>
     </div>
     <div id="auth-body" style="max-height:0;overflow:hidden;transition:max-height .4s cubic-bezier(.4,0,.2,1),opacity .3s;opacity:0">
      <p style="color:var(--text2);font-size:12px;margin:12px 0" data-i18n="auth_settings_desc">Set or change access password</p>
      <div class="form-group">
       <label data-i18n="auth_token">Password</label>
       <input type="password" id="set-token" placeholder="">
      </div>
      <div style="display:flex;gap:8px">
       <button class="btn btn-accent btn-sm" onclick="saveToken()"><span data-i18n="auth_set">Set Token</span></button>
       <button class="btn btn-red btn-sm" onclick="removeToken()"><span data-i18n="auth_remove">Remove</span></button>
      </div>
      <p id="token-status" style="color:var(--text2);font-size:12px;margin-top:8px"></p>
     </div>
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

<div class="modal-overlay" id="input-modal-overlay" onclick="if(event.target===this)closeInputModal()">
 <div class="modal" style="width:420px">
  <h3 id="input-modal-title"></h3>
  <div class="form-group"><label id="input-modal-label"></label><input type="text" id="input-modal-value" autocomplete="off" onkeydown="if(event.key==='Enter')inputModalConfirm()"></div>
  <div style="display:flex;gap:10px;justify-content:flex-end">
   <button class="btn btn-outline" onclick="closeInputModal()"><span data-i18n="cancel">Cancel</span></button>
   <button class="btn btn-accent" onclick="inputModalConfirm()"><span data-i18n="confirm">Confirm</span></button>
  </div>
 </div>
</div>

<script>
const CAN=%PERMISSIONS%;
const IS_ADMIN=%IS_ADMIN%;
const PERMISSION_LABELS={console:'perm_console',mods:'perm_mods',properties:'perm_properties',files:'perm_files',server_control:'perm_server_control',players:'perm_players'};
const EXTRA_TRANSLATIONS={en:{password_hint:'Minimum 5 characters'},ru:{password_hint:'Минимум 5 символов'},de:{password_hint:'Mindestens 5 Zeichen'},fr:{password_hint:'5 caractères minimum'},zh:{password_hint:'至少5个字符'}};
function applyAccess(){
 const tabs={setup:'server_control',console:'console',players:'players',files:'properties',filebrowser:'files',plugins:'mods'};
 Object.entries(tabs).forEach(([tab,permission])=>{
  if(CAN.includes(permission))return;
  const nav=document.getElementById('nav-'+tab),panel=document.getElementById('tab-'+tab);
 if(nav)nav.style.display='none'; if(panel)panel.remove();
 });
 if(!CAN.includes('server_control')){const controls=document.getElementById('control-btns');if(controls&&controls.closest('.panel'))controls.closest('.panel').style.display='none';}
 if(!CAN.includes('console')){const dash=document.getElementById('dash-console');if(dash&&dash.closest('.panel'))dash.closest('.panel').style.display='none';}
 if(!IS_ADMIN){const nav=document.getElementById('nav-settings'),panel=document.getElementById('tab-settings'),users=document.getElementById('tab-users');if(nav)nav.style.display='none';if(panel)panel.remove();if(users)users.remove();return;}
 const settingsNav=document.getElementById('nav-settings');
 if(settingsNav&&!document.getElementById('nav-users')){
  const nav=document.createElement('a');nav.id='nav-users';nav.href='#';nav.onclick=()=>showTab('users');nav.innerHTML='<span class="icon"><svg viewBox="0 0 24 24"><path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6M16 11h6"/></svg></span><span data-i18n="users">Пользователи</span>';settingsNav.parentNode.insertBefore(nav,settingsNav);
 }
}
async function loadCurrentAccount(){
 try{const me=await api('me');const el=document.getElementById('account-badge');if(el&&me.username)el.textContent=t('account')+': '+me.username+(me.role==='admin'?' · '+t('administrator'):'');}catch(e){}
}
let currentTab='dashboard';
let currentList='ops';
let consoleLines=[];
let modalAction='';
let serverReady=false;
let lastWasRunning=null;
let lastDashInfo=null;
let inputModalCallback=null;

function showInputModal(title,label,callback){
 document.getElementById('input-modal-title').textContent=title;
 document.getElementById('input-modal-label').textContent=label;
 const input=document.getElementById('input-modal-value');
 input.value='';
 inputModalCallback=callback;
 document.getElementById('input-modal-overlay').classList.add('active');
 setTimeout(()=>input.focus(),50);
}
function closeInputModal(){
 document.getElementById('input-modal-overlay').classList.remove('active');
 inputModalCallback=null;
}
async function inputModalConfirm(){
 const input=document.getElementById('input-modal-value');
 const value=input.value.trim();
 if(!value||!inputModalCallback)return;
 const callback=inputModalCallback;
 closeInputModal();
 await callback(value);
}

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
 applyAccess();
 loadCurrentAccount();
 try{
  const r=await fetch('/api/lang',{credentials:'same-origin'});
  T=await r.json();
  const lang=(await fetch('/api/settings',{credentials:'same-origin'}).then(x=>x.json()).catch(()=>({lang:'en'}))).lang||'en';
  if(!T.password_hint)T.password_hint=(EXTRA_TRANSLATIONS[lang]||EXTRA_TRANSLATIONS.en).password_hint;
 }catch(e){T={};}
 try{
  const sr=await fetch('/api/settings',{credentials:'same-origin'});
  const sd=await sr.json();
   if(sd.accent)applyAccent(sd.accent);
   if(sd.fireflies&&!firefliesOn){firefliesOn=true;setTimeout(()=>{initFireflies();document.getElementById('fireflies-canvas').classList.add('active');animateFireflies();},500);}
 }catch(e){}
 applyTranslations();
 loadCurrentAccount();
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
 const roleSelect=document.getElementById('new-user-role');
 if(roleSelect)selectUserRole(roleSelect.value||'user');
 document.getElementById('page-title').textContent=t(currentTab);
 const ml=document.getElementById('chart-mem-label');if(ml)ml.textContent=t('memory_used');
 const dl=document.getElementById('chart-disk-label');if(dl)dl.textContent=t('disk_usage');
 const cl=document.getElementById('chart-cpu-label');if(cl)cl.textContent=t('cpu_load');
 const ct=document.getElementById('chart-title');if(ct)ct.textContent=t('status');
 if(lastUpdateResult)renderUpdateResult(lastUpdateResult);
 if(currentTab==='dashboard')refreshDashboard();
 else if(currentTab==='players')loadPlayers();
 else if(currentTab==='plugins')loadPlugins();
 else if(currentTab==='setup')loadSetup();
 else if(currentTab==='settings')loadSettingsPage();
 else if(currentTab==='users')loadUsers();
}

let lastUpdateResult=null;
function renderUpdateResult(data){
 const result=document.getElementById('update-result');
 if(!result||!data)return;
 result.style.display='block';
 if(data.update){
  result.innerHTML='<div style="background:var(--surface2);border:1px solid var(--accent);border-radius:8px;padding:12px"><p style="margin:0 0 8px;font-size:13px">'+t('update_available')+': <b>v'+data.local+'</b> -> <b>v'+data.remote+'</b></p><button class="btn btn-accent btn-sm" onclick="doUpdate()">'+t('install_update')+'</button></div>';
 }else{
  result.innerHTML='<div style="background:var(--surface2);border:1px solid var(--green);border-radius:8px;padding:12px"><p style="margin:0;font-size:13px;color:var(--green)">'+t('up_to_date')+' (v'+data.local+')</p></div>';
 }
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
  if(tab==='users')loadUsers();
}

function selectedPermissions(){return [...document.querySelectorAll('#new-user-perms input:checked')].map(el=>el.value);}
async function createUser(){
 const username=document.getElementById('new-user-name').value.trim();
 const password=document.getElementById('new-user-password').value;
 const role=document.getElementById('new-user-role').value;
 const r=await api('users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password,role,permissions:selectedPermissions()})});
 if(r.error){toast(r.error);return;} document.getElementById('new-user-name').value='';document.getElementById('new-user-password').value='';toast(t('user_created'));loadUsers();
}
async function loadUsers(){
 const data=await api('users'); const out=document.getElementById('users-list');if(!out)return;
 if(data.error){out.innerHTML='<div class="empty">'+esc(data.error)+'</div>';return;}
 out.innerHTML=data.users.map(u=>{const perms=Object.entries(PERMISSION_LABELS).map(([key,labelKey])=>'<label style="display:block;margin:6px 0"><input type="checkbox" data-perm="'+key+'" '+(u.permissions.includes(key)?'checked':'')+' '+(u.role==='admin'?'disabled':'')+'> '+t(labelKey)+'</label>').join('');return '<div class="panel" style="padding:14px;margin-bottom:10px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><b>'+esc(u.username)+'</b><span style="color:var(--text2);font-size:12px">'+(u.role==='admin'?t('administrator'):t('user'))+'</span></div><div class="user-perms" data-user="'+esc(u.username)+'" style="margin:10px 0;font-size:12px;color:var(--text2)">'+perms+'</div><div style="display:flex;gap:8px"><button class="btn btn-accent btn-sm" onclick="saveUser(\''+esc(u.username)+'\')">'+t('save_permissions')+'</button>'+(u.username==='admin'?'':'<button class="btn btn-red btn-sm" onclick="deleteUser(\''+esc(u.username)+'\')">'+t('delete_user')+'</button>')+'</div></div>';}).join('')||'<div class="empty">'+t('no_users')+'</div>';
}
async function saveUser(username){const row=document.querySelector('.user-perms[data-user="'+CSS.escape(username)+'"]');const permissions=[...row.querySelectorAll('input:checked')].map(x=>x.dataset.perm);const r=await api('users',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,permissions})});if(r.error){toast(r.error);return;}toast(t('user_updated'));loadUsers();}
async function deleteUser(username){if(!confirm(t('confirm_delete_user')+' '+username+'?'))return;const r=await api('users',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({username})});if(r.error){toast(r.error);return;}toast(t('user_deleted'));loadUsers();}

function toast(msg){
 let el=document.createElement('div');
 el.className='toast';el.textContent=msg;
 document.body.appendChild(el);
 setTimeout(()=>{el.style.animation='slideOut .3s ease forwards';setTimeout(()=>el.remove(),300);},3000);
}

async function api(path,opts={}){
 try{
  opts.credentials='same-origin';
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
function playWarningSound(){
 try{
  const ctx=new(window.AudioContext||window.webkitAudioContext)();
  const now=ctx.currentTime;
  [440,520].forEach((freq,i)=>{
   const osc=ctx.createOscillator();
   const gain=ctx.createGain();
   osc.type='sine';
   osc.frequency.value=freq;
   gain.gain.setValueAtTime(0.3,now+i*0.12);
   gain.gain.exponentialRampToValueAtTime(0.001,now+i*0.12+0.15);
   osc.connect(gain);
   gain.connect(ctx.destination);
   osc.start(now+i*0.12);
   osc.stop(now+i*0.12+0.15);
  });
 }catch(e){}
}
function confirmAction(msg,action,type,name,sound){
 if(sound)playWarningSound();
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
     await api('server?action=stop');
     toast(t('server_restarting'));
     let waited=0;
     const poll=async()=>{
      try{
       const s=await api('status');
       if(!s.running||waited>=30000){
        await api('server?action=start');
        setTimeout(refreshDashboard,5000);
        return;
       }
      }catch(e){}
      waited+=1000;
      setTimeout(poll,1000);
     };
     setTimeout(poll,1000);
  }else if(action==='doUploadCore'){
   doUploadCore();
  }else if(action==='deleteFileItemConfirm'){
   deleteFileItemConfirm(type);
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
  lastDashInfo=info;
  serverReady=info.has_jar;

  if(lastWasRunning===true&&info.running===false){
   const cl=await api('console?lines=30');
   const crashed=cl.lines&&cl.lines.some(l=>/ERROR|Exception|crash|SIGTERM|OutOfMemory|killed/i.test(l));
   if(crashed){playCrashSound();toast(t('server_crashed'));}
  }
  lastWasRunning=info.running;

  if(!document.getElementById('card-status')){
   let statusHtml='';
   statusHtml+=`<div class="status-card" id="card-status"><div class="label">${t('status')}</div><div class="value" id="val-status"></div></div>`;
   statusHtml+=`<div class="status-card clickable" id="card-mem" onclick="showCharts()" title="${t('click_to_detail')}"><div class="label">${t('memory_used')}</div><div class="value" id="val-mem"></div></div>`;
   statusHtml+=`<div class="status-card clickable" id="card-disk" onclick="showCharts()" title="${t('click_to_detail')}"><div class="label">${t('disk_usage')}</div><div class="value" id="val-disk"></div></div>`;
   statusHtml+=`<div class="status-card clickable" id="card-cpu" onclick="showCharts()" title="${t('click_to_detail')}"><div class="label">${t('cpu_load')}</div><div class="value" id="val-cpu"></div></div>`;
   statusHtml+=`<div class="status-card" id="card-tps"><div class="label">${t('tps')}</div><div class="value" id="val-tps"></div></div>`;
   statusHtml+=`<div class="status-card" id="card-max"><div class="label">${t('max_players')}</div><div class="value" id="val-max"></div></div>`;
   document.getElementById('status-bar').innerHTML=statusHtml;
  }
  const vs=document.getElementById('val-status');vs.textContent=info.running?t('online'):t('offline');vs.className='value '+(info.running?'green':'red');
  const memPct=info.memory.total_mb?Math.round((info.memory.used_mb/info.memory.total_mb)*100):0;
  const vm=document.getElementById('val-mem');vm.textContent=memPct+'%';vm.className='value '+(memPct>80?'red':memPct>60?'yellow':'blue');
  const diskPct=info.disk?info.disk.percent:0;
  const vd=document.getElementById('val-disk');vd.textContent=diskPct+'%';vd.className='value '+(diskPct>80?'red':diskPct>60?'yellow':'cyan');
  const cpuPct=info.cpu_percent||0;
  const vc=document.getElementById('val-cpu');vc.textContent=cpuPct+'%';vc.className='value '+(cpuPct>80?'red':cpuPct>60?'yellow':'green');
  document.getElementById('val-tps').textContent=info.tps||'-';
  document.getElementById('val-max').textContent=info.max_players;
  document.querySelector('#card-status .label').textContent=t('status');
  document.querySelector('#card-mem .label').textContent=t('memory_used');
  document.querySelector('#card-disk .label').textContent=t('disk_usage');
  document.querySelector('#card-cpu .label').textContent=t('cpu_load');
  document.querySelector('#card-tps .label').textContent=t('tps');
  document.querySelector('#card-max .label').textContent=t('max_players');

 document.getElementById('status-dot').style.background=info.running?'var(--green)':'var(--red)';

 let btns='';
 if(!CAN.includes('server_control')){document.getElementById('control-btns').innerHTML='';return;}
 if(info.running){
   btns+=`<button class="btn btn-red" onclick="serverAction('stop')"><svg class="ico ico-sm" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="5" y="5" width="14" height="14" rx="2"/></svg> ${t('stop')}</button>`;
  btns+=`<button class="btn btn-yellow" onclick="serverAction('restart')">&#8635; ${t('restart')}</button>`;
 }else{
   btns+=`<button class="btn btn-green" onclick="serverAction('start')"><svg class="ico ico-sm" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="6,3 20,12 6,21"/></svg> ${t('start')}</button>`;
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
  if(chartInited&&document.getElementById('chart-overlay').classList.contains('active')){
   renderChartModal(lastDashInfo);
  }
}

function ringChart(id,pct,color,size){
 const r=size/2-8;
 const c=2*Math.PI*r;
 const offset=c-(pct/100)*c;
 return`<div class="ring-chart" style="width:${size}px;height:${size}px"><svg width="${size}" height="${size}"><circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="var(--border)" stroke-width="6"/><circle id="${id}-ring" cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="${color}" stroke-width="6" stroke-dasharray="${c}" stroke-dashoffset="${c}" stroke-linecap="round" style="transition:stroke-dashoffset .8s cubic-bezier(.4,0,.2,1)"/></svg><span class="ring-value" style="color:${color}" id="${id}-val">0%</span></div>`;
}

function updateRing(id,pct){
 const ring=document.getElementById(id+'-ring');
 const val=document.getElementById(id+'-val');
 if(!ring||!val)return;
 const r=parseFloat(ring.getAttribute('r'));
 const c=2*Math.PI*r;
 const offset=c-(pct/100)*c;
 ring.style.strokeDashoffset=offset;
 val.textContent=Math.round(pct)+'%';
}

let chartInited=false;

function renderChartModal(info){
 const memPct=info.memory.total_mb?(info.memory.used_mb/info.memory.total_mb*100):0;
 const memColor=memPct>80?'var(--red)':memPct>60?'var(--yellow)':'var(--blue)';
 const diskPct=info.disk?info.disk.percent:0;
 const diskColor=diskPct>80?'var(--red)':diskPct>60?'var(--yellow)':'var(--cyan)';
 const cpuPct=info.cpu_percent||0;
 const cpuColor=cpuPct>80?'var(--red)':cpuPct>60?'var(--yellow)':'var(--green)';
 document.getElementById('chart-title').textContent=t('status');
 if(!chartInited){
  let html='';
  html+=`<div class="chart-modal-item">${ringChart('chart-mem',0,memColor,130)}<div class="chart-modal-label" id="chart-mem-label">${t('memory_used')}</div><div class="chart-modal-detail" id="chart-mem-detail">0 / 0 MB</div></div>`;
  html+=`<div class="chart-modal-item">${ringChart('chart-disk',0,diskColor,130)}<div class="chart-modal-label" id="chart-disk-label">${t('disk_usage')}</div><div class="chart-modal-detail" id="chart-disk-detail">0 / 0 MB</div></div>`;
  html+=`<div class="chart-modal-item">${ringChart('chart-cpu',0,cpuColor,130)}<div class="chart-modal-label" id="chart-cpu-label">${t('cpu_load')}</div><div class="chart-modal-detail" id="chart-cpu-detail">0%</div></div>`;
  document.getElementById('chart-content').innerHTML=html;
  chartInited=true;
 }
 const memRing=document.getElementById('chart-mem-ring');
 if(memRing)memRing.style.stroke=memColor;
 updateRing('chart-mem',memPct);
 document.getElementById('chart-mem-detail').textContent=`${info.memory.used_mb||0} / ${info.memory.total_mb||0} MB`;
 const diskRing=document.getElementById('chart-disk-ring');
 if(diskRing)diskRing.style.stroke=diskColor;
 updateRing('chart-disk',diskPct);
 document.getElementById('chart-disk-detail').textContent=`${info.disk?info.disk.used_mb:0} / ${info.disk?info.disk.total_mb:0} MB`;
 const cpuRing=document.getElementById('chart-cpu-ring');
 if(cpuRing)cpuRing.style.stroke=cpuColor;
 updateRing('chart-cpu',cpuPct);
 document.getElementById('chart-cpu-detail').textContent=`${cpuPct}%`;
}

async function showCharts(){
 if(!lastDashInfo){lastDashInfo=await api('status');}
 if(!lastDashInfo||lastDashInfo.error)return;
 renderChartModal(lastDashInfo);
 document.getElementById('chart-overlay').classList.add('active');
}

function closeChartModal(){
 document.getElementById('chart-overlay').classList.remove('active');
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
   const checked='';
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
       <h3>Server Core</h3>
       <p>Current: <strong>${esc(jarName)}</strong></p>
       <div class="drop-zone" id="drop-core" ondragover="handleDrag(event,this)" ondragleave="handleDragLeave(this)" ondrop="handleDropCore(event,this)" onclick="this.querySelector('input').click()">
        <input type="file" accept=".jar" onchange="uploadCore(this.files[0]);this.value=''">
        <div class="drop-text">Drop .jar here</div>
       </div>
       <div id="core-replace-opts" style="display:none">
        ${togglesHtml}
        <button class="btn btn-accent" onclick="installCore()" style="margin-top:14px">${t('install_core')}</button>
       </div>
       <div style="margin-top:16px;border-top:1px solid var(--border);padding-top:14px">
        <p style="color:var(--text2);font-size:12px;margin-bottom:8px">${t('download_core')}</p>
        <div style="display:flex;flex-wrap:wrap;gap:8px;position:relative">
         <button class="btn btn-accent btn-sm" onclick="showCoreVersions('vanilla',this)">Vanilla</button>
         <button class="btn btn-accent btn-sm" onclick="showCoreVersions('purpur',this)">Purpur</button>
         <button class="btn btn-accent btn-sm" onclick="showCoreVersions('fabric',this)">Fabric</button>
         <button class="btn btn-accent btn-sm" onclick="showCoreVersions('mohist',this)">Mohist</button>
         <div id="core-versions-panel" style="display:none;position:absolute;top:100%;left:0;right:0;background:var(--surface2);border:1px solid var(--border);border-radius:12px;box-shadow:0 10px 40px rgba(0,0,0,.4);z-index:10;margin-top:8px;overflow:hidden">
          <div style="padding:12px 16px;border-bottom:1px solid var(--border)"><h4 id="core-versions-title" style="margin:0;font-size:14px"></h4><p id="core-versions-java" style="color:var(--text2);font-size:11px;margin:4px 0 0"></p></div>
          <div id="core-versions-list" style="max-height:300px;overflow-y:auto"></div>
         </div>
        </div>
       </div>
      </div>
     <div class="panel">
       <h3>Server Files</h3>
       <div id="setup-files"></div>
     </div>
    </div>`;
    loadSetupFiles();
   }else{
    el.innerHTML=`
    <div class="setup-card">
      <h3>Setup Minecraft Server</h3>
      <p>Upload a server .jar file to get started.</p>
      <div class="drop-zone" id="drop-core" ondragover="handleDrag(event,this)" ondragleave="handleDragLeave(this)" ondrop="handleDropCore(event,this)" onclick="this.querySelector('input').click()">
       <input type="file" accept=".jar" onchange="uploadCore(this.files[0]);this.value=''">
       <div class="drop-text">Drop server.jar here</div>
      </div>
      <div id="core-replace-opts" style="display:none">
       ${togglesHtml}
       <button class="btn btn-accent" onclick="installCore()" style="margin-top:14px">${t('install_core')}</button>
      </div>
      <div style="margin-top:20px;border-top:1px solid var(--border);padding-top:14px">
       <p style="color:var(--text2);font-size:12px;margin-bottom:8px">${t('download_core')}</p>
       <div style="display:flex;flex-wrap:wrap;gap:8px;position:relative">
        <button class="btn btn-accent btn-sm" onclick="showCoreVersions('vanilla',this)">Vanilla</button>
        <button class="btn btn-accent btn-sm" onclick="showCoreVersions('purpur',this)">Purpur</button>
        <button class="btn btn-accent btn-sm" onclick="showCoreVersions('fabric',this)">Fabric</button>
        <button class="btn btn-accent btn-sm" onclick="showCoreVersions('mohist',this)">Mohist</button>
        <div id="core-versions-panel" style="display:none;position:absolute;top:100%;left:0;right:0;background:var(--surface2);border:1px solid var(--border);border-radius:12px;box-shadow:0 10px 40px rgba(0,0,0,.4);z-index:10;margin-top:8px;overflow:hidden">
         <div style="padding:12px 16px;border-bottom:1px solid var(--border)"><h4 id="core-versions-title" style="margin:0;font-size:14px"></h4><p id="core-versions-java" style="color:var(--text2);font-size:11px;margin:4px 0 0"></p></div>
         <div id="core-versions-list" style="max-height:300px;overflow-y:auto"></div>
        </div>
       </div>
      </div>
      </div>`;
   }
}

let pendingCoreFile=null;
let pendingCoreUrl=null;
let pendingCoreName='';
async function uploadCore(file){
 if(!file||!file.name.endsWith('.jar')){toast('Only .jar files');return;}
 pendingCoreFile=file;
 pendingCoreUrl=null;
 pendingCoreName=file.name;
 const info=await api('status');
 if(info.has_jar||true){
  const panel=document.getElementById('core-replace-opts');
  if(panel)panel.style.display='block';
 }
 toast('File selected: '+file.name);
}

async function installCore(){
 const opts=document.getElementById('core-replace-opts');
 if(opts)opts.style.display='none';
 const keepMap={'keep-world':['world','world_nether','world_the_end'],'keep-mods':['mods'],'keep-plugins':['plugins'],'keep-ops':['ops.json'],'keep-bans':['banned-players.json'],'keep-bansip':['banned-ips.json'],'keep-wl':['whitelist.json'],'keep-props':['server.properties']};
 const dels={};
 for(const[key,paths]of Object.entries(keepMap)){
  const el=document.getElementById(key);
  if(el&&!el.disabled&&!el.checked){paths.forEach(p=>{dels[p]=true;});}
 }
 if(pendingCoreFile){
  if(!confirm(t('install_core')+': '+pendingCoreFile.name+'?'))return;
  toast(t('uploading')+' '+pendingCoreFile.name+'...');
  try{
   const fd=new FormData();
   fd.append('file',pendingCoreFile);
   fd.append('type','core');
   fd.append('keep_data',JSON.stringify(dels));
   const r=await fetch('/api/upload-core',{method:'POST',body:fd,credentials:'same-origin'});
   const j=await r.json();
   if(j.error){toast(j.error);return;}
   toast(j.message||t('uploaded'));
   pendingCoreFile=null;
   loadSetup();
  }catch(e){toast('Error: '+e.message);}
 }else if(pendingCoreUrl){
  if(!confirm(t('install_core')+': '+pendingCoreName+'?'))return;
  toast(t('uploading')+' '+pendingCoreName+'...');
  try{
   const r=await api('download-core',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:pendingCoreUrl,keep_data:dels})});
   if(r.error){toast(r.error);return;}
   toast(r.message||t('uploaded'));
   loadSetup();
  }catch(e){toast('Error: '+e.message);}
 }
}

const CORE_VERSIONS={
 vanilla:{
  name:'Vanilla',
  versions:[
   {v:'1.21.5',java:25,url:'https://piston-data.mojang.com/v1/objects/e6ec2f64e6080b9b5d9b471b291c33cc7f509733/server.jar'},
   {v:'1.21.4',java:21,url:'https://piston-data.mojang.com/v1/objects/4707d00eb834b446575d89a61a11b5d548d8c001/server.jar'},
   {v:'1.21.3',java:21,url:'https://piston-data.mojang.com/v1/objects/45810d238246d90e811d896f87b14695b7fb6839/server.jar'},
   {v:'1.21.2',java:21,url:'https://piston-data.mojang.com/v1/objects/7bf95409b0d9b5388bfea3704ec92012d273c14c/server.jar'},
   {v:'1.21.1',java:21,url:'https://piston-data.mojang.com/v1/objects/59353fb40c36d304f2035d51e7d6e6baa98dc05c/server.jar'},
   {v:'1.20.6',java:21,url:'https://piston-data.mojang.com/v1/objects/145ff0858209bcfc164859ba735d4199aafa1eea/server.jar'},
   {v:'1.20.4',java:17,url:'https://piston-data.mojang.com/v1/objects/8dd1a28015f51b1803213892b50b7b4fc76e594d/server.jar'},
   {v:'1.20.2',java:17,url:'https://piston-data.mojang.com/v1/objects/5b868151bd02b41319f54c8d4061b8cae84e665c/server.jar'},
   {v:'1.20.1',java:17,url:'https://piston-data.mojang.com/v1/objects/84194a2f286ef7c14ed7ce0090dba59902951553/server.jar'},
   {v:'1.19.4',java:17,url:'https://piston-data.mojang.com/v1/objects/8f3112a1049751cc472ec13e397eade5336ca7ae/server.jar'},
   {v:'1.19.2',java:17,url:'https://piston-data.mojang.com/v1/objects/f69c284232d7c7580bd89a5a4931c3581eae1378/server.jar'},
   {v:'1.18.2',java:17,url:'https://piston-data.mojang.com/v1/objects/c8f83c5655308435b3dcf03c06d9fe8740a77469/server.jar'},
   {v:'1.17.1',java:16,url:'https://piston-data.mojang.com/v1/objects/a16d67e5807f57fc4e550299cf20226194497dc2/server.jar'},
   {v:'1.16.5',java:8,url:'https://piston-data.mojang.com/v1/objects/1b557e7b033b583cd9f66746b7a9ab1ec1673ced/server.jar'},
   {v:'1.12.2',java:8,url:'https://piston-data.mojang.com/v1/objects/886945bfb2b978778c3a0288fd7fab09d315b25f/server.jar'},
  ]
 },
  fabric:{
   name:'Fabric',
   versions:[
    {v:'1.21.5',java:21,url:'https://meta.fabricmc.net/v2/versions/loader/1.21.5/0.19.3/1.0.1/server/jar'},
    {v:'1.21.4',java:21,url:'https://meta.fabricmc.net/v2/versions/loader/1.21.4/0.19.3/1.0.1/server/jar'},
    {v:'1.21.3',java:21,url:'https://meta.fabricmc.net/v2/versions/loader/1.21.3/0.19.3/1.0.1/server/jar'},
    {v:'1.21.2',java:21,url:'https://meta.fabricmc.net/v2/versions/loader/1.21.2/0.19.3/1.0.1/server/jar'},
    {v:'1.21.1',java:21,url:'https://meta.fabricmc.net/v2/versions/loader/1.21.1/0.19.3/1.0.1/server/jar'},
    {v:'1.20.6',java:21,url:'https://meta.fabricmc.net/v2/versions/loader/1.20.6/0.19.3/1.0.1/server/jar'},
    {v:'1.20.4',java:17,url:'https://meta.fabricmc.net/v2/versions/loader/1.20.4/0.19.3/1.0.1/server/jar'},
    {v:'1.20.2',java:17,url:'https://meta.fabricmc.net/v2/versions/loader/1.20.2/0.19.3/1.0.1/server/jar'},
    {v:'1.20.1',java:17,url:'https://meta.fabricmc.net/v2/versions/loader/1.20.1/0.19.3/1.0.1/server/jar'},
    {v:'1.19.4',java:17,url:'https://meta.fabricmc.net/v2/versions/loader/1.19.4/0.19.3/1.0.1/server/jar'},
    {v:'1.18.2',java:17,url:'https://meta.fabricmc.net/v2/versions/loader/1.18.2/0.19.3/1.0.1/server/jar'},
    {v:'1.16.5',java:8,url:'https://meta.fabricmc.net/v2/versions/loader/1.16.5/0.19.3/1.0.1/server/jar'},
   ]
  },
  purpur:{
   name:'Purpur',
   versions:[
    {v:'1.21.1',java:21,url:'https://api.purpurmc.org/v2/purpur/1.21.1/2329/download'},
    {v:'1.20.4',java:17,url:'https://api.purpurmc.org/v2/purpur/1.20.4/2176/download'},
    {v:'1.20.2',java:17,url:'https://api.purpurmc.org/v2/purpur/1.20.2/2095/download'},
    {v:'1.20.1',java:17,url:'https://api.purpurmc.org/v2/purpur/1.20.1/2062/download'},
    {v:'1.19.4',java:17,url:'https://api.purpurmc.org/v2/purpur/1.19.4/1985/download'},
    {v:'1.18.2',java:17,url:'https://api.purpurmc.org/v2/purpur/1.18.2/1632/download'},
    {v:'1.16.5',java:8,url:'https://api.purpurmc.org/v2/purpur/1.16.5/1171/download'},
   ]
  },
  mohist:{
   name:'Mohist',
   versions:[
    {v:'1.20.1 (build 54)',java:17,url:'https://api.mohistmc.com/project/mohist/1.20.1/builds/54/download'},
    {v:'1.20.2 (build 88)',java:17,url:'https://api.mohistmc.com/project/mohist/1.20.2/builds/88/download'},
    {v:'1.19.4 (build 90)',java:17,url:'https://api.mohistmc.com/project/mohist/1.19.4/builds/90/download'},
    {v:'1.18.2 (build 87)',java:17,url:'https://api.mohistmc.com/project/mohist/1.18.2/builds/87/download'},
    {v:'1.16.5 (build 65)',java:8,url:'https://api.mohistmc.com/project/mohist/1.16.5/builds/65/download'},
    {v:'1.12.2 (build 61)',java:8,url:'https://api.mohistmc.com/project/mohist/1.12.2/builds/61/download'},
   ]
  }
};

let javaVersion=0;

function showCoreVersions(core,btn){
 const panel=document.getElementById('core-versions-panel');
 if(!panel)return;
 if(panel.style.display==='block'){panel.style.display='none';return;}
 const data=CORE_VERSIONS[core];
 if(!data)return;
 javaVersion=parseInt(document.getElementById('set-java')?.value?.match(/v(\d+)/)?.[1]||'0');
 document.getElementById('core-versions-java').textContent='Java: v'+javaVersion;
 let html='';
 data.versions.forEach(v=>{
  const ok=javaVersion>=v.java;
  html+=`<div style="display:flex;align-items:center;justify-content:space-between;padding:10px 16px;border-bottom:1px solid var(--border);${ok?'cursor:pointer;':'opacity:0.4;'}" ${ok?`onclick="downloadCore('${v.url}','${data.name} ${v.v}');document.getElementById('core-versions-panel').style.display='none'"`:''}>`;
  html+=`<span style="font-size:13px;font-weight:500">${data.name} ${v.v}</span>`;
  html+=`<span style="font-size:11px;color:${ok?'var(--green)':'var(--red)'}">Java ${v.java}+</span>`;
  html+=`</div>`;
 });
 document.getElementById('core-versions-list').innerHTML=html;
 panel.style.display='block';
}

function closeCoreVersions(){
 document.getElementById('core-versions-panel').style.display='none';
}

document.addEventListener('click',function(e){
 var panel=document.getElementById('core-versions-panel');
 if(panel&&panel.style.display==='block'&&!e.target.closest('#core-versions-panel')&&!e.target.closest('[onclick*="showCoreVersions"]')){
  panel.style.display='none';
 }
 });

function showKeepToggles(callback){
  /* unused - toggles now shown directly in loadSetup */
 }

async function downloadCore(url,name){
 pendingCoreUrl=url;
 pendingCoreName=name;
 pendingCoreFile=null;
 const info=await api('status');
 const panel=document.getElementById('core-replace-opts');
 if(panel)panel.style.display='block';
}

async function doDownloadCore(){
 installCore();
}

async function doUploadCore(){
 installCore();
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
  confirmAction(t('confirm_delete')+': '+name+'?','removePlayerConfirm',apiType,name,'true');
}

async function removePlayerConfirm(type,name){
 const r=await api('player',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'remove',type,name})});
 toast(r.message||t('removed'));loadPlayers();
}

async function loadProperties(){
 const data=await api('properties');
 if(!data||data.error)return;
 const important=['server-port','server-ip','max-players','motd','gamemode','difficulty','pvp','white-list','online-mode','level-name','view-distance','simulation-distance','spawn-protection','max-world-size','level-seed','allow-flight','hardcore','enable-command-block'];
 const dropdowns={
  'gamemode':['survival','creative','adventure','spectator'],
  'difficulty':['peaceful','easy','normal','hard'],
  'level-type':['minecraft\\:normal','minecraft\\:flat','minecraft\\:largebiomes','minecraft\\:amplified','minecraft\\:single_biome_surface'],
  'pvp':['true','false'],
  'white-list':['true','false'],
  'online-mode':['true','false'],
  'hardcore':['true','false'],
  'enable-command-block':['true','false'],
  'allow-flight':['true','false'],
  'force-gamemode':['true','false'],
  'spawn-animals':['true','false'],
  'spawn-monsters':['true','false'],
  'spawn-npcs':['true','false'],
  'generate-structures':['true','false'],
  'enable-rcon':['true','false'],
  'sync-chunk-writes':['true','false'],
  'use-native-transport':['true','false'],
  'prevent-proxy-connections':['true','false'],
 };
 let html='';
 important.forEach(k=>{
  if(!(k in data))return;
  const v=data[k];
  if(k in dropdowns){
   const opts=dropdowns[k];
   const selId='prop-'+k.replace(/[^a-z0-9]/g,'-');
   html+=`<div class="form-group"><label>${k}</label><div class="custom-select" onclick="toggleCustomSelect(this)"><div class="custom-select-selected" data-key="${k}" data-value="${esc(v)}">${esc(v)}</div><div class="custom-select-options">`;
   opts.forEach(o=>{
    html+=`<div class="custom-select-option${o===v?' selected':''}" data-value="${esc(o)}" onclick="selectProp(this)">${esc(o)}</div>`;
   });
   html+=`</div></div></div>`;
  }else{
   html+=`<div class="form-group"><label>${k}</label><input type="text" data-key="${k}" value="${esc(v)}"${k==='level-name'?' data-orig="'+esc(v)+'"':''}></div>`;
  }
 });
 document.getElementById('props-form').innerHTML=html;
}

function selectProp(el){
 const sel=el.closest('.custom-select');
 sel.querySelector('.custom-select-selected').textContent=el.textContent;
 sel.querySelector('.custom-select-selected').dataset.value=el.dataset.value;
 sel.querySelectorAll('.custom-select-option').forEach(o=>o.classList.remove('selected'));
 el.classList.add('selected');
 sel.classList.remove('open');
}

async function saveProperties(){
 const props={};
 document.querySelectorAll('#props-form .custom-select-selected[data-key]').forEach(el=>{
  props[el.dataset.key]=el.dataset.value;
 });
 document.querySelectorAll('#props-form input[data-key]').forEach(inp=>{
  props[inp.dataset.key]=inp.value;
 });
 if(props['level-name']){
  const old=document.querySelector('#props-form input[data-key="level-name"]');
  if(old&&old.dataset.orig&&old.dataset.orig!==props['level-name']){
   if(!confirm('Changing level-name will create a new world. Continue?')){
    old.value=old.dataset.orig;return;
   }
  }
 }
 const r=await api('properties',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(props)});
 toast(r.message||'Properties saved');
}

const EDITABLE_EXTS=['.json','.txt','.yml','.yaml','.properties','.cfg','.conf','.ini','.xml','.toml','.sh','.bat','.py','.js','.log','.md','.csv','.env','.c','.cpp','.h','.java','.rb','.go','.rs','.lua','.php','.ts','.tsx','.jsx','.css','.scss','.less','.sql','.graphql','.proto','.dockerfile','.tf','.hcl','.nix','.swift','.kt','.scala','.r','.m','.pl','.pm','.tcl','.asm','.s','.cobol','.f90','.vb','.cs','.fs','.hs','.ex','.exs','.erl','.clj','.lisp','.rkt','.jl','.nim','.d','.groovy','.gradle','.cmake','.make','.mk','.awk','.sed','.vim','.el','.elv','.fish','.zsh','.bash','.ps1','.psm1','.psd1'];
let currentEditFile='';
let currentDir='';
let fileSortField='name';
let fileSortAsc=true;
let allFiles=[];
let searchDebounce=null;

function sortFiles(files,field,asc){
 const dirs=files.filter(f=>f.type==='dir');
 const regular=files.filter(f=>f.type==='file');
 const collator=new Intl.Collator(undefined,{numeric:true,sensitivity:'base'});
 const cmp=(a,b)=>{
  if(field==='name')return collator.compare(a.name,b.name)*asc;
  return((a.size_bytes||0)-(b.size_bytes||0))*asc;
 };
 dirs.sort(cmp);
 regular.sort(cmp);
 return[...dirs,...regular];
}

async function filterFiles(){
 clearTimeout(searchDebounce);
 const q=(document.getElementById('file-search')?.value||'').trim();
 const recursive=document.getElementById('file-search-recursive')?.checked;
 if(q.length>0&&recursive){
  searchDebounce=setTimeout(async()=>{
   const url='search?q='+encodeURIComponent(q)+(currentDir?'&path='+encodeURIComponent(currentDir):'');
   const data=await api(url);
   if(!data||data.error)return;
   renderSearchResults(data.files||[],q);
  },300);
 }else{
  renderFiles();
 }
}

function renderSearchResults(files,q){
 let html='<table><tr><th>'+t('sort_by_name')+'</th><th>'+t('sort_by_size')+'</th><th></th></tr>';
 if(currentDir){
  const parent=currentDir.split('/').slice(0,-1).join('/');
  html+=`<tr style="cursor:pointer" onclick="loadFiles('${esc(parent)}')"><td style="color:var(--accent)"><svg class="ico ico-sm" viewBox="0 0 24 24"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg> ..</td><td style="color:var(--text2)">-</td><td></td></tr>`;
 }
 files.forEach(f=>{
  const ext=f.name.includes('.')?'.'+f.name.split('.').pop().toLowerCase():'';
  const editable=EDITABLE_EXTS.includes(ext);
  const dir=f.path?f.path.substring(0,f.path.lastIndexOf('/')):'';
  const dlBtn=`<a href="/api/file-download?name=${encodeURIComponent(f.path)}" onclick="event.stopPropagation()" download style="padding:4px 8px;display:inline-flex;align-items:center;justify-content:center;border-radius:8px;background:var(--accent);color:#fff;text-decoration:none;transition:all .2s" onmouseover="this.style.filter='brightness(1.1)'" onmouseout="this.style.filter=''"><svg class="ico ico-sm" viewBox="0 0 24 24" style="width:14px;height:14px;stroke:#fff;fill:none"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7,10 12,15 17,10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></a>`;
  const delBtn=`<button class="btn btn-red btn-sm" style="padding:4px 8px" onclick="event.stopPropagation();deleteFileItem('${esc(f.path).replace(/'/g,"\\'")}')"><svg class="ico ico-sm" viewBox="0 0 24 24" style="width:14px;height:14px"><polyline points="3,6 5,6 21,6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button>`;
  const actions=`<span style="display:flex;gap:4px;justify-content:flex-end">${dlBtn}${delBtn}</span>`;
  if(editable){
   html+=`<tr style="cursor:pointer" onclick="openFileEditor('${esc(f.path).replace(/'/g,"\\'")}')"><td style="color:var(--accent);font-weight:600">${esc(f.name)}${dir?` <span style="color:var(--text2);font-weight:400;font-size:12px">${esc(dir)}</span>`:''}</td><td style="color:var(--text2)">${f.size}</td><td style="text-align:right">${actions}</td></tr>`;
  }else{
   html+=`<tr><td>${esc(f.name)}${dir?` <span style="color:var(--text2);font-size:12px">${esc(dir)}</span>`:''}</td><td style="color:var(--text2)">${f.size}</td><td style="text-align:right">${actions}</td></tr>`;
  }
 });
 html+='</table>';
 document.getElementById('file-list').innerHTML=html||`<div class="empty">${t('no_files')}</div>`;
}

function renderFiles(){
 const q=(document.getElementById('file-search')?.value||'').toLowerCase();
 let files=sortFiles(allFiles,fileSortField,fileSortAsc);
 if(q)files=files.filter(f=>f.name.toLowerCase().includes(q));
 let html='<table><tr><th style="cursor:pointer" onclick="toggleSort(\'name\')">'+t('sort_by_name')+'</th><th style="cursor:pointer" onclick="toggleSort(\'size\')">'+t('sort_by_size')+'</th><th></th></tr>';
 if(currentDir){
  const parent=currentDir.split('/').slice(0,-1).join('/');
  html+=`<tr style="cursor:pointer" onclick="loadFiles('${esc(parent)}')"><td style="color:var(--accent)"><svg class="ico ico-sm" viewBox="0 0 24 24"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg> ..</td><td style="color:var(--text2)">-</td><td></td></tr>`;
 }
 files.forEach(f=>{
  const isDir=f.type==='dir';
  const ext=f.name.includes('.')?'.'+f.name.split('.').pop().toLowerCase():'';
  const editable=!isDir&&EDITABLE_EXTS.includes(ext);
  const fp=currentDir?currentDir+'/'+f.name:f.name;
  const dlBtn=`<a href="/api/file-download?name=${encodeURIComponent(fp)}" onclick="event.stopPropagation()" download style="padding:4px 8px;display:inline-flex;align-items:center;justify-content:center;border-radius:8px;background:var(--accent);color:#fff;text-decoration:none;transition:all .2s" onmouseover="this.style.filter='brightness(1.1)'" onmouseout="this.style.filter=''"><svg class="ico ico-sm" viewBox="0 0 24 24" style="width:14px;height:14px;stroke:#fff;fill:none"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7,10 12,15 17,10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></a>`;
  const delBtn=`<button class="btn btn-red btn-sm" style="padding:4px 8px" onclick="event.stopPropagation();deleteFileItem('${esc(fp).replace(/'/g,"\\'")}')"><svg class="ico ico-sm" viewBox="0 0 24 24" style="width:14px;height:14px"><polyline points="3,6 5,6 21,6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button>`;
  const actions=`<span style="display:flex;gap:4px;justify-content:flex-end">${dlBtn}${delBtn}</span>`;
  if(isDir){
   const dirPath=currentDir?currentDir+'/'+f.name.replace('/',''):f.name.replace('/','');
   html+=`<tr style="cursor:pointer" onclick="loadFiles('${esc(dirPath)}')"><td style="color:var(--accent)"><svg class="ico ico-sm" viewBox="0 0 24 24"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg> ${esc(f.name)}</td><td style="color:var(--text2)">${f.size}</td><td style="text-align:right">${actions}</td></tr>`;
  }else if(editable){
   html+=`<tr style="cursor:pointer" onclick="openFileEditor('${esc(fp).replace(/'/g,"\\'")}')"><td style="color:var(--accent);font-weight:600">${esc(f.name)}</td><td style="color:var(--text2)">${f.size}</td><td style="text-align:right">${actions}</td></tr>`;
  }else{
   html+=`<tr><td>${esc(f.name)}</td><td style="color:var(--text2)">${f.size}</td><td style="text-align:right">${actions}</td></tr>`;
  }
 });
 html+='</table>';
 document.getElementById('file-list').innerHTML=html||`<div class="empty">${t('no_files')}</div>`;
 updateSortButtons();
}

function updateSortButtons(){
 const table=document.querySelector('#file-list table');
 if(!table)return;
 const ths=table.querySelectorAll('th');
 if(ths.length<2)return;
 const labels=[t('sort_by_name'),t('sort_by_size'),''];
 const fields=['name','size',''];
 ths.forEach((th,i)=>{
  const f=fields[i]||'';
  const active=fileSortField===f;
  const arrow=active?(fileSortAsc?'\u25B2':'\u25BC'):'';
  th.innerHTML=(labels[i]||'')+(f?' '+arrow:'');
  th.style.color=active?'var(--accent)':'';
  th.style.cursor=f?'pointer':'default';
 });
}

function toggleSort(field){
 if(fileSortField===field){fileSortAsc=!fileSortAsc;}
 else{fileSortField=field;fileSortAsc=true;}
 renderFiles();
}

async function loadFiles(path){
 currentDir=path||'';
 updateCreateFileLabel();
 if(document.getElementById('file-search'))document.getElementById('file-search').value='';
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
 allFiles=data.files||[];
 renderFiles();
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

async function createFolder(){
 showInputModal(t('create_folder'),t('enter_name'),async name=>{
  const r=await api('file-mkdir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,path:currentDir||''})});
  if(r.error){toast(r.error);return;}
  toast(r.message||t('saved'));
  loadFiles(currentDir);
 });
}

function updateCreateFileLabel(){
 const labels={en:'Create File',ru:'Создать файл',de:'Datei erstellen',fr:'Créer un fichier',zh:'创建文件'};
 const el=document.getElementById('create-file-btn');
 if(el)el.textContent=labels[currentLang]||labels.en;
}

async function createFile(){
 const labels={en:'File name',ru:'Имя файла',de:'Dateiname',fr:'Nom du fichier',zh:'文件名'};
 const titles={en:'Create File',ru:'Создать файл',de:'Datei erstellen',fr:'Créer un fichier',zh:'创建文件'};
 showInputModal(titles[currentLang]||titles.en,labels[currentLang]||labels.en,async name=>{
  const fileName=currentDir?currentDir+'/'+name:name;
  const r=await api('file-write',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:fileName,content:''})});
  if(r.error){toast(r.error);return;}
  toast(r.message||t('saved'));
  loadFiles(currentDir);
 });
}

async function uploadFileToDir(file){
 if(!file)return;
 const fd=new FormData();
 fd.append('file',file);
 fd.append('path',currentDir||'');
 toast(t('uploading')+' '+file.name+'...');
 const r=await fetch('/api/file-upload',{method:'POST',body:fd,credentials:'same-origin'});
 const d=await r.json();
 toast(d.message||t('uploaded'));
 loadFiles(currentDir);
}

async function deleteFileItem(name){
  confirmAction(t('confirm_delete_file')+': '+name.split('/').pop()+'?','deleteFileItemConfirm',name,'','true');
}

async function deleteFileItemConfirm(name){
 const r=await api('file-delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
 if(r.error){toast(r.error);return;}
 toast(r.message||t('removed'));
 loadFiles(currentDir);
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
  let html=`<div style="display:flex;gap:8px;margin-bottom:10px;align-items:center">`;
  if(plugins.length>2)html+=`<button class="btn btn-red btn-sm" onclick="confirmAction('${t('confirm_delete_all')} plugins?','deleteAll','plugins','','true')">${t('delete_all')} (${plugins.length})</button>`;
  html+=`<input type="text" placeholder="Search..." oninput="filterList('plugins-list',this.value)" style="flex:1;padding:6px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;box-sizing:border-box">`;
  html+=`</div>`;
  html+=plugins.map(p=>`<div class="plugin-item" data-name="${esc(p).toLowerCase()}"><span class="name">${esc(p)}</span><button class="btn btn-red btn-sm" onclick="confirmAction('${t('confirm_delete')}: ${esc(p).replace(/'/g,"\\'")}?','deleteItem','plugins','${esc(p).replace(/'/g,"\\'")}','true')"><svg class="ico ico-sm" viewBox="0 0 24 24"><polyline points="3,6 5,6 21,6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg></button></div>`).join('');
  document.getElementById('plugins-list').innerHTML=html;
 }else{
  document.getElementById('plugins-list').innerHTML=`<div class="empty">${t('no_plugins')}</div>`;
 }
 if(mods&&mods.length){
  let html=`<div style="display:flex;gap:8px;margin-bottom:10px;align-items:center">`;
  if(mods.length>2)html+=`<button class="btn btn-red btn-sm" onclick="confirmAction('${t('confirm_delete_all')} mods?','deleteAll','mods','','true')">${t('delete_all')} (${mods.length})</button>`;
  html+=`<input type="text" placeholder="Search..." oninput="filterList('mods-list',this.value)" style="flex:1;padding:6px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;box-sizing:border-box">`;
  html+=`</div>`;
  html+=mods.map(m=>`<div class="mod-item" data-name="${esc(m).toLowerCase()}"><span class="name">${esc(m)}</span><button class="btn btn-red btn-sm" onclick="confirmAction('${t('confirm_delete')}: ${esc(m).replace(/'/g,"\\'")}?','deleteItem','mods','${esc(m).replace(/'/g,"\\'")}','true')"><svg class="ico ico-sm" viewBox="0 0 24 24"><polyline points="3,6 5,6 21,6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg></button></div>`).join('');
  document.getElementById('mods-list').innerHTML=html;
 }else{
  document.getElementById('mods-list').innerHTML=`<div class="empty">${t('no_mods')}</div>`;
 }
}

function filterList(listId,query){
 const q=query.toLowerCase();
 document.querySelectorAll('#'+listId+' > div[data-name]').forEach(el=>{
  el.style.display=el.dataset.name.includes(q)?'':'none';
 });
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
  const r=await fetch('/api/upload',{method:'POST',body:fd,credentials:'same-origin'});
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
function selectUserRole(role){
 const select=document.getElementById('new-user-role');
 const selected=document.getElementById('role-selected');
 if(select)select.value=role;
 if(selected){selected.dataset.value=role;selected.textContent=role==='admin'?t('administrator'):t('user');}
 document.querySelectorAll('#role-select .custom-select-option').forEach(o=>o.classList.toggle('selected',o.dataset.value===role));
 document.getElementById('role-select')?.classList.remove('open');
}
document.addEventListener('click',e=>{
 if(!e.target.closest('.custom-select'))document.querySelectorAll('.custom-select').forEach(s=>s.classList.remove('open'));
});

let envData={};

async function loadEnvInfo(){
 try{
  envData=await api('env-info');
  if(envData.mc_dir&&document.getElementById('set-mcdir'))document.getElementById('set-mcdir').value=envData.mc_dir;
  if(envData.port&&document.getElementById('set-port'))document.getElementById('set-port').value=envData.port;
  if(envData.java_path)document.getElementById('set-java').value=envData.java_path+' (v'+envData.java_version+')';
  if(envData.platform)document.getElementById('set-screen').value=envData.platform;
  const rconStatus=envData.rcon_enabled?'Enabled (port '+envData.rcon_port+')':'Disabled';
  if(document.getElementById('set-rcon'))document.getElementById('set-rcon').value=rconStatus;
 }catch(e){}
}

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
   document.getElementById('fireflies-toggle').checked=!!data.fireflies;
   if(data.fireflies&&!firefliesOn){
    firefliesOn=true;
    initFireflies();
    document.getElementById('fireflies-canvas').classList.add('active');
    animateFireflies();
   }else if(!data.fireflies&&firefliesOn){
    firefliesOn=false;
    document.getElementById('fireflies-canvas').classList.remove('active');
    if(fireflyAnim){cancelAnimationFrame(fireflyAnim);fireflyAnim=null;}
   }
 await loadEnvInfo();
 // Authorization is account-based and is configured during installation;
 // the old enable/disable switch is intentionally no longer exposed.
   if(data.panel_opacity!==undefined){
    panelOpacity=data.panel_opacity;
    document.getElementById('opacity-slider').value=panelOpacity;
    document.getElementById('opacity-val').textContent=panelOpacity+'%';
    const a=(panelOpacity/100).toFixed(2);
    document.documentElement.style.setProperty('--panel-bg',`rgba(21,25,34,${a})`);
   }
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
 savedAccent=hex;
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
 const hex=savedAccent;
 const r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
 document.documentElement.style.setProperty('--accent',hex);
 document.documentElement.style.setProperty('--accent2',`rgb(${Math.min(255,r+40)},${Math.min(255,g+20)},${Math.min(255,b+60)})`);
 document.documentElement.style.setProperty('--accent-rgb',`${r},${g},${b}`);
 document.documentElement.style.setProperty('--glow',`0 0 20px rgba(${r},${g},${b},.15)`);
 document.documentElement.style.setProperty('--logo-color',getContrastColor(hex));
 const lum=(0.299*r+0.587*g+0.114*b)/255;
 document.documentElement.style.setProperty('--text',lum<.35?'#ffffff':'#e2e8f0');
 document.documentElement.style.setProperty('--text2',lum<.35?'#b0b8c8':'#8892a4');
 document.getElementById('color-picker').value=hex;
 document.getElementById('color-hex').textContent=hex;
 document.querySelectorAll('.color-swatch').forEach(s=>s.classList.toggle('active',rgbToHex(s.style.background)===hex));
 const a=(panelOpacity/100).toFixed(2);
 document.documentElement.style.setProperty('--panel-bg',`rgba(21,25,34,${a})`);
 const c=document.getElementById('fireflies-canvas');
 if(firefliesOn){
  if(!c.classList.contains('active'))c.classList.add('active');
  if(!fireflyAnim)animateFireflies();
 }else{
  c.classList.remove('active');
  if(fireflyAnim){cancelAnimationFrame(fireflyAnim);fireflyAnim=null;}
 }
}

async function saveToken(){
 const token=document.getElementById('set-token').value.trim();
 if(!token){toast('Enter a password');return;}
 const r=await api('set-token',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
 if(r.error){toast(r.error);return;}
 document.getElementById('token-status').textContent=r.message||'Saved';
 document.getElementById('set-token').value='';
}

async function removeToken(){
 const r=await api('remove-token',{method:'POST'});
 document.getElementById('token-status').textContent=r.message||'Removed';
 document.getElementById('set-token').value='';
}

function toggleAuth(on){
 const body=document.getElementById('auth-body');
 if(on){
  body.style.maxHeight=body.scrollHeight+'px';
  body.style.opacity='1';
 }else{
  body.style.maxHeight='0';
  body.style.opacity='0';
 }
 saveAuthState(on);
}

async function saveAuthState(enabled){
 await api('settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({auth_enabled:enabled})});
 if(!enabled)await api('remove-token',{method:'POST'});
}

 async function saveSettings(){
  const lang=currentLang;
  const mcdir=document.getElementById('set-mcdir').value.trim();
  const port=document.getElementById('set-port').value.trim();
  const javaargs=document.getElementById('set-javaargs').value.trim();
  const encoding=document.getElementById('set-encoding').value.trim();
  const accent=document.getElementById('color-picker').value;
  const ff=document.getElementById('fireflies-toggle').checked;
   await api('settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lang,accent,fireflies:ff,panel_opacity:panelOpacity})});
  const envData={MC_DIR:mcdir||'/minecraft',PANEL_PORT:port||'8080',JAVA_ARGS:javaargs,JAVA_ENCODING:encoding};
  await api('save-env',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(envData)});
   savedAccent=accent;
   firefliesOn=ff;
   if(ff&&!fireflyAnim){initFireflies();document.getElementById('fireflies-canvas').classList.add('active');animateFireflies();}
   else if(!ff&&fireflyAnim){document.getElementById('fireflies-canvas').classList.remove('active');cancelAnimationFrame(fireflyAnim);fireflyAnim=null;}
   T=await (await fetch('/api/lang',{credentials:'same-origin'})).json();
  if(!T.password_hint)T.password_hint=(EXTRA_TRANSLATIONS[currentLang]||EXTRA_TRANSLATIONS.en).password_hint;
  applyTranslations();
  showTab(currentTab);
  // Account name and other async UI fragments need an explicit refresh too;
  // they are not covered by the static data-i18n pass.
  loadCurrentAccount();
  toast(t('saved'));
 }

 async function checkForUpdates(){
  const btn=document.getElementById('btn-check-update');
  const result=document.getElementById('update-result');
  btn.disabled=true;
  btn.innerHTML='<svg viewBox="0 0 24 24" style="width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2" class="spin"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> '+t('check_update')+'...';
  try{
   const data=await api('check-update');
   lastUpdateResult=data;
   renderUpdateResult(data);
  }catch(e){
   lastUpdateResult={error:true};
   result.style.display='block';
   result.innerHTML='<div style="background:var(--surface2);border:1px solid var(--red);border-radius:8px;padding:12px"><p style="margin:0;font-size:13px;color:var(--red)">'+t('update_error')+'</p></div>';
  }
  btn.disabled=false;
  btn.innerHTML='<svg viewBox="0 0 24 24" style="width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> '+t('check_update');
 }

 async function doUpdate(){
  if(!confirm('Update panel? It will restart.'))return;
  const result=document.getElementById('update-result');
  result.innerHTML='<p style="font-size:13px;color:var(--accent)">Updating...</p>';
  try{
   const data=await api('do-update',{method:'POST'});
   if(data.ok){
    result.innerHTML='<p style="font-size:13px;color:var(--green)">Updated! Restarting...</p>';
    setTimeout(()=>location.reload(),3000);
   }else{
    result.innerHTML=`<p style="font-size:13px;color:var(--red)">Error: ${data.error}</p>`;
   }
  }catch(e){
   result.innerHTML='<p style="font-size:13px;color:var(--red)">Update failed</p>';
  }
 }

let savedAccent='%ACCENT%';
let firefliesOn=%FIREFLIES%;
let panelOpacity=%OPACITY%;
let fireflyAnim=null;
const fireflyDots=[];
const FIREFLY_COUNT=20;
if(savedAccent!=='#6c5ce7')applyAccent(savedAccent);
if(firefliesOn){setTimeout(()=>{initFireflies();document.getElementById('fireflies-canvas').classList.add('active');animateFireflies();},300);}
setTimeout(()=>{const a=(panelOpacity/100).toFixed(2);document.documentElement.style.setProperty('--panel-bg',`rgba(21,25,34,${a})`);},100);

function initFireflies(){
 if(fireflyAnim){cancelAnimationFrame(fireflyAnim);fireflyAnim=null;}
 const c=document.getElementById('fireflies-canvas');
 if(!c)return;
 c.width=window.innerWidth;
 c.height=window.innerHeight;
 fireflyDots.length=0;
 for(let i=0;i<FIREFLY_COUNT;i++){
  fireflyDots.push({
   x:Math.random()*c.width,
   y:Math.random()*c.height,
   r:Math.random()*2+1,
   dx:(Math.random()-.5)*.4,
   dy:(Math.random()-.5)*.4,
   alpha:Math.random(),
   da:(Math.random()-.5)*.01
  });
 }
}

function animateFireflies(){
 const c=document.getElementById('fireflies-canvas');
 if(!c||!firefliesOn)return;
 const ctx=c.getContext('2d');
 ctx.clearRect(0,0,c.width,c.height);
 const ac=savedAccent||'#6c5ce7';
 const r=parseInt(ac.slice(1,3),16),g=parseInt(ac.slice(3,5),16),b=parseInt(ac.slice(5,7),16);
  fireflyDots.forEach(d=>{
   d.x+=d.dx;d.y+=d.dy;
   d.alpha+=d.da;
   if(d.alpha>=1){d.da=-0.004;}
   if(d.alpha<=0){d.da=0.0015;d.alpha=0;}
   d.alpha=Math.max(0,Math.min(1,d.alpha));
   if(d.x<0||d.x>c.width)d.dx*=-1;
   if(d.y<0||d.y>c.height)d.dy*=-1;
   const glow=ctx.createRadialGradient(d.x,d.y,0,d.x,d.y,d.r*12);
   glow.addColorStop(0,`rgba(${r},${g},${b},${d.alpha})`);
   glow.addColorStop(.15,`rgba(${r},${g},${b},${d.alpha*.7})`);
   glow.addColorStop(.4,`rgba(${r},${g},${b},${d.alpha*.25})`);
   glow.addColorStop(1,`rgba(${r},${g},${b},0)`);
   ctx.beginPath();
   ctx.arc(d.x,d.y,d.r,0,Math.PI*2);
   ctx.fillStyle=`rgba(${r},${g},${b},${d.alpha})`;
   ctx.fill();
   ctx.beginPath();
   ctx.arc(d.x,d.y,d.r*12,0,Math.PI*2);
   ctx.fillStyle=glow;
   ctx.fill();
 });
 fireflyAnim=requestAnimationFrame(animateFireflies);
}

function toggleFireflies(on){
 firefliesOn=on;
 const c=document.getElementById('fireflies-canvas');
 if(on){
  initFireflies();
  c.classList.add('active');
  animateFireflies();
 }else{
  c.classList.remove('active');
  if(fireflyAnim){cancelAnimationFrame(fireflyAnim);fireflyAnim=null;}
  const ctx=c.getContext('2d');
  if(ctx)ctx.clearRect(0,0,c.width,c.height);
 }
}

function setPanelOpacity(val){
 panelOpacity=parseInt(val);
 document.getElementById('opacity-val').textContent=val+'%';
 const a=(val/100).toFixed(2);
 document.documentElement.style.setProperty('--panel-bg',`rgba(21,25,34,${a})`);
}

window.addEventListener('resize',()=>{
 const c=document.getElementById('fireflies-canvas');
 if(!c)return;
 c.width=window.innerWidth;
 c.height=window.innerHeight;
 if(firefliesOn&&!fireflyAnim)animateFireflies();
});

refreshDashboard();
loadLang();
loadEnvInfo();
setInterval(()=>{if(currentTab==='dashboard')refreshDashboard();},2000);
setInterval(()=>{if(currentTab==='players')loadOnlinePlayers();},3000);
</script>
</body>
</html>"""


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
            "https://raw.githubusercontent.com/fizyCH/FizMine/main/panel.py?t=" + str(int(time.time())),
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
        
        req = urllib.request.Request(
            "https://raw.githubusercontent.com/fizyCH/FizMine/main/panel.py?t=" + str(int(time.time())),
            headers={"User-Agent": "FizMine-Panel"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            remote = resp.read().decode("utf-8", errors="replace")
        
        remote_ver = re.search(r'PANEL_VERSION\s*=\s*"([^"]+)"', remote)
        remote_ver = remote_ver.group(1) if remote_ver else "0"
        
        if remote_ver == local_ver:
            return JSONResponse({"ok": True, "message": "Already up to date"})
        
        panel_path = Path(__file__).resolve()
        shutil.copy2(str(panel_path), str(panel_path) + ".bak")
        panel_path.write_text(remote, encoding="utf-8")
        
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
    main()
