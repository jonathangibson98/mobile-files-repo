"""mobile-files: standalone web UI for file management + Samba settings.

Single Flask app. Serves a static SPA and a JSON API. Samba config lives in
$CONFIG_DIR/config.json and is rendered to smb.conf on every change.
"""
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
from functools import wraps
from pathlib import Path

from flask import (Flask, jsonify, request, send_file, send_from_directory,
                   session)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")).resolve()
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config")).resolve()
SMB_CONF = Path(os.environ.get("SMB_CONF", "/etc/samba/smb.conf"))
CONFIG_FILE = CONFIG_DIR / "config.json"
SECRET_FILE = CONFIG_DIR / "secret.key"
PUID = int(os.environ.get("PUID", "1000"))
PGID = int(os.environ.get("PGID", "1000"))
SAMBA_ENABLED = os.environ.get("SAMBA_ENABLED", "1") == "1"
SYSTEM_USER = "files"

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")
WG_RE = re.compile(r"^[A-Za-z0-9_-]{1,15}$")

DEFAULT_CONFIG = {
    "global": {
        "workgroup": "WORKGROUP",
        "server_string": "Mobile Files",
        "min_protocol": "SMB2",
        "guest_map": True,
    },
    "shares": [
        {
            "name": "files",
            "path": "/data",
            "comment": "Shared files",
            "read_only": False,
            "guest_ok": False,
            "browseable": True,
            "valid_users": [],
            "write_list": [],
        }
    ],
    "users": [],
}

app = Flask(__name__, static_folder=None)
lock = threading.Lock()


# ---------------------------------------------------------------- config ---
def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open() as f:
            cfg = json.load(f)
    else:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg["shares"][0]["path"] = str(DATA_DIR)
        save_config(cfg)
    return cfg


def save_config(cfg):
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(CONFIG_FILE)


def render_smb_conf(cfg):
    g = cfg["global"]
    lines = [
        "[global]",
        f"   workgroup = {g['workgroup']}",
        f"   server string = {g['server_string']}",
        "   server role = standalone server",
        "   security = user",
        f"   server min protocol = {g['min_protocol']}",
        "   log file = /var/log/samba/log.%m",
        "   max log size = 1000",
        "   load printers = no",
        "   printing = bsd",
        "   printcap name = /dev/null",
        "   disable spoolss = yes",
        "   unix extensions = no",
        "   socket options = TCP_NODELAY",
    ]
    if g.get("guest_map", True):
        lines.append("   map to guest = Bad User")
    for s in cfg["shares"]:
        lines += [
            "",
            f"[{s['name']}]",
            f"   path = {s['path']}",
            f"   comment = {s.get('comment', '')}",
            f"   browseable = {'yes' if s.get('browseable', True) else 'no'}",
            f"   read only = {'yes' if s.get('read_only') else 'no'}",
            f"   guest ok = {'yes' if s.get('guest_ok') else 'no'}",
            f"   force user = {SYSTEM_USER}",
            f"   force group = {SYSTEM_USER}",
            "   create mask = 0664",
            "   directory mask = 0775",
        ]
        if s.get("valid_users"):
            lines.append(f"   valid users = {' '.join(s['valid_users'])}")
        if s.get("write_list"):
            lines.append(f"   write list = {' '.join(s['write_list'])}")
    return "\n".join(lines) + "\n"


def run(cmd, input_text=None, check=True):
    return subprocess.run(cmd, input=input_text, text=True,
                          capture_output=True, check=check)


def apply_samba(cfg):
    """Write smb.conf, validate with testparm, and reload smbd."""
    if not SAMBA_ENABLED:
        return
    conf = render_smb_conf(cfg)
    tmp = SMB_CONF.with_suffix(".new")
    tmp.write_text(conf)
    try:
        r = run(["testparm", "-s", str(tmp)], check=False)
        if r.returncode != 0:
            raise ValueError("Invalid Samba config: " + (r.stderr or r.stdout)[-400:])
        tmp.replace(SMB_CONF)
    finally:
        if tmp.exists():
            tmp.unlink()
    run(["smbcontrol", "all", "reload-config"], check=False)


def ensure_system_user(name):
    if run(["id", name], check=False).returncode != 0:
        run(["useradd", "-M", "-N", "-g", SYSTEM_USER, "-s", "/usr/sbin/nologin", name])


# ------------------------------------------------------------------ auth ---
def get_secret():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
        SECRET_FILE.chmod(0o600)
    return SECRET_FILE.read_text().strip()


def admin_password():
    pw = os.environ.get("ADMIN_PASSWORD")
    if pw:
        return pw
    pwfile = CONFIG_DIR / "admin_password"
    if not pwfile.exists():
        pwfile.write_text(secrets.token_urlsafe(12))
        pwfile.chmod(0o600)
        print(f"[mobile-files] No ADMIN_PASSWORD set. Generated one: "
              f"{pwfile.read_text()}", flush=True)
    return pwfile.read_text().strip()


app.secret_key = get_secret()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                  MAX_CONTENT_LENGTH=None)
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PW = admin_password()
_fails = {}


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("auth"):
            return jsonify(error="Not signed in"), 401
        if request.method in ("POST", "PUT", "DELETE") and \
                request.headers.get("X-Requested-With") != "mobile-files":
            return jsonify(error="Bad request"), 400
        return fn(*a, **kw)
    return wrapper


@app.post("/api/login")
def login():
    ip = request.remote_addr
    if _fails.get(ip, 0) >= 10:
        return jsonify(error="Too many attempts, restart the container to reset"), 429
    d = request.get_json(silent=True) or {}
    ok = hmac.compare_digest(str(d.get("username", "")), ADMIN_USER) & \
        hmac.compare_digest(str(d.get("password", "")), ADMIN_PW)
    if not ok:
        _fails[ip] = _fails.get(ip, 0) + 1
        return jsonify(error="Wrong username or password"), 401
    _fails.pop(ip, None)
    session["auth"] = True
    session.permanent = True
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    return jsonify(auth=bool(session.get("auth")), samba=SAMBA_ENABLED)


# ----------------------------------------------------------------- files ---
def safe_path(rel):
    rel = (rel or "").lstrip("/")
    p = (DATA_DIR / rel).resolve()
    if p != DATA_DIR and DATA_DIR not in p.parents:
        raise ValueError("Path outside data directory")
    return p


def rel_of(p):
    return "" if p == DATA_DIR else str(p.relative_to(DATA_DIR))


def chown(p):
    try:
        os.chown(p, PUID, PGID)
    except OSError:
        pass


def chown_tree(p):
    chown(p)
    if p.is_dir() and not p.is_symlink():
        for r, ds, fs in os.walk(p):
            for n in ds + fs:
                chown(Path(r) / n)


@app.errorhandler(ValueError)
def bad_value(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(OSError)
def os_err(e):
    return jsonify(error=e.strerror or str(e)), 400


@app.get("/api/files")
@login_required
def list_files():
    p = safe_path(request.args.get("path"))
    if not p.is_dir():
        raise ValueError("Not a folder")
    items = []
    for e in os.scandir(p):
        try:
            st = e.stat()
        except OSError:
            continue
        items.append({"name": e.name, "dir": e.is_dir(),
                      "size": st.st_size, "mtime": int(st.st_mtime)})
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    usage = shutil.disk_usage(DATA_DIR)
    return jsonify(path=rel_of(p), items=items,
                   disk={"total": usage.total, "free": usage.free})


@app.get("/api/download")
@login_required
def download():
    p = safe_path(request.args.get("path"))
    if not p.is_file():
        raise ValueError("Not a file")
    inline = request.args.get("inline") == "1"
    return send_file(p, as_attachment=not inline, download_name=p.name)


@app.post("/api/mkdir")
@login_required
def mkdir():
    d = request.get_json(force=True)
    name = d.get("name", "").strip()
    if not name or "/" in name or name in (".", ".."):
        raise ValueError("Invalid folder name")
    p = safe_path(d.get("path")) / name
    p.mkdir()
    chown(p)
    return jsonify(ok=True)


@app.post("/api/rename")
@login_required
def rename():
    d = request.get_json(force=True)
    name = d.get("name", "").strip()
    if not name or "/" in name or name in (".", ".."):
        raise ValueError("Invalid name")
    src = safe_path(d.get("path"))
    if src == DATA_DIR:
        raise ValueError("Cannot rename root")
    dst = src.with_name(name)
    if dst.exists():
        raise ValueError("Name already exists")
    src.rename(dst)
    return jsonify(ok=True)


@app.post("/api/move")
@login_required
def move():
    d = request.get_json(force=True)
    dest = safe_path(d.get("dest"))
    if not dest.is_dir():
        raise ValueError("Destination is not a folder")
    for rel in d.get("paths", []):
        src = safe_path(rel)
        if src == DATA_DIR or src == dest or src in dest.parents:
            raise ValueError("Cannot move into itself")
        target = dest / src.name
        if target.exists():
            raise ValueError(f"{src.name} already exists there")
        shutil.move(str(src), str(target))
    return jsonify(ok=True)


@app.post("/api/delete")
@login_required
def delete():
    d = request.get_json(force=True)
    for rel in d.get("paths", []):
        p = safe_path(rel)
        if p == DATA_DIR:
            raise ValueError("Cannot delete root")
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()
    return jsonify(ok=True)


@app.post("/api/upload")
@login_required
def upload():
    dest = safe_path(request.args.get("path"))
    if not dest.is_dir():
        raise ValueError("Not a folder")
    saved = []
    for f in request.files.getlist("files"):
        name = os.path.basename(f.filename or "")
        if not name or name in (".", ".."):
            continue
        target = dest / name
        n = 1
        while target.exists():
            stem, suf = os.path.splitext(name)
            target = dest / f"{stem} ({n}){suf}"
            n += 1
        f.save(target)
        chown(target)
        saved.append(target.name)
    return jsonify(saved=saved)


# ----------------------------------------------------------------- samba ---
def validate_share(s):
    if not NAME_RE.match(s.get("name", "")):
        raise ValueError("Share name: letters, numbers, . _ - only (max 40)")
    if s["name"].lower() in ("global", "homes", "printers", "ipc$"):
        raise ValueError(f"'{s['name']}' is a reserved share name")
    raw = str(s.get("path", "")).strip()
    if raw == str(DATA_DIR) or raw.startswith(str(DATA_DIR) + "/"):
        raw = raw[len(str(DATA_DIR)):]
    path = safe_path(raw)
    path.mkdir(parents=True, exist_ok=True)
    chown(path)
    return {
        "name": s["name"],
        "path": str(path),
        "comment": re.sub(r"[\r\n]", " ", str(s.get("comment", "")))[:120],
        "read_only": bool(s.get("read_only")),
        "guest_ok": bool(s.get("guest_ok")),
        "browseable": bool(s.get("browseable", True)),
        "valid_users": [u for u in s.get("valid_users", []) if USER_RE.match(u)],
        "write_list": [u for u in s.get("write_list", []) if USER_RE.match(u)],
    }


@app.get("/api/samba")
@login_required
def samba_get():
    cfg = load_config()
    return jsonify(global_=cfg["global"], shares=cfg["shares"],
                   users=cfg["users"], data_dir=str(DATA_DIR), enabled=SAMBA_ENABLED)


@app.put("/api/samba")
@login_required
def samba_put():
    d = request.get_json(force=True)
    with lock:
        cfg = load_config()
        g = d.get("global", {})
        if not WG_RE.match(g.get("workgroup", "")):
            raise ValueError("Workgroup: letters, numbers, - _ (max 15)")
        if g.get("min_protocol") not in ("SMB2", "SMB3", "NT1"):
            raise ValueError("Invalid minimum protocol")
        cfg["global"] = {
            "workgroup": g["workgroup"].upper(),
            "server_string": re.sub(r"[\r\n]", " ", g.get("server_string", ""))[:60],
            "min_protocol": g["min_protocol"],
            "guest_map": bool(g.get("guest_map", True)),
        }
        shares = [validate_share(s) for s in d.get("shares", [])]
        names = [s["name"].lower() for s in shares]
        if len(names) != len(set(names)):
            raise ValueError("Share names must be unique")
        cfg["shares"] = shares
        apply_samba(cfg)
        save_config(cfg)
    return jsonify(ok=True)


@app.post("/api/samba/users")
@login_required
def user_add():
    d = request.get_json(force=True)
    name, pw = d.get("name", ""), d.get("password", "")
    if not USER_RE.match(name):
        raise ValueError("Username: lowercase letters, numbers, - _ (start with a letter)")
    if len(pw) < 6:
        raise ValueError("Password must be at least 6 characters")
    with lock:
        cfg = load_config()
        if name in cfg["users"]:
            raise ValueError("User already exists")
        if SAMBA_ENABLED:
            ensure_system_user(name)
            r = run(["smbpasswd", "-a", "-s", name], input_text=f"{pw}\n{pw}\n", check=False)
            if r.returncode != 0:
                raise ValueError("smbpasswd failed: " + r.stderr[-200:])
        cfg["users"].append(name)
        save_config(cfg)
    return jsonify(ok=True)


@app.post("/api/samba/users/<name>/password")
@login_required
def user_pw(name):
    pw = (request.get_json(force=True) or {}).get("password", "")
    if len(pw) < 6:
        raise ValueError("Password must be at least 6 characters")
    cfg = load_config()
    if name not in cfg["users"]:
        raise ValueError("No such user")
    if SAMBA_ENABLED:
        r = run(["smbpasswd", "-s", name], input_text=f"{pw}\n{pw}\n", check=False)
        if r.returncode != 0:
            raise ValueError("smbpasswd failed: " + r.stderr[-200:])
    return jsonify(ok=True)


@app.delete("/api/samba/users/<name>")
@login_required
def user_del(name):
    with lock:
        cfg = load_config()
        if name not in cfg["users"]:
            raise ValueError("No such user")
        if SAMBA_ENABLED:
            run(["smbpasswd", "-x", name], check=False)
            run(["userdel", name], check=False)
        cfg["users"].remove(name)
        for s in cfg["shares"]:
            s["valid_users"] = [u for u in s["valid_users"] if u != name]
            s["write_list"] = [u for u in s["write_list"] if u != name]
        apply_samba(cfg)
        save_config(cfg)
    return jsonify(ok=True)


@app.get("/api/samba/status")
@login_required
def samba_status():
    if not SAMBA_ENABLED:
        return jsonify(running=False, connections="Samba disabled")
    running = run(["pgrep", "-x", "smbd"], check=False).returncode == 0
    r = run(["smbstatus", "-b"], check=False)
    return jsonify(running=running, connections=(r.stdout or r.stderr).strip())


# ---------------------------------------------------------------- static ---
STATIC = Path(__file__).parent / "static"


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


# --------------------------------------------------------------- startup ---
def restore():
    """Called by entrypoint: recreate system users and smb.conf from config."""
    cfg = load_config()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SAMBA_ENABLED:
        for u in cfg["users"]:
            ensure_system_user(u)
        SMB_CONF.write_text(render_smb_conf(cfg))
        for s in cfg["shares"]:
            Path(s["path"]).mkdir(parents=True, exist_ok=True)
    print("[mobile-files] config restored", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        restore()
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
