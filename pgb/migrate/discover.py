"""Find panel installs on this machine, open their databases, back them up, restart them."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from .dbio import BaseDB, DBError, DockerDB, SqliteDB
from .readers import detect_kind

KNOWN = {
    "pasarguard": {"dirs": ["/opt/pasarguard"], "sqlite": ["/var/lib/pasarguard/db.sqlite3"], "label": "PasarGuard"},
    "marzban": {"dirs": ["/opt/marzban"], "sqlite": ["/var/lib/marzban/db.sqlite3"], "label": "Marzban"},
    "marzneshin": {"dirs": ["/etc/opt/marzneshin", "/opt/marzneshin"], "sqlite": ["/var/lib/marzneshin/db.sqlite3"],
                   "label": "Marzneshin"},
    "rebecca": {"dirs": ["/opt/rebecca"], "sqlite": ["/var/lib/rebecca/db.sqlite3"], "label": "Rebecca"},
    "3xui": {"dirs": [], "sqlite": ["/etc/x-ui/x-ui.db"], "label": "3x-ui / x-ui"},
}


def read_env(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for ln in open(path, encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[k.strip()] = v
    except OSError:
        pass
    return out


def _compose_services(d: str) -> Dict[str, Dict[str, Any]]:
    try:
        r = subprocess.run(["docker", "compose", "config", "--format", "json"], cwd=d, capture_output=True,
                           timeout=60)
        return json.loads(r.stdout or b"{}").get("services", {}) if r.returncode == 0 else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def discover() -> List[Dict[str, Any]]:
    found = []
    for kind, info in KNOWN.items():
        for d in info["dirs"] or [None]:
            env = read_env(os.path.join(d, ".env")) if d else {}
            url = env.get("SQLALCHEMY_DATABASE_URL", "")
            cands = []
            if url and not url.startswith("sqlite"):
                found.append({"kind": kind, "label": info["label"], "dir": d, "engine": url.split(":", 1)[0].split("+")[0],
                              "path": None, "needs_docker": True})
                break
            if url.startswith("sqlite"):
                cands.append(unquote(urlparse(url).path) if "///" in url else "")
            cands += info["sqlite"]
            for p in cands:
                if p and os.path.isfile(p):
                    found.append({"kind": kind, "label": info["label"], "dir": d, "engine": "sqlite", "path": p,
                                  "needs_docker": False})
                    break
            else:
                continue
            break
    return found


def open_panel(kind: str, d: Optional[str] = None, path: Optional[str] = None, writable: bool = False) -> BaseDB:
    """Open the database of an installed panel. ``path`` forces a sqlite file."""
    info = KNOWN.get(kind)
    if path:
        return SqliteDB(path, writable=writable)
    if not info:
        raise DBError(f"unknown panel kind {kind}")
    d = d or next((x for x in info["dirs"] if os.path.isdir(x)), None)
    env = read_env(os.path.join(d, ".env")) if d else {}
    url = env.get("SQLALCHEMY_DATABASE_URL", "")
    if not url or url.startswith("sqlite"):
        cand = ([unquote(urlparse(url).path)] if url and "///" in url else []) + info["sqlite"]
        for p in cand:
            if p and os.path.isfile(p):
                return SqliteDB(p, writable=writable)
        raise DBError(f"no SQLite database found for {info['label']}")
    u = urlparse(url)
    scheme = u.scheme.split("+")[0]
    dialect = "postgresql" if scheme in ("postgresql", "postgres", "timescaledb") else \
        "mysql" if scheme in ("mysql", "mariadb") else None
    if not dialect:
        raise DBError(f"unsupported database engine: {scheme}")
    if not d:
        raise DBError("a compose directory is needed for this database")
    wanted = ("postgres", "timescale") if dialect == "postgresql" else ("mysql", "mariadb")
    services = _compose_services(d)
    svc = next((n for n, sv in services.items()
                if any(w in (sv.get("image", "") + n).lower() for w in wanted)), None)
    if not svc:
        raise DBError(f"could not find the {dialect} service in {d}/docker-compose.yml")
    return resolve_docker_db(d, svc, dialect, unquote(u.username or ""), unquote(u.password or ""),
                             (u.path or "/").lstrip("/").split("?")[0], env, services.get(svc, {}).get("environment") or {})


_PASS_KEYS = {
    "postgresql": ["POSTGRES_PASSWORD", "DB_PASSWORD", "DATABASE_PASSWORD", "PGPASSWORD"],
    "mysql": ["MYSQL_PASSWORD", "MARIADB_PASSWORD", "MYSQL_ROOT_PASSWORD", "MARIADB_ROOT_PASSWORD",
              "DB_PASSWORD", "DATABASE_PASSWORD"],
}
_USER_KEYS = {"postgresql": ["POSTGRES_USER", "DB_USER"], "mysql": ["MYSQL_USER", "MARIADB_USER", "DB_USER"]}
_DB_KEYS = {"postgresql": ["POSTGRES_DB", "DB_NAME"], "mysql": ["MYSQL_DATABASE", "MARIADB_DATABASE", "DB_NAME"]}


def credential_candidates(dialect: str, url_user: str, url_pass: str, dbname: str,
                          dotenv: Dict[str, str], svc_env: Any) -> List[tuple]:
    """Ordered (user, password, dbname) guesses: the panel URL first, then whatever the
    .env file and the database container itself were configured with, then no password
    (PostgreSQL trusts local socket connections inside the container)."""
    if isinstance(svc_env, list):                       # compose may render env as ["K=V", ...]
        svc_env = dict(x.split("=", 1) for x in svc_env if "=" in x)
    pool = {**dotenv, **{str(k): str(v) for k, v in (svc_env or {}).items() if v is not None}}
    users = [url_user] + [pool[k] for k in _USER_KEYS[dialect] if pool.get(k)]
    users += ["postgres"] if dialect == "postgresql" else ["root"]
    dbs = [dbname] + [pool[k] for k in _DB_KEYS[dialect] if pool.get(k)]
    pws = [url_pass] + [pool[k] for k in _PASS_KEYS[dialect] if pool.get(k)] + [""]
    out, seen = [], set()
    for pw in dict.fromkeys(pws):
        for us in dict.fromkeys(x for x in users if x):
            if dialect == "mysql" and us != "root" and pw == pool.get("MYSQL_ROOT_PASSWORD"):
                continue
            for db in dict.fromkeys(x for x in dbs if x):
                if (us, pw, db) not in seen:
                    seen.add((us, pw, db))
                    out.append((us, pw, db))
    return out[:40]


def resolve_docker_db(d: str, svc: str, dialect: str, url_user: str, url_pass: str, dbname: str,
                      dotenv: Optional[Dict[str, str]] = None, svc_env: Any = None) -> DockerDB:
    """Return a working DockerDB. If the credentials in the panel's URL are wrong or stale,
    try the ones found in .env / the container environment instead of failing."""
    first_err = ""
    for i, (us, pw, db) in enumerate(credential_candidates(dialect, url_user, url_pass, dbname,
                                                           dotenv or {}, svc_env)):
        cand = DockerDB(d, svc, dialect, us, pw, db)
        try:
            cand._run("SELECT 1;")
        except DBError as e:
            first_err = first_err or str(e)
            if "docker is not installed" in str(e):
                raise
            continue
        cand.credentials_note = "" if i == 0 else \
            "database credentials from the panel URL were rejected; using ones found in .env / the container instead"
        return cand
    raise DBError("could not log in to the database with any credentials found in the panel's .env or the "
                  f"container environment ({first_err or 'no candidates'}). Is the database container running?")


def backup_target(db: BaseDB, dest_dir: str) -> str:
    """Safety dump of the database that is about to be modified."""
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if isinstance(db, SqliteDB):
        dest = os.path.join(dest_dir, f"pre-migration-{stamp}.sqlite3")
        shutil.copy2(db.path, dest)
        for ext in ("-wal", "-shm"):
            if os.path.exists(db.path + ext):
                shutil.copy2(db.path + ext, dest + ext)
        os.chmod(dest, 0o600)
        return dest
    if isinstance(db, DockerDB):
        dest = os.path.join(dest_dir, f"pre-migration-{stamp}.sql")
        if db.dialect == "postgresql":
            cmd = ["docker", "compose", "exec", "-T"] + (["-e", "PGPASSWORD"] if db.password else []) + \
                  [db.service, "pg_dump", "-U", db.user, "-d", db.dbname]
        else:
            cmd = ["docker", "compose", "exec", "-T"] + (["-e", "MYSQL_PWD"] if db.password else []) + \
                  [db.service, "mysqldump", "-u", db.user, "--single-transaction", db.dbname]
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            r = subprocess.run(cmd, cwd=db.compose_dir, stdout=f, stderr=subprocess.PIPE, timeout=1800, env=db.env())
        if r.returncode != 0 or os.path.getsize(dest) == 0:
            raise DBError("database dump failed: " + r.stderr.decode("utf-8", "replace")[:200].replace(db.password or "\0", "***"))
        return dest
    raise DBError("cannot back up this database type")


def restart_panel(kind: str, d: Optional[str]) -> str:
    """Restart the panel so it reloads users (PasarGuard keeps them in memory)."""
    if kind == "3xui":
        r = subprocess.run(["systemctl", "restart", "x-ui"], capture_output=True)
        return "x-ui restarted" if r.returncode == 0 else "could not restart x-ui"
    if d and os.path.isdir(d):
        r = subprocess.run(["docker", "compose", "restart"], cwd=d, capture_output=True, timeout=300)
        return "stack restarted" if r.returncode == 0 else "could not restart the docker stack"
    return "restart the panel manually"
