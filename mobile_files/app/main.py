"""mobile-files: standalone web UI for file management + Samba settings.

Single Flask app. Serves a static SPA and a JSON API. Settings live in
$CONFIG_DIR/config.json; Samba's smb.conf is rendered from it on every change.
The same code runs as the plain Docker image and as the Home Assistant add-on.
"""
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, jsonify, request, send_file,
                   send_from_directory, session, stream_with_context)

VERSION = "2.0.0"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")).resolve()  # root of what the UI can see ("/" = everything)
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config")).resolve()
SMB_CONF = Path(os.environ.get("SMB_CONF", "/etc/samba/smb.conf"))
CONFIG_FILE = CONFIG_DIR / "config.json"
SECRET_FILE = CONFIG_DIR / "secret.key"
PUID = int(os.environ.get("PUID", "1000"))
PGID = int(os.environ.get("PGID", "1000"))
SAMBA_ENABLED = os.environ.get("SAMBA_ENABLED", "1") == "1"
SYSTEM_USER = "files"
_trash_env = os.environ.get("TRASH_DIR", str(DATA_DIR / ".mobile_files_trash")).strip()
TRASH_DIR = Path(_trash_env).resolve() if _trash_env else None


def _env_list(json_name, plain_name):
    """List from a JSON env var (add-on) or a comma/newline separated one (Docker)."""
    raw = os.environ.get(json_name, "")
    if raw.strip():
        try:
            v = json.loads(raw)
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()]
        except ValueError:
            pass
    return [x.strip() for x in re.split(r"[,\n]", os.environ.get(plain_name, "")) if x.strip()]


HIDE_WEB = _env_list("HIDE_WEB_JSON", "HIDE_FROM_WEB")        # hidden AND blocked in the web UI
HIDE_SAMBA = _env_list("HIDE_SAMBA_JSON", "HIDE_FROM_SAMBA")  # hidden in Samba listings (names only)
MAX_EDIT_BYTES = 5 * 1024 * 1024
SEARCH_LIMIT = 300

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")
WG_RE = re.compile(r"^[A-Za-z0-9_-]{1,15}$")
ARCHIVE_RE = re.compile(r"\.(zip|tar|tgz|tar\.gz|tar\.bz2|tbz2|tar\.xz|txz)$", re.I)

DEFAULT_GLOBAL = {
    "workgroup": "WORKGROUP",
    "server_string": "Mobile Files",
    "min_protocol": "SMB2",
    "guest_map": True,
    "recycle": True,       # Samba deletes go to the same Trash as the web UI
    "apple": False,        # vfs_fruit tweaks for macOS / iOS Files
}
DEFAULT_SETTINGS = {"trash": True, "trash_days": 30}
DEFAULT_CONFIG = {
    "global": DEFAULT_GLOBAL,
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
    "favorites": [],
    "settings": DEFAULT_SETTINGS,
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
    # fill in keys added by newer versions
    cfg["global"] = {**DEFAULT_GLOBAL, **cfg.get("global", {})}
    cfg["settings"] = {**DEFAULT_SETTINGS, **cfg.get("settings", {})}
    cfg.setdefault("favorites", [])
    cfg.setdefault("users", [])
    cfg.setdefault("shares", [])
    return cfg


def save_config(cfg):
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(CONFIG_FILE)


def trash_on(cfg=None):
    return bool(TRASH_DIR) and (cfg or load_config())["settings"].get("trash", True)


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
    names = []
    for e in HIDE_SAMBA:
        n = e.rstrip("/").split("/")[-1].replace("\n", " ").strip()
        if n:
            names.append(n)
    if TRASH_DIR:
        names.append(TRASH_DIR.name)
    if names:
        lines.append("   hide files = /" + "/".join(names) + "/")
    vfs = []
    recycle = g.get("recycle", True) and trash_on(cfg)
    if recycle:
        vfs.append("recycle")
    if g.get("apple"):
        vfs += ["catia", "fruit", "streams_xattr"]
        lines += ["   fruit:metadata = stream", "   fruit:model = MacSamba",
                  "   fruit:posix_rename = yes", "   fruit:veto_appledouble = no",
                  "   fruit:wipe_intentionally_left_blank_rfork = yes",
                  "   fruit:delete_empty_adfiles = yes"]
    if vfs:
        lines.append(f"   vfs objects = {' '.join(vfs)}")
    if recycle:
        lines += [f"   recycle:repository = {TRASH_DIR / 'samba' / '%S'}",
                  "   recycle:keeptree = yes", "   recycle:versions = yes",
                  "   recycle:touch_mtime = yes", "   recycle:directory_mode = 0775",
                  "   recycle:exclude = *.tmp,~$*,.~lock*,.DS_Store,Thumbs.db",
                  "   recycle:exclude_dir = /tmp,/temp,/cache"]
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
    prepare_trash()
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
        nologin = "/usr/sbin/nologin" if Path("/usr/sbin/nologin").exists() else "/sbin/nologin"
        run(["useradd", "-M", "-N", "-g", SYSTEM_USER, "-s", nologin, name])


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
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  MAX_CONTENT_LENGTH=None)
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PW = admin_password()
_fails = {}


INGRESS_IP = "172.30.32.2"  # Home Assistant supervisor proxy; HA already authenticated the user


def is_ingress():
    return request.remote_addr == INGRESS_IP


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not (session.get("auth") or is_ingress()):
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
    auth = bool(session.get("auth")) or is_ingress()
    out = dict(auth=auth, samba=SAMBA_ENABLED, ingress=is_ingress(), version=VERSION)
    if auth:
        cfg = load_config()
        out.update(root=str(DATA_DIR), hidden=len(HIDE_RULES), favorites=cfg["favorites"],
                   settings=cfg["settings"], trash_available=bool(TRASH_DIR))
    return jsonify(out)


# ----------------------------------------------------------------- files ---
def _glob_regex(g):
    out, i = [], 0
    while i < len(g):
        if g.startswith("**", i):
            out.append(".*"); i += 2; continue
        c = g[i]
        out.append("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c))
        i += 1
    return "".join(out)


def _compile_hide(entries):
    """Entry forms: /full/path, bare name (matched anywhere), or wildcards (* ? **)."""
    rules = []
    for e in entries:
        if "/" in e:
            e = "/" + e.strip("/")
            if e == "/":
                continue  # would hide everything
            rx = "^" + (_glob_regex(e) if ("*" in e or "?" in e) else re.escape(e)) + "(/.*)?$"
        else:
            rx = "(^|/)" + _glob_regex(e) + "(/.*)?$"
        rules.append(re.compile(rx))
    return rules


HIDE_RULES = _compile_hide(HIDE_WEB)


def in_trash(p):
    return bool(TRASH_DIR) and (p == TRASH_DIR or TRASH_DIR in p.parents)


def is_hidden(p):
    """Match full paths as written, and also paths relative to the root folder."""
    p = Path(p)
    if in_trash(p):
        return True
    if not HIDE_RULES:
        return False
    cands = [str(p)]
    try:
        cands.append("/" + str(p.relative_to(DATA_DIR)))
    except ValueError:
        pass
    return any(r.search(c) for r in HIDE_RULES for c in cands)


def safe_path(rel):
    rel = (rel or "").lstrip("/")
    raw = Path(os.path.normpath(DATA_DIR / rel))
    # Resolve the parent only, so a symlink itself can be renamed/deleted
    # without touching whatever it points to.
    p = raw if raw == DATA_DIR else raw.parent.resolve() / raw.name
    if p != DATA_DIR and DATA_DIR not in p.parents:
        raise ValueError("Path outside root directory")
    if is_hidden(raw) or is_hidden(p) or is_hidden(p.resolve()):
        raise ValueError("Not found")
    return p


def rel_of(p):
    return "" if p == DATA_DIR else str(p.relative_to(DATA_DIR))


def chown(p):
    try:
        os.chown(p, PUID, PGID, follow_symlinks=False)
    except OSError:
        pass


def chown_tree(p):
    chown(p)
    if p.is_dir() and not p.is_symlink():
        for r, ds, fs in os.walk(p):
            for n in ds + fs:
                chown(Path(r) / n)


def unique(dest, name):
    """dest/name, or dest/'name (1).ext' etc. if taken."""
    target = dest / name
    stem, suf = os.path.splitext(name)
    if name.lower().endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        stem, suf = name[:-7], name[-7:]
    n = 1
    while target.exists() or target.is_symlink():
        target = dest / f"{stem} ({n}){suf}"
        n += 1
    return target


def entry(p, name=None):
    st = p.lstat() if p.is_symlink() else p.stat()
    is_dir = p.is_dir()
    return {"name": name or p.name, "path": rel_of(p), "dir": is_dir,
            "size": 0 if is_dir else st.st_size, "mtime": int(st.st_mtime),
            "link": p.is_symlink()}


@app.errorhandler(ValueError)
def bad_value(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(OSError)
def os_err(e):
    return jsonify(error=e.strerror or str(e)), 400


def _force_remove(func, path, _exc):
    """rmtree error handler: clear read-only bits and retry once."""
    os.chmod(path, 0o777)
    func(path)


def remove_now(p):
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, onerror=_force_remove)
    else:
        try:
            p.unlink()
        except PermissionError:
            os.chmod(p, 0o666)
            p.unlink()


@app.get("/api/files")
@login_required
def list_files():
    p = safe_path(request.args.get("path"))
    if not p.is_dir():
        raise ValueError("Not a folder")
    items = []
    for e in os.scandir(p):
        ep = Path(e.path)
        if is_hidden(ep):
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        items.append({"name": e.name, "path": rel_of(ep), "dir": e.is_dir(),
                      "size": st.st_size, "mtime": int(st.st_mtime),
                      "link": e.is_symlink()})
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    usage = shutil.disk_usage(p)
    return jsonify(path=rel_of(p), items=items,
                   disk={"total": usage.total, "free": usage.free})


@app.get("/api/search")
@login_required
def search():
    base = safe_path(request.args.get("path"))
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify(items=[], truncated=False)
    pat = re.compile(_glob_regex(q) + "$") if ("*" in q or "?" in q) else None
    items, deadline, truncated = [], time.time() + 10, False
    for root, dirs, files in os.walk(base):
        rp = Path(root)
        dirs[:] = [d for d in dirs if not is_hidden(rp / d)]
        for n in dirs + files:
            ln = n.lower()
            if (pat.match(ln) if pat else q in ln):
                p = rp / n
                if is_hidden(p):
                    continue
                try:
                    items.append(entry(p))
                except OSError:
                    continue
                if len(items) >= SEARCH_LIMIT:
                    truncated = True
                    break
        if truncated or time.time() > deadline:
            truncated = True
            break
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    return jsonify(items=items, truncated=truncated)


@app.get("/api/info")
@login_required
def info():
    p = safe_path(request.args.get("path"))
    st = p.lstat()
    out = {"name": p.name or "/", "path": rel_of(p), "full": str(p), "dir": p.is_dir(),
           "mtime": int(st.st_mtime), "mode": stat.filemode(st.st_mode),
           "uid": st.st_uid, "gid": st.st_gid, "link": os.readlink(p) if p.is_symlink() else None}
    if p.is_dir() and not p.is_symlink():
        size = files = dirs = 0
        deadline, partial = time.time() + 8, False
        for root, ds, fs in os.walk(p):
            dirs += len(ds)
            for n in fs:
                try:
                    size += os.lstat(os.path.join(root, n)).st_size
                    files += 1
                except OSError:
                    pass
            if time.time() > deadline:
                partial = True
                break
        out.update(size=size, files=files, dirs=dirs, partial=partial)
    else:
        out.update(size=st.st_size)
    return jsonify(out)


@app.get("/api/download")
@login_required
def download():
    p = safe_path(request.args.get("path"))
    if not p.is_file():
        raise ValueError("Not a file")
    inline = request.args.get("inline") == "1"
    return send_file(p, as_attachment=not inline, download_name=p.name, conditional=True)


class _ZipStream:
    """Write-only buffer that lets zipfile stream without seeking."""
    def __init__(self):
        self.buf = bytearray()

    def write(self, b):
        self.buf += b
        return len(b)

    def flush(self):
        pass

    def pop(self):
        b = bytes(self.buf)
        self.buf.clear()
        return b


def _zip_members(paths):
    """Yield (filesystem path, archive name) for every file/folder under paths."""
    for p in paths:
        base = p.parent
        if p.is_dir() and not p.is_symlink():
            yield p, str(p.relative_to(base)) + "/"
            for root, ds, fs in os.walk(p):
                rp = Path(root)
                ds[:] = [d for d in ds if not is_hidden(rp / d)]
                for d in ds:
                    yield rp / d, str((rp / d).relative_to(base)) + "/"
                for n in fs:
                    fp = rp / n
                    if not is_hidden(fp) and fp.is_file():
                        yield fp, str(fp.relative_to(base))
        elif p.is_file():
            yield p, p.name


def _write_zip(zf, paths, on_chunk=None):
    for fp, arc in _zip_members(paths):
        if arc.endswith("/"):
            zf.writestr(zipfile.ZipInfo.from_file(fp, arc), b"")
            continue
        zi = zipfile.ZipInfo.from_file(fp, arc)
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi._compresslevel = zi.compress_level = 1   # fast on a Pi; media is already compressed
        with fp.open("rb") as src, zf.open(zi, "w", force_zip64=True) as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
                if on_chunk:
                    yield from on_chunk()
        if on_chunk:
            yield from on_chunk()


@app.get("/api/zip")
@login_required
def zip_download():
    paths = [safe_path(r) for r in request.args.getlist("paths")]
    if not paths:
        raise ValueError("Nothing selected")
    name = (paths[0].name if len(paths) == 1 else (paths[0].parent.name or "files")) + ".zip"

    def gen():
        s = _ZipStream()
        with zipfile.ZipFile(s, "w") as zf:
            yield from _write_zip(zf, paths, on_chunk=lambda: [s.pop()])
        yield s.pop()

    return Response(stream_with_context(gen()), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/compress")
@login_required
def compress():
    d = request.get_json(force=True)
    paths = [safe_path(r) for r in d.get("paths", [])]
    if not paths:
        raise ValueError("Nothing selected")
    dest = paths[0].parent
    name = (d.get("name") or "").strip() or (paths[0].name if len(paths) == 1 else "Archive")
    if "/" in name or name in (".", ".."):
        raise ValueError("Invalid name")
    if not name.lower().endswith(".zip"):
        name += ".zip"
    target = unique(dest, name)
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
            for fp, arc in _zip_members(paths):
                zf.write(fp, arc)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    chown(target)
    return jsonify(ok=True, name=target.name)


@app.post("/api/extract")
@login_required
def extract():
    d = request.get_json(force=True)
    src = safe_path(d.get("path"))
    if not src.is_file() or not ARCHIVE_RE.search(src.name):
        raise ValueError("Not a supported archive (.zip, .tar, .tar.gz, .tar.xz, .tar.bz2)")
    stem = ARCHIVE_RE.sub("", src.name) or "extracted"
    dest = unique(src.parent, stem)
    dest.mkdir()
    try:
        if src.name.lower().endswith(".zip"):
            with zipfile.ZipFile(src) as zf:
                for m in zf.infolist():
                    out = (dest / m.filename).resolve()
                    if dest.resolve() not in out.parents and out != dest.resolve():
                        raise ValueError(f"Unsafe path in archive: {m.filename}")
                zf.extractall(dest)
        else:
            with tarfile.open(src) as tf:
                tf.extractall(dest, filter="data")
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    chown_tree(dest)
    return jsonify(ok=True, name=dest.name)


@app.get("/api/read")
@login_required
def read_text():
    p = safe_path(request.args.get("path"))
    if not p.is_file():
        raise ValueError("Not a file")
    size = p.stat().st_size
    if size > MAX_EDIT_BYTES:
        raise ValueError("File too large to edit here (max 5 MB)")
    data = p.read_bytes()
    if b"\0" in data:
        raise ValueError("Binary file - download it instead")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return jsonify(content=text, size=size, mtime=p.stat().st_mtime)


@app.put("/api/write")
@login_required
def write_text():
    d = request.get_json(force=True)
    p = safe_path(d.get("path"))
    if p.exists() and not p.is_file():
        raise ValueError("Not a file")
    content = d.get("content", "")
    if not isinstance(content, str):
        raise ValueError("Bad content")
    existed = p.exists()
    if d.get("create"):
        name = p.name
        if existed:
            raise ValueError("A file with that name already exists")
        if not name or name in (".", ".."):
            raise ValueError("Invalid file name")
    elif existed and d.get("mtime") and abs(p.stat().st_mtime - float(d["mtime"])) > 0.001 \
            and not d.get("force"):
        return jsonify(error="File changed on disk since you opened it", conflict=True), 409
    with p.open("wb") as f:          # in place: keeps owner and permissions
        f.write(content.encode("utf-8"))
    if not existed:
        chown(p)
    return jsonify(ok=True, mtime=p.stat().st_mtime)


@app.post("/api/mkdir")
@login_required
def mkdir():
    d = request.get_json(force=True)
    name = d.get("name", "").strip()
    if not name or "/" in name or name in (".", ".."):
        raise ValueError("Invalid folder name")
    p = safe_path(str(Path(d.get("path") or "") / name))
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
    _fix_favorites(rel_of(src), rel_of(dst))
    return jsonify(ok=True)


def _fix_favorites(old, new):
    with lock:
        cfg = load_config()
        fav, changed = [], False
        for f in cfg["favorites"]:
            if f == old or f.startswith(old + "/"):
                changed = True
                if new is None:
                    continue
                f = new + f[len(old):]
            fav.append(f)
        if changed:
            cfg["favorites"] = fav
            save_config(cfg)


def _check_dest(paths, dest):
    if not dest.is_dir():
        raise ValueError("Destination is not a folder")
    for src in paths:
        if src == DATA_DIR or src == dest or src in dest.parents:
            raise ValueError("Cannot put a folder inside itself")


@app.post("/api/move")
@login_required
def move():
    d = request.get_json(force=True)
    dest = safe_path(d.get("dest"))
    paths = [safe_path(r) for r in d.get("paths", [])]
    _check_dest(paths, dest)
    for src in paths:
        if src.parent == dest:
            continue
        target = dest / src.name
        if target.exists():
            raise ValueError(f"{src.name} already exists there")
        shutil.move(str(src), str(target))
        _fix_favorites(rel_of(src), rel_of(target))
    return jsonify(ok=True)


@app.post("/api/copy")
@login_required
def copy():
    d = request.get_json(force=True)
    dest = safe_path(d.get("dest"))
    paths = [safe_path(r) for r in d.get("paths", [])]
    _check_dest(paths, dest)
    for src in paths:
        target = unique(dest, src.name)
        if src.is_dir() and not src.is_symlink():
            shutil.copytree(src, target, symlinks=True)
        else:
            shutil.copy2(src, target, follow_symlinks=False)
        chown_tree(target)
    return jsonify(ok=True)


# ----------------------------------------------------------------- trash ---
def prepare_trash():
    if not TRASH_DIR:
        return
    for sub in ("", "items", "samba"):
        (TRASH_DIR / sub).mkdir(parents=True, exist_ok=True)
        chown(TRASH_DIR / sub)
        try:
            os.chmod(TRASH_DIR / sub, 0o775)
        except OSError:
            pass


def to_trash(p):
    prepare_trash()
    tid = f"{int(time.time() * 1000)}-{secrets.token_hex(3)}"
    box = TRASH_DIR / "items" / tid
    box.mkdir()
    meta = {"name": p.name, "orig": rel_of(p), "deleted": int(time.time()),
            "dir": p.is_dir() and not p.is_symlink()}
    try:
        shutil.move(str(p), str(box / p.name))
    except Exception:
        shutil.rmtree(box, ignore_errors=True)
        raise
    (TRASH_DIR / "items" / f"{tid}.json").write_text(json.dumps(meta))


def _dir_size(p, limit=2.0):
    if not p.is_dir() or p.is_symlink():
        try:
            return p.lstat().st_size
        except OSError:
            return 0
    total, deadline = 0, time.time() + limit
    for root, _ds, fs in os.walk(p):
        for n in fs:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
        if time.time() > deadline:
            break
    return total


def trash_items():
    if not TRASH_DIR:
        return []
    items = []
    idir = TRASH_DIR / "items"
    if idir.is_dir():
        for mf in idir.glob("*.json"):
            try:
                meta = json.loads(mf.read_text())
            except (OSError, ValueError):
                continue
            obj = idir / mf.stem / meta.get("name", "")
            if not obj.exists() and not obj.is_symlink():
                continue
            items.append({"id": mf.stem, "name": meta["name"], "orig": meta["orig"],
                          "deleted": meta["deleted"], "dir": meta.get("dir", False),
                          "size": _dir_size(obj), "source": "web"})
    sdir = TRASH_DIR / "samba"
    if sdir.is_dir():
        for share in sdir.iterdir():
            if not share.is_dir():
                continue
            for root, _ds, fs in os.walk(share):
                for n in fs:
                    fp = Path(root) / n
                    try:
                        st = fp.lstat()
                    except OSError:
                        continue
                    rel = str(fp.relative_to(share))
                    items.append({"id": "samba:" + str(fp.relative_to(sdir)), "name": n,
                                  "orig": f"{share.name}: {rel}", "deleted": int(st.st_mtime),
                                  "dir": False, "size": st.st_size, "source": "samba"})
                    if len(items) > 2000:
                        break
    items.sort(key=lambda i: -i["deleted"])
    return items


def _trash_obj(tid):
    """Return (object path, metadata path or None, restore target)."""
    if tid.startswith("samba:"):
        rel = Path(os.path.normpath(tid[6:]))
        sdir = TRASH_DIR / "samba"
        fp = (sdir / rel)
        if ".." in rel.parts or sdir not in fp.parents:
            raise ValueError("Bad trash id")
        share_name, sub = rel.parts[0], Path(*rel.parts[1:])
        share = next((s for s in load_config()["shares"] if s["name"] == share_name), None)
        target = Path(share["path"]) / sub if share else None
        return fp, None, target
    if not re.match(r"^[0-9]+-[0-9a-f]{6}$", tid):
        raise ValueError("Bad trash id")
    mf = TRASH_DIR / "items" / f"{tid}.json"
    meta = json.loads(mf.read_text())
    return TRASH_DIR / "items" / tid / meta["name"], mf, DATA_DIR / meta["orig"]


def _cleanup_empty(start, stop):
    d = start
    while d != stop and stop in d.parents:
        try:
            d.rmdir()
        except OSError:
            break
        d = d.parent


@app.get("/api/trash")
@login_required
def trash_list():
    cfg = load_config()
    return jsonify(available=bool(TRASH_DIR), enabled=trash_on(cfg),
                   days=cfg["settings"]["trash_days"], path=str(TRASH_DIR or ""),
                   items=trash_items())


@app.post("/api/trash/restore")
@login_required
def trash_restore():
    if not TRASH_DIR:
        raise ValueError("Trash is not available")
    restored = []
    for tid in request.get_json(force=True).get("ids", []):
        obj, mf, target = _trash_obj(tid)
        if target is None:
            raise ValueError("That share no longer exists")
        target = Path(os.path.normpath(target))
        if target != DATA_DIR and DATA_DIR not in target.parents:
            raise ValueError("Original location is outside the root folder")
        target.parent.mkdir(parents=True, exist_ok=True)
        target = unique(target.parent, target.name)
        shutil.move(str(obj), str(target))
        if mf:
            mf.unlink(missing_ok=True)
            shutil.rmtree(obj.parent, ignore_errors=True)
        else:
            _cleanup_empty(obj.parent, TRASH_DIR / "samba")
        restored.append(rel_of(target))
    return jsonify(ok=True, restored=restored)


@app.post("/api/trash/purge")
@login_required
def trash_purge():
    if not TRASH_DIR:
        raise ValueError("Trash is not available")
    d = request.get_json(force=True)
    if d.get("all"):
        for sub in ("items", "samba"):
            p = TRASH_DIR / sub
            if p.exists():
                shutil.rmtree(p, onerror=_force_remove)
        prepare_trash()
        return jsonify(ok=True)
    for tid in d.get("ids", []):
        obj, mf, _ = _trash_obj(tid)
        if mf:
            shutil.rmtree(obj.parent, onerror=_force_remove)
            mf.unlink(missing_ok=True)
        else:
            remove_now(obj)
            _cleanup_empty(obj.parent, TRASH_DIR / "samba")
    return jsonify(ok=True)


def purge_old():
    """Delete trash older than trash_days (0 = keep forever)."""
    if not TRASH_DIR or not TRASH_DIR.exists():
        return
    days = int(load_config()["settings"].get("trash_days", 30) or 0)
    if days <= 0:
        return
    cutoff = time.time() - days * 86400
    for it in trash_items():
        if it["deleted"] < cutoff:
            try:
                obj, mf, _ = _trash_obj(it["id"])
                if mf:
                    shutil.rmtree(obj.parent, onerror=_force_remove)
                    mf.unlink(missing_ok=True)
                else:
                    remove_now(obj)
                    _cleanup_empty(obj.parent, TRASH_DIR / "samba")
            except Exception as e:  # noqa: BLE001 - keep the janitor alive
                print(f"[mobile-files] trash purge: {e}", flush=True)


def _janitor():
    while True:
        try:
            purge_old()
        except Exception as e:  # noqa: BLE001
            print(f"[mobile-files] trash purge failed: {e}", flush=True)
        time.sleep(3600)


@app.post("/api/delete")
@login_required
def delete():
    d = request.get_json(force=True)
    use_trash = trash_on() and not d.get("permanent")
    for rel in d.get("paths", []):
        p = safe_path(rel)
        if p == DATA_DIR:
            raise ValueError("Cannot delete root")
        if use_trash:
            to_trash(p)
        else:
            remove_now(p)
        _fix_favorites(rel_of(p), None)
    return jsonify(ok=True, trashed=use_trash)


@app.post("/api/upload")
@login_required
def upload():
    dest = safe_path(request.args.get("path"))
    if not dest.is_dir():
        raise ValueError("Not a folder")
    saved = []
    for f in request.files.getlist("files"):
        # keep sub-folders for folder uploads ("photos/2024/a.jpg"), never escape dest
        parts = [x for x in (f.filename or "").replace("\\", "/").split("/") if x not in ("", ".", "..")]
        if not parts:
            continue
        folder = dest
        for part in parts[:-1]:
            folder = folder / part
            if not folder.exists():
                folder.mkdir()
                chown(folder)
        if is_hidden(folder / parts[-1]):
            continue
        target = unique(folder, parts[-1])
        f.save(target)
        chown(target)
        saved.append(target.name)
    return jsonify(saved=saved)


# ------------------------------------------------------- favorites/settings ---
@app.put("/api/favorites")
@login_required
def favorites_put():
    favs = []
    for r in request.get_json(force=True).get("favorites", []):
        p = safe_path(r)
        if p.is_dir() and rel_of(p) not in favs:
            favs.append(rel_of(p))
    with lock:
        cfg = load_config()
        cfg["favorites"] = favs[:50]
        save_config(cfg)
    return jsonify(ok=True, favorites=cfg["favorites"])


@app.put("/api/settings")
@login_required
def settings_put():
    d = request.get_json(force=True)
    with lock:
        cfg = load_config()
        s = cfg["settings"]
        if "trash" in d:
            s["trash"] = bool(d["trash"])
        if "trash_days" in d:
            days = int(d["trash_days"])
            if not 0 <= days <= 3650:
                raise ValueError("Days must be 0-3650")
            s["trash_days"] = days
        save_config(cfg)
        if SAMBA_ENABLED:
            apply_samba(cfg)   # Samba recycle bin follows the trash switch
    return jsonify(ok=True, settings=s)


# ----------------------------------------------------------------- samba ---
def validate_share(s):
    if not NAME_RE.match(s.get("name", "")):
        raise ValueError("Share name: letters, numbers, . _ - only (max 40)")
    if s["name"].lower() in ("global", "homes", "printers", "ipc$"):
        raise ValueError(f"'{s['name']}' is a reserved share name")
    raw = str(s.get("path", "")).strip()
    if raw == str(DATA_DIR) or raw.startswith(str(DATA_DIR).rstrip("/") + "/"):
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
                   users=cfg["users"], data_dir=str(DATA_DIR), enabled=SAMBA_ENABLED,
                   trash=trash_on(cfg))


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
            "recycle": bool(g.get("recycle", True)),
            "apple": bool(g.get("apple", False)),
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


@app.get("/icon.png")
def icon():
    return send_from_directory(STATIC, "icon.png")


@app.get("/logo.png")
def logo():
    return send_from_directory(STATIC, "logo.png")


@app.get("/manifest.webmanifest")
def manifest():
    return jsonify(name="Mobile Files", short_name="Files", start_url=".", scope=".",
                   display="standalone", background_color="#0f1115", theme_color="#0f1115",
                   icons=[{"src": "logo.png", "sizes": "512x512", "type": "image/png"}])


@app.get("/")
def index():
    r = send_from_directory(STATIC, "index.html")
    r.headers["Cache-Control"] = "no-cache"
    return r


# --------------------------------------------------------------- startup ---
def restore():
    """Called by entrypoint: recreate system users and smb.conf from config."""
    cfg = load_config()
    save_config(cfg)  # writes any new default keys
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    prepare_trash()
    if SAMBA_ENABLED:
        for u in cfg["users"]:
            ensure_system_user(u)
        SMB_CONF.write_text(render_smb_conf(cfg))
        for s in cfg["shares"]:
            Path(s["path"]).mkdir(parents=True, exist_ok=True)
    print(f"[mobile-files] v{VERSION} config restored (trash: {TRASH_DIR or 'off'})", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        restore()
    else:
        threading.Thread(target=_janitor, daemon=True).start()
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
else:
    threading.Thread(target=_janitor, daemon=True).start()
