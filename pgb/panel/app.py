"""PG-Backup web panel (Starlette). Everything lives under a random secret path."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Mount, Route

from . import security as sec
from . import tg
from .jobs import JobRunner
from .store import Store

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE = "pgb_s"
SESSION_TTL = 8 * 3600
PANEL_VERSION = "5.0.0"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
BACKUP_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}\.zip$")
UNIT_BASE = "pasarguard-backup"
SESSION_BASE = "pasarguard_backup"
MAX_JSON = 256 * 1024
TOKEN_RE = re.compile(r"^\d{5,20}:[A-Za-z0-9_-]{20,80}$")
CHAT_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z0-9_]{4,64})$")
PROXY_RE = re.compile(r"^(socks5h?|socks4|https?)://([^\s/@:]+(:[^\s/@]*)?@)?[A-Za-z0-9_.\-\[\]:]+:\d{1,5}/?$")


class Config:
    def __init__(self, **kw: Any):
        e = os.environ
        self.state_dir = kw.get("state_dir") or e.get("PGB_STATE_DIR", "/var/lib/pg-backup-panel")
        self.cli = kw.get("cli") or (e.get("PGB_BACKUP_BIN", "/usr/local/bin/PG-Backup").split())
        self.creds_dir = kw.get("creds_dir") or e.get("PGB_CREDS_DIR", "/etc/pasarguard-backup")
        self.backup_dir = kw.get("backup_dir") or e.get("PGB_BACKUP_DIR", os.path.join(self.state_dir, "backups"))
        self.secure_cookie = kw.get("secure_cookie", e.get("PGB_SECURE_COOKIE", "auto"))
        self.use_systemctl = kw.get("use_systemctl", shutil.which("systemctl") is not None)
        self.unit_dir = kw.get("unit_dir") or e.get("PGB_UNIT_DIR", "/etc/systemd/system")


def _read(path: str) -> str:
    with open(path) as f:
        return f.read()


def jerr(msg: str, status: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": msg, **extra}, status_code=status)


SEC_HEADERS = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
               (b"x-frame-options", b"DENY"), (b"cross-origin-opener-policy", b"same-origin"),
               (b"cross-origin-resource-policy", b"same-origin"), (b"x-robots-tag", b"noindex, nofollow")]


class SecurityHeaders:
    """Pure-ASGI middleware (safe for SSE): baseline headers on every response and
    `Cache-Control: no-store` on API responses unless a route set its own."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def wrapped(msg):
            if msg["type"] == "http.response.start":
                hdrs = list(msg.get("headers", []))
                have = {k.lower() for k, _ in hdrs}
                for k, v in SEC_HEADERS:
                    if k not in have:
                        hdrs.append((k, v))
                if b"cache-control" not in have and "/api/" in scope.get("path", ""):
                    hdrs.append((b"cache-control", b"no-store"))
                msg = {**msg, "headers": hdrs}
            await send(msg)
        await self.app(scope, receive, wrapped)


def create_app(cfg: Optional[Config] = None) -> Starlette:
    cfg = cfg or Config()
    store = Store(cfg.state_dir)
    os.makedirs(cfg.backup_dir, mode=0o700, exist_ok=True)
    signer = sec.Signer(store.signing_key())
    limiter = sec.RateLimiter()
    revoked: Dict[str, float] = {}               # session id -> expiry (logout revocation)
    jobs = JobRunner(store)
    secret_path = store.get("secret_path")
    if not secret_path:
        secret_path = sec.random_path()
        store.set("secret_path", secret_path)

    # ------------------------------------------------------------------ helpers
    def client_ip(req: Request) -> str:
        return (req.client.host if req.client else "?")

    def is_https(req: Request) -> bool:
        if cfg.secure_cookie in ("1", "true", True):
            return True
        if cfg.secure_cookie in ("0", "false", False):
            return False
        return req.headers.get("x-forwarded-proto", req.url.scheme) == "https"

    def session(req: Request) -> Optional[Dict[str, Any]]:
        tok = req.cookies.get(COOKIE)
        data = signer.load(tok) if tok else None
        if not data or data.get("t") != "s":
            return None
        user = store.get("admin")
        if not user or data.get("u") != user["username"] or data.get("v") != user.get("sv", 0):
            return None
        if data.get("j") in revoked:
            return None
        return data

    def guarded(fn):
        async def wrap(req: Request):
            s = session(req)
            if not s:
                return jerr("authentication required", 401)
            if req.method not in ("GET", "HEAD") and req.headers.get("x-pgb") != "1":
                return jerr("missing CSRF header", 403)
            req.state.user = s["u"]
            return await fn(req)
        return wrap

    async def body(req: Request) -> Dict[str, Any]:
        """JSON body, capped at 256 KiB (SSH keys and option sets are far smaller)."""
        try:
            raw = b""
            async for chunk in req.stream():
                raw += chunk
                if len(raw) > MAX_JSON:
                    return {}
            d = json.loads(raw or b"{}")
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def set_cookie(resp: Response, req: Request, token: str, ttl: int) -> None:
        resp.set_cookie(COOKIE, token, max_age=ttl, httponly=True, samesite="strict", secure=is_https(req),
                        path="/" + secret_path)

    def totp_secret(admin: Dict[str, Any]) -> str:
        t = admin.get("totp")
        if t is True:
            return store.get_secret("totp") or ""
        s_ = str(t or "")                         # legacy: plaintext secret -> migrate to encrypted storage
        if s_:
            store.set_secret("totp", s_)
            admin["totp"] = True
            store.set("admin", admin)
        return s_

    def _build_id() -> str:
        import hashlib
        h_ = hashlib.sha256()
        for f_ in (os.path.abspath(__file__), os.path.join(STATIC_DIR, "app.js"), os.path.join(STATIC_DIR, "style.css")):
            try:
                with open(f_, "rb") as fh:
                    h_.update(fh.read())
            except OSError:
                pass
        return h_.hexdigest()[:7]
    BUILD_ID = _build_id()

    def run_cli(*args: str) -> List[str]:
        return list(cfg.cli) + ["--cli"] + list(args)

    # ------------------------------------------------------------------ auth
    async def login(req: Request):
        if req.headers.get("x-pgb") != "1":
            return jerr("missing CSRF header", 403)
        d = await body(req)
        username, password, otp = str(d.get("username", "")), str(d.get("password", "")), str(d.get("otp", ""))
        ip = client_ip(req)
        keys = (f"u:{username.lower()}", f"ip:{ip}")
        wait = max(limiter.retry_after(k) for k in keys)
        if wait:
            return jerr("too many attempts", 429, retry_after=wait)
        admin = store.get("admin")
        # constant-ish work even for unknown users
        good = bool(admin) and hmac.compare_digest(username.encode(), admin["username"].encode()) and sec.verify_password(password, admin["hash"])
        if not good:
            if not admin:
                sec.verify_password(password, "scrypt$32768$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
            for k in keys:
                limiter.fail(k)
            store.audit(username[:40], ip, "login_failed")
            return jerr("invalid username or password", 401)
        if admin.get("totp"):
            if not otp:
                return JSONResponse({"need_otp": True})
            step = sec.verify_totp(totp_secret(admin), otp, admin.get("last_step", 0))
            if step:
                admin["last_step"] = step
                store.set("admin", admin)
            else:
                left = sec.use_recovery_code(otp, admin.get("recovery", []))
                if left is None:
                    for k in keys:
                        limiter.fail(k)
                    store.audit(username, ip, "otp_failed")
                    return jerr("invalid verification code", 401)
                admin["recovery"] = left
                store.set("admin", admin)
                store.audit(username, ip, "recovery_code_used")
        for k in keys:
            limiter.ok(k)
        store.audit(username, ip, "login")
        resp = JSONResponse({"ok": True, "user": username})
        set_cookie(resp, req, signer.sign({"t": "s", "u": username, "v": admin.get("sv", 0), "j": secrets.token_hex(8)}, SESSION_TTL), SESSION_TTL)
        return resp

    async def logout(req: Request):
        s = session(req)
        if s and s.get("j"):
            now = time.time()
            for k in [k for k, exp in revoked.items() if exp < now]:
                revoked.pop(k, None)
            revoked[s["j"]] = now + SESSION_TTL + 60
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/" + secret_path)
        return resp

    @guarded
    async def me(req: Request):
        a = store.get("admin")
        return JSONResponse({"user": a["username"], "totp": bool(a.get("totp")),
                             "recovery_left": len(a.get("recovery", [])), "version": PANEL_VERSION, "build": BUILD_ID})

    @guarded
    async def change_password(req: Request):
        d = await body(req)
        a = store.get("admin")
        if not sec.verify_password(str(d.get("old", "")), a["hash"]):
            return jerr("current password is wrong", 403)
        bad = sec.check_password_strength(str(d.get("new", "")))
        if bad:
            return jerr(bad)
        a["hash"] = sec.hash_password(d["new"])
        a["sv"] = a.get("sv", 0) + 1            # invalidates every other session
        store.set("admin", a)
        store.audit(a["username"], client_ip(req), "password_changed")
        resp = JSONResponse({"ok": True})
        set_cookie(resp, req, signer.sign({"t": "s", "u": a["username"], "v": a["sv"], "j": secrets.token_hex(8)}, SESSION_TTL), SESSION_TTL)
        return resp

    @guarded
    async def totp_setup(req: Request):
        a = store.get("admin")
        if a.get("totp"):
            return jerr("2FA is already enabled")
        s = sec.new_totp_secret()
        store.set_secret("totp_pending", s)
        return JSONResponse({"secret": s, "uri": sec.totp_uri(s, a["username"])})

    @guarded
    async def totp_enable(req: Request):
        d = await body(req)
        s = store.get_secret("totp_pending")
        if not s:
            return jerr("start the setup first")
        step = sec.verify_totp(s, str(d.get("code", "")))
        if not step:
            return jerr("that code is not correct")
        plain, hashed = sec.new_recovery_codes()
        a = store.get("admin")
        store.set_secret("totp", s)               # encrypted at rest (Fernet), not in the admin record
        a.update(totp=True, last_step=step, recovery=hashed)
        store.set("admin", a)
        store.delete("secret:totp_pending")
        store.audit(a["username"], client_ip(req), "2fa_enabled")
        return JSONResponse({"ok": True, "recovery_codes": plain})

    @guarded
    async def totp_disable(req: Request):
        d = await body(req)
        a = store.get("admin")
        if not sec.verify_password(str(d.get("password", "")), a["hash"]):
            return jerr("password is wrong", 403)
        if not a.get("totp") or not sec.verify_totp(totp_secret(a), str(d.get("code", ""))):
            return jerr("verification code is wrong", 403)
        a.pop("totp", None)
        store.delete("secret:totp")
        a["recovery"] = []
        store.set("admin", a)
        store.audit(a["username"], client_ip(req), "2fa_disabled")
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------ dashboard
    def sysinfo() -> Dict[str, Any]:
        info: Dict[str, Any] = {}
        try:
            info["load"] = [round(x, 2) for x in os.getloadavg()]
        except OSError:
            info["load"] = []
        try:
            mem = {l.split(":")[0]: int(l.split()[1]) * 1024 for l in _read("/proc/meminfo").splitlines() if ":" in l}
            info["mem_total"], info["mem_avail"] = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
        except OSError:
            pass
        du = shutil.disk_usage(cfg.backup_dir)
        info["disk_total"], info["disk_free"] = du.total, du.free
        try:
            info["uptime"] = int(float(_read("/proc/uptime").split()[0]))
        except OSError:
            pass
        return info

    def list_backups() -> List[Dict[str, Any]]:
        out = []
        for n in os.listdir(cfg.backup_dir):
            p = os.path.join(cfg.backup_dir, n)
            if BACKUP_RE.match(n) and os.path.isfile(p):
                st = os.stat(p)
                out.append({"name": n, "size": st.st_size, "mtime": st.st_mtime})
        return sorted(out, key=lambda x: -x["mtime"])

    def unit_of(name: str) -> str:
        """systemd unit for a scheduler. The terminal menu names the instance after the unit
        itself (pasarguard-backup-1); schedulers created here may use a short name."""
        return name if (name == UNIT_BASE or name.startswith(UNIT_BASE + "-")) else f"{UNIT_BASE}-{name}"

    def unit_path(name: str) -> str:
        return os.path.join(cfg.unit_dir, unit_of(name) + ".service")

    def parse_legacy_unit(path: str) -> Optional[Dict[str, Any]]:
        """Schedulers made by PG-Backup < 4.2 keep token/chat in ExecStart (no credentials file)."""
        try:
            text = _read(path)
        except OSError:
            return None
        line = next((x.strip()[len("ExecStart="):] for x in text.splitlines() if x.strip().startswith("ExecStart=")), "")
        try:
            argv = shlex.split(line)
        except ValueError:
            return None
        if "--daemon-backup" not in argv:
            return None
        out: Dict[str, Any] = {"token": "", "chat": "", "proxy": "", "interval": 1.0, "node": "--node" in argv}
        for i, a_ in enumerate(argv[:-1]):
            if a_ in ("--token", "--chat", "--proxy"):
                out[a_[2:]] = argv[i + 1]
            elif a_ == "--interval":
                try:
                    out["interval"] = float(argv[i + 1])
                except ValueError:
                    pass
        return out

    def live_sessions() -> Dict[str, str]:
        found: Dict[str, str] = {}
        for cmd, kind in ((["screen", "-list"], "screen"), (["tmux", "list-sessions", "-F", "#{session_name}"], "tmux")):
            if not shutil.which(cmd[0]):
                continue
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                continue
            for ln in r.stdout.splitlines():
                m = re.search(r"(?:\d+\.)?(" + SESSION_BASE + r"[A-Za-z0-9_-]*)", ln)
                if m and NAME_RE.match(m.group(1)):
                    found[m.group(1)] = kind
        return found

    def schedulers() -> List[Dict[str, Any]]:
        """Every backup scheduler on this host, however it was created: credentials file (v4.2+),
        systemd unit (including pre-4.2 units with the token in ExecStart), screen or tmux session."""
        active: Dict[str, str] = {}
        if cfg.use_systemctl:
            try:
                r = subprocess.run(["systemctl", "list-units", "--all", "--type=service", "--no-legend", "--plain",
                                    f"{UNIT_BASE}-*.service"], capture_output=True, text=True, timeout=15)
                for ln in r.stdout.splitlines():
                    pp = ln.split()
                    if pp and pp[0].endswith(".service"):
                        active[pp[0][:-8]] = pp[2] if len(pp) > 2 else "?"
            except (OSError, subprocess.SubprocessError):
                pass
        sessions = live_sessions()
        recs: Dict[str, Dict[str, Any]] = {}

        def add(name: str, meta: Optional[Dict[str, Any]], legacy: bool = False) -> None:
            u = unit_of(name)
            if os.path.isfile(unit_path(name)):
                kind = "systemd"
            else:
                kind = sessions.get(name, "none")
            if kind == "systemd":
                st = active.get(u, "inactive")
            else:
                st = "active" if kind in ("screen", "tmux") else "inactive"
            tok = (meta or {}).get("token", "")
            recs[name] = {"name": name, "unit": u, "kind": kind, "state": st, "legacy": legacy, "managed": meta is not None and not legacy,
                          "interval": (meta or {}).get("interval"), "node": bool((meta or {}).get("node")),
                          "chat": (meta or {}).get("chat"), "token_hint": ("…" + tok[-4:]) if tok else "",
                          "proxy": bool((meta or {}).get("proxy"))}

        if os.path.isdir(cfg.creds_dir):
            for fn in sorted(os.listdir(cfg.creds_dir)):
                if fn.endswith(".json") and NAME_RE.match(fn[:-5]):
                    try:
                        add(fn[:-5], json.loads(_read(os.path.join(cfg.creds_dir, fn))))
                    except (OSError, ValueError):
                        continue
        known_units = {r["unit"] for r in recs.values()}
        if os.path.isdir(cfg.unit_dir):
            for fn in sorted(os.listdir(cfg.unit_dir)):
                if fn.startswith(UNIT_BASE + "-") and fn.endswith(".service") and fn[:-8] not in known_units:
                    name = fn[:-8]
                    if NAME_RE.match(name):
                        meta = parse_legacy_unit(os.path.join(cfg.unit_dir, fn))
                        add(name, meta, legacy=bool(meta and meta.get("token")))
        for name in sessions:
            if name not in recs:
                add(name, None)
        return sorted(recs.values(), key=lambda r: r["name"])

    @guarded
    async def dashboard(req: Request):
        b = list_backups()
        return JSONResponse({"system": sysinfo(), "backups": {"count": len(b), "size": sum(x["size"] for x in b),
                                                              "latest": b[0] if b else None},
                             "schedulers": schedulers(), "jobs": store.jobs(8),
                             "servers": len(store.get("servers", [])), "version": store.get("version", "")})

    # ------------------------------------------------------------------ backups
    @guarded
    async def backups_list(req: Request):
        return JSONResponse({"items": list_backups(), "dir": cfg.backup_dir})

    def safe_backup(name: str) -> Optional[str]:
        if not BACKUP_RE.match(name or ""):
            return None
        p = os.path.join(cfg.backup_dir, name)
        return p if os.path.isfile(p) else None

    @guarded
    async def backup_download(req: Request):
        p = safe_backup(req.path_params["name"])
        if not p:
            return jerr("not found", 404)
        store.audit(req.state.user, client_ip(req), "backup_download", req.path_params["name"])
        return FileResponse(p, media_type="application/zip", filename=os.path.basename(p))

    @guarded
    async def backup_delete(req: Request):
        p = safe_backup(req.path_params["name"])
        if not p:
            return jerr("not found", 404)
        os.remove(p)
        store.audit(req.state.user, client_ip(req), "backup_delete", req.path_params["name"])
        return JSONResponse({"ok": True})

    @guarded
    async def backup_upload(req: Request):
        name = os.path.basename(req.query_params.get("name", ""))
        if not BACKUP_RE.match(name):
            return jerr("file name must be letters/digits/._- and end in .zip")
        dest = os.path.join(cfg.backup_dir, name)
        tmp = dest + ".part"
        size = 0
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            async for chunk in req.stream():
                size += len(chunk)
                if size > 4 * 1024 ** 3:
                    f.close()
                    os.remove(tmp)
                    return jerr("file too large", 413)
                f.write(chunk)
        import zipfile
        if not zipfile.is_zipfile(tmp):
            os.remove(tmp)
            return jerr("not a valid zip file")
        os.replace(tmp, dest)
        store.audit(req.state.user, client_ip(req), "backup_upload", name)
        return JSONResponse({"ok": True, "name": name})

    def one_job_at_a_time() -> Optional[JSONResponse]:
        if jobs.busy():
            return jerr("another job is still running", 409)
        return None

    @guarded
    async def backup_create(req: Request):
        busy = one_job_at_a_time()
        if busy:
            return busy
        d = await body(req)
        argv = run_cli("backup") + (["--node"] if d.get("include_node") else [])
        jid = jobs.start("backup", "Create backup", argv, cwd=cfg.backup_dir)
        store.audit(req.state.user, client_ip(req), "backup_create")
        return JSONResponse({"job": jid})

    @guarded
    async def backup_restore(req: Request):
        busy = one_job_at_a_time()
        if busy:
            return busy
        name = req.path_params["name"]
        p = safe_backup(name)
        d = await body(req)
        if not p:
            return jerr("not found", 404)
        if d.get("confirm") != name:
            return jerr("type the backup file name to confirm the restore")
        jid = jobs.start("restore", f"Restore {name}", run_cli("restore", "--file", p, "--yes"), cwd=cfg.backup_dir)
        store.audit(req.state.user, client_ip(req), "backup_restore", name)
        return JSONResponse({"job": jid})

    @guarded
    async def backup_to_telegram(req: Request):
        busy = one_job_at_a_time()
        if busy:
            return busy
        d = await body(req)
        p = safe_backup(req.path_params["name"])
        if not p:
            return jerr("not found", 404)
        inst = str(d.get("instance", "default"))
        if not NAME_RE.match(inst):
            return jerr("bad instance name")
        jid = jobs.start("telegram", "Send to Telegram", run_cli("telegram-send", "--file", p, "--instance", inst))
        return JSONResponse({"job": jid})

    # ------------------------------------------------------------------ schedulers
    @guarded
    async def sched_list(req: Request):
        return JSONResponse({"items": schedulers()})

    def find_sched(name: str) -> Optional[Dict[str, Any]]:
        return next((x for x in schedulers() if x["name"] == name), None) if NAME_RE.match(name or "") else None

    def systemctl(*args: str):
        return subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=60)

    def kill_session(kind: str, name: str) -> None:
        if kind == "screen":
            subprocess.run(["screen", "-S", name, "-X", "quit"], capture_output=True, timeout=15)
        elif kind == "tmux":
            subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True, timeout=15)

    @guarded
    async def sched_action(req: Request):
        name, action = req.path_params["name"], req.path_params["action"]
        rec = find_sched(name)
        if not rec:
            return jerr("not found", 404)
        if action == "upgrade":
            return upgrade_sched(req, rec)
        if action not in ("start", "stop", "restart", "enable", "disable"):
            return jerr("bad request")
        if rec["kind"] in ("screen", "tmux"):
            if action != "stop":
                return jerr("this scheduler runs in " + rec["kind"] + "; use “Convert to service” to manage it here")
            kill_session(rec["kind"], name)
        elif rec["kind"] == "systemd":
            if not cfg.use_systemctl:
                return jerr("systemd is not available on this host", 501)
            r = systemctl(action, rec["unit"] + ".service")
            if r.returncode != 0:
                return jerr((r.stderr or "systemctl failed").strip()[:300], 500)
        else:
            return jerr("this scheduler is not installed; convert or delete it")
        store.audit(req.state.user, client_ip(req), f"scheduler_{action}", name)
        return JSONResponse({"ok": True})

    def upgrade_sched(req: Request, rec: Dict[str, Any]):
        """Turn an old scheduler (legacy unit with token in ExecStart / screen / tmux / orphan creds)
        into a normal managed systemd service with credentials in the 0600 file."""
        name = rec["name"]
        cpath = os.path.join(cfg.creds_dir, f"{name}.json")
        meta: Optional[Dict[str, Any]] = None
        if os.path.isfile(cpath):
            try:
                meta = json.loads(_read(cpath))
            except (OSError, ValueError):
                meta = None
        if meta is None and rec["kind"] == "systemd":
            meta = parse_legacy_unit(unit_path(name))
        if not meta or not meta.get("token") or not meta.get("chat"):
            return jerr("no Telegram credentials were found for this scheduler; delete it and create a new one", 409)
        os.makedirs(cfg.creds_dir, mode=0o700, exist_ok=True)
        fd = os.open(cpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"token": meta["token"], "chat": str(meta["chat"]), "proxy": meta.get("proxy") or "",
                       "interval": float(meta.get("interval") or 1.0), "node": bool(meta.get("node"))}, f)
        if rec["kind"] in ("screen", "tmux"):
            kill_session(rec["kind"], name)
        os.makedirs(cfg.unit_dir, exist_ok=True)
        ufd = os.open(unit_path(name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(ufd, "w") as f:
            f.write(_unit_text(name, float(meta.get("interval") or 1.0), bool(meta.get("node"))))
        if cfg.use_systemctl:
            for cmd in (("daemon-reload",), ("enable", rec["unit"] + ".service"), ("restart", rec["unit"] + ".service")):
                r = systemctl(*cmd)
                if r.returncode != 0:
                    return jerr("upgraded, but systemd said: " + (r.stderr or "failed").strip()[:300], 500)
        store.audit(req.state.user, client_ip(req), "scheduler_upgrade", name)
        return JSONResponse({"ok": True, "started": cfg.use_systemctl})

    @guarded
    async def sched_logs(req: Request):
        name = req.path_params["name"]
        rec = find_sched(name)
        if not rec or rec["kind"] != "systemd" or not cfg.use_systemctl:
            return jerr("logs are only available for systemd services — use “Convert to service”", 400)
        try:
            n = min(max(int(req.query_params.get("n", "150") or 150), 10), 1000)
        except ValueError:
            n = 150
        r = subprocess.run(["journalctl", "-u", rec["unit"] + ".service", "-n", str(n), "--no-pager", "-o", "short-iso"],
                           capture_output=True, text=True, timeout=20)
        return JSONResponse({"log": r.stdout[-60000:]})

    def _unit_text(name: str, interval: float, node: bool) -> str:
        py = shutil.which(cfg.cli[0]) or cfg.cli[0]
        parts = [py] + list(cfg.cli[1:]) + ["--daemon-backup", "--interval", repr(float(interval)), "--instance", name]
        if node:
            parts.append("--node")
        if any(re.search(r"\s", x) for x in parts):
            raise ValueError("paths with spaces are not supported")
        return ("[Unit]\nDescription=PasarGuard Scheduled Backup (" + name + ")\n"
                "After=network-online.target docker.service\nWants=network-online.target\n\n"
                "[Service]\nType=simple\nExecStart=" + " ".join(parts) + "\nRestart=always\nRestartSec=10\nUser=root\n\n"
                "[Install]\nWantedBy=multi-user.target\n")

    @guarded
    async def sched_create(req: Request):
        """Create a Telegram backup scheduler (credentials file + systemd service) from the panel."""
        d = await body(req)
        name = str(d.get("name") or "").strip()
        if not name:
            n = 1
            while any(os.path.exists(x) for x in (os.path.join(cfg.creds_dir, f"{UNIT_BASE}-{n}.json"),
                                                    unit_path(f"{UNIT_BASE}-{n}"))):
                n += 1
            name = str(n)
        if not NAME_RE.match(name):
            return jerr("name may only contain letters, digits, '-' and '_'")
        name = unit_of(name)                          # same convention as the terminal menu: instance == unit name
        if not NAME_RE.match(name):
            return jerr("name is too long")
        token, chat = str(d.get("token", "")).strip(), str(d.get("chat", "")).strip()
        proxy = str(d.get("proxy") or "").strip()
        if not TOKEN_RE.match(token):
            return jerr("invalid bot token format (looks like 123456789:AA…)")
        if not CHAT_RE.match(chat):
            return jerr("invalid chat id (a number like 123456789 or -100…, or @channel)")
        if proxy and not PROXY_RE.match(proxy):
            return jerr("proxy must look like socks5://host:port or http://host:port")
        try:
            iv = float(d.get("interval", 1))
        except (TypeError, ValueError):
            return jerr("interval must be a number of hours")
        if not 0.05 <= iv <= 24 * 30:
            return jerr("interval out of range")
        path = os.path.join(cfg.creds_dir, f"{name}.json")
        if os.path.exists(path) or os.path.exists(unit_path(name)):
            return jerr("a scheduler with this name already exists", 409)
        if d.get("test", True):                       # fail early on a wrong token / chat id
            ok_, msg = await asyncio.get_running_loop().run_in_executor(None, tg.test, token, chat, proxy or None)
            if not ok_:
                return jerr("Telegram test failed: " + msg, 400)
        try:
            unit = _unit_text(name, iv, bool(d.get("node")))
        except ValueError as e:
            return jerr(str(e), 500)
        os.makedirs(cfg.creds_dir, mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"token": token, "chat": chat, "proxy": proxy, "interval": iv, "node": bool(d.get("node"))}, f)
        os.makedirs(cfg.unit_dir, exist_ok=True)
        upath = unit_path(name)
        ufd = os.open(upath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(ufd, "w") as f:
            f.write(unit)
        started, err = False, ""
        if cfg.use_systemctl:
            unit_name = unit_of(name) + ".service"
            for cmd in (["systemctl", "daemon-reload"], ["systemctl", "enable", unit_name],
                        ["systemctl", "restart", unit_name]):
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if r.returncode != 0:
                    err = (r.stderr or "systemctl failed").strip()[:300]
                    break
            else:
                started = True
        store.audit(req.state.user, client_ip(req), "scheduler_create", name)
        if err:
            return jerr("scheduler saved but could not be started: " + err, 500, name=name)
        return JSONResponse({"ok": True, "name": name, "started": started})

    @guarded
    async def sched_delete(req: Request):
        name = req.path_params["name"]
        rec = find_sched(name)
        if not rec:
            return jerr("not found", 404)
        if rec["kind"] in ("screen", "tmux"):
            kill_session(rec["kind"], name)
        if cfg.use_systemctl and rec["kind"] == "systemd":
            systemctl("disable", "--now", rec["unit"] + ".service")
        for p_ in (unit_path(name) if rec["kind"] == "systemd" else None, os.path.join(cfg.creds_dir, f"{name}.json")):
            if p_:
                try:
                    os.remove(p_)
                except OSError:
                    pass
        if cfg.use_systemctl:
            systemctl("daemon-reload")
        store.audit(req.state.user, client_ip(req), "scheduler_delete", name)
        return JSONResponse({"ok": True})

    @guarded
    async def sched_update(req: Request):
        name = req.path_params["name"]
        d = await body(req)
        path = os.path.join(cfg.creds_dir, f"{name}.json")
        if not NAME_RE.match(name) or not find_sched(name):
            return jerr("not found", 404)
        if not os.path.isfile(path):
            return jerr("this is an old-style scheduler: click “Convert to service” first", 409)
        data = json.loads(_read(path))
        if d.get("token"):
            tok = str(d["token"]).strip()
            if not TOKEN_RE.match(tok):
                return jerr("invalid bot token format")
            data["token"] = tok
        if d.get("chat") not in (None, ""):
            chat = str(d["chat"]).strip()
            if not CHAT_RE.match(chat):
                return jerr("invalid chat id")
            data["chat"] = chat
        if "proxy" in d:
            px = str(d["proxy"] or "").strip()
            if px and not PROXY_RE.match(px):
                return jerr("proxy must look like socks5://host:port or http://host:port")
            data["proxy"] = px
        if d.get("interval") not in (None, ""):
            try:
                iv = float(d["interval"])
            except (TypeError, ValueError):
                return jerr("interval must be a number of hours")
            if not 0.05 <= iv <= 24 * 30:
                return jerr("interval out of range")
            data["interval"] = iv
        if "node" in d:
            data["node"] = bool(d["node"])
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.chmod(path, 0o600)
        store.audit(req.state.user, client_ip(req), "scheduler_update", name)
        if d.get("restart") and cfg.use_systemctl:
            subprocess.run(["systemctl", "restart", unit_of(name) + ".service"], capture_output=True, timeout=60)
        return JSONResponse({"ok": True})

    @guarded
    async def telegram_test(req: Request):
        d = await body(req)
        name = str(d.get("instance", ""))
        token, chat, proxy = str(d.get("token", "")), str(d.get("chat", "")), str(d.get("proxy", "")) or None
        if name and not token:
            if not NAME_RE.match(name):
                return jerr("bad instance")
            try:
                m = json.loads(_read(os.path.join(cfg.creds_dir, f"{name}.json")))
            except (OSError, ValueError):
                return jerr("instance not found", 404)
            token, chat = m.get("token", ""), chat or str(m.get("chat", ""))
            proxy = proxy or (m.get("proxy") or None)
        if not token or not chat:
            return jerr("token and chat id are required")
        if not TOKEN_RE.match(token) or not CHAT_RE.match(chat):
            return jerr("invalid bot token or chat id format")
        if proxy and not PROXY_RE.match(proxy):
            return jerr("invalid proxy")
        loop = asyncio.get_running_loop()
        ok_, msg = await loop.run_in_executor(None, tg.test, token, chat, proxy)
        return JSONResponse({"ok": ok_, "message": msg})

    # ------------------------------------------------------------------ servers / transfer
    def public_server(s: Dict[str, Any]) -> Dict[str, Any]:
        return {k: s.get(k, "") for k in ("id", "name", "host", "port", "user", "auth", "host_fp")}

    @guarded
    async def servers_list(req: Request):
        return JSONResponse({"items": [public_server(s) for s in store.get("servers", [])]})

    @guarded
    async def servers_add(req: Request):
        d = await body(req)
        host, user = str(d.get("host", "")).strip(), str(d.get("user", "root")).strip()
        name = str(d.get("name") or host).strip()[:60]
        if not re.match(r"^[A-Za-z0-9_.:][A-Za-z0-9_.:-]{0,254}$", host):
            return jerr("invalid host")
        if user != "root":
            return jerr("transfer needs the root user on the destination server")
        try:
            port = int(d.get("port") or 22)
        except (TypeError, ValueError):
            return jerr("invalid port")
        if not 1 <= port <= 65535:
            return jerr("invalid port")
        fp = str(d.get("host_fp") or "").strip()
        if fp and not re.match(r"^SHA256:[A-Za-z0-9+/]{43}$", fp):
            return jerr("host key fingerprint must look like SHA256:xxxxxxxx… (ssh-keygen -lf output)")
        auth = "key" if d.get("private_key") else "password"
        secret = str(d.get("private_key") or d.get("password") or "")
        if not secret:
            return jerr("a password or a private key is required")
        sid = secrets.token_hex(4)
        store.set_secret(f"server:{sid}", secret)
        servers = store.get("servers", []) + [{"id": sid, "name": name, "host": host, "port": port, "user": user, "auth": auth, "host_fp": fp}]
        store.set("servers", servers)
        store.audit(req.state.user, client_ip(req), "server_add", host)
        return JSONResponse({"ok": True, "id": sid})

    @guarded
    async def servers_delete(req: Request):
        sid = req.path_params["sid"]
        store.set("servers", [s for s in store.get("servers", []) if s["id"] != sid])
        store.delete(f"secret:server:{sid}")
        return JSONResponse({"ok": True})

    @guarded
    async def transfer(req: Request):
        busy = one_job_at_a_time()
        if busy:
            return busy
        d = await body(req)
        srv = next((s for s in store.get("servers", []) if s["id"] == d.get("server")), None)
        if not srv:
            return jerr("choose a saved server")
        if d.get("confirm") != srv["host"]:
            return jerr("type the destination host to confirm: its PasarGuard data will be replaced")
        secret = store.get_secret(f"server:{srv['id']}") or ""
        argv = run_cli("transfer", "--host", srv["host"], "--port", str(srv["port"]), "--user", srv["user"], "--yes")
        env = {"PGB_SSH_SECRET": secret, "PGB_SSH_AUTH": srv["auth"], "PGB_SSH_HOSTKEY": srv.get("host_fp", "")}
        if d.get("include_node"):
            argv.append("--node")
        jid = jobs.start("transfer", f"Transfer to {srv['name']}", argv, env=env, redact=[secret])
        store.audit(req.state.user, client_ip(req), "transfer", srv["host"])
        return JSONResponse({"job": jid})

    # ------------------------------------------------------------------ database conversion
    # Produces a NEW converted backup zip in the backup folder. It never restores anything.
    CONVERT_TARGETS = ("postgresql", "timescaledb", "mysql", "mariadb")

    @guarded
    async def convert_inspect(req: Request):
        from .. import dbconv
        p = safe_backup(req.path_params["name"])
        if not p:
            return jerr("not found", 404)
        try:
            info = await asyncio.get_running_loop().run_in_executor(None, dbconv.inspect, p)
        except Exception as e:
            return JSONResponse({"type": "", "targets": [], "error": str(e)[:200]})
        return JSONResponse(info)

    @guarded
    async def convert_start(req: Request):
        from .. import dbconv
        busy = one_job_at_a_time()
        if busy:
            return busy
        d = await body(req)
        to = str(d.get("to", ""))
        if to not in CONVERT_TARGETS:
            return jerr("unknown target engine")
        src = str(d.get("source", ""))
        if src == "live":
            argv = run_cli("convert", "--from-live", "--to", to)
            title = f"Convert this server's backup to {to}"
        else:
            p = safe_backup(src)
            if not p:
                return jerr("backup not found", 404)
            try:
                info = dbconv.inspect(p)
            except Exception as e:
                return jerr(str(e)[:200])
            if to not in info["targets"]:
                return jerr(f"{info['type']} → {to} is not supported")
            argv = run_cli("convert", "--file", p, "--to", to)
            title = f"Convert {src} to {to}"
        jid = jobs.start("convert", title, argv, cwd=cfg.backup_dir)
        store.audit(req.state.user, client_ip(req), "db_convert", f"{src}->{to}")
        return JSONResponse({"job": jid})

    # ------------------------------------------------------------------ jobs
    @guarded
    async def jobs_list(req: Request):
        return JSONResponse({"items": store.jobs(60)})

    @guarded
    async def job_get(req: Request):
        j = store.job_get(int(req.path_params["jid"]))
        return JSONResponse(j) if j else jerr("not found", 404)

    @guarded
    async def job_cancel(req: Request):
        return JSONResponse({"ok": jobs.cancel(int(req.path_params["jid"]))})

    async def job_stream(req: Request):
        s = session(req)
        if not s:
            return jerr("authentication required", 401)
        jid = int(req.path_params["jid"])

        async def gen():
            sent = 0
            while True:
                j = store.job_get(jid)
                if not j:
                    yield "event: end\ndata: missing\n\n"
                    return
                log = j["log"] or ""
                if len(log) > sent:
                    for ln in log[sent:].splitlines():
                        yield "data: " + json.dumps(ln) + "\n\n"
                    sent = len(log)
                if j["status"] != "running":
                    yield f"event: end\ndata: {j['status']}\n\n"
                    return
                if await req.is_disconnected():
                    return
                await asyncio.sleep(0.6)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @guarded
    async def audit_list(req: Request):
        return JSONResponse({"items": store.audit_tail(150)})

    # ------------------------------------------------------------------ static
    async def index(req: Request):
        p = os.path.join(STATIC_DIR, "index.html")
        if not os.path.isfile(p):
            return PlainTextResponse("UI not installed", 500)
        return FileResponse(p, headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                       "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})

    async def static_file(req: Request):
        rel = req.path_params["path"]
        full = os.path.realpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(full):
            return PlainTextResponse("not found", 404)
        # fonts never change; code/styles must revalidate (ETag) so an updated panel is never shown stale
        cache = "public, max-age=604800, immutable" if "/fonts/" in full.replace(os.sep, "/") else "no-cache"
        return FileResponse(full, headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"})

    api = [
        Route("/login", login, methods=["POST"]), Route("/logout", logout, methods=["POST"]),
        Route("/me", me), Route("/password", change_password, methods=["POST"]),
        Route("/2fa/setup", totp_setup, methods=["POST"]), Route("/2fa/enable", totp_enable, methods=["POST"]),
        Route("/2fa/disable", totp_disable, methods=["POST"]),
        Route("/dashboard", dashboard),
        Route("/backups", backups_list), Route("/backups/create", backup_create, methods=["POST"]),
        Route("/backups/upload", backup_upload, methods=["POST"]),
        Route("/backups/{name}/download", backup_download), Route("/backups/{name}", backup_delete, methods=["DELETE"]),
        Route("/backups/{name}/restore", backup_restore, methods=["POST"]),
        Route("/backups/{name}/telegram", backup_to_telegram, methods=["POST"]),
        Route("/schedulers", sched_list), Route("/schedulers/create", sched_create, methods=["POST"]),
        Route("/schedulers/{name}", sched_delete, methods=["DELETE"]),
        Route("/schedulers/{name}/logs", sched_logs),
        Route("/schedulers/{name}/update", sched_update, methods=["POST"]),
        Route("/schedulers/{name}/{action}", sched_action, methods=["POST"]),
        Route("/telegram/test", telegram_test, methods=["POST"]),
        Route("/servers", servers_list), Route("/servers/add", servers_add, methods=["POST"]),
        Route("/servers/{sid}", servers_delete, methods=["DELETE"]), Route("/transfer", transfer, methods=["POST"]),
        Route("/convert/{name}", convert_inspect), Route("/convert", convert_start, methods=["POST"]),
        Route("/jobs", jobs_list), Route("/jobs/{jid:int}", job_get),
        Route("/jobs/{jid:int}/cancel", job_cancel, methods=["POST"]), Route("/jobs/{jid:int}/stream", job_stream),
        Route("/audit", audit_list),
    ]
    inner = Starlette(routes=[Route("/", index), Route("/static/{path:path}", static_file), Mount("/api", routes=api)])
    root = Starlette(routes=[Mount("/" + secret_path, app=inner)])
    root.add_middleware(SecurityHeaders)
    root.state.store = store
    root.state.secret_path = secret_path
    root.state.cfg = cfg
    return root


def app_from_env() -> Starlette:  # uvicorn factory target
    return create_app()
