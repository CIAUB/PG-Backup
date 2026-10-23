"""Install / manage the PG-Backup web panel (stdlib only; runs as root).

    python3 -m pgb.installer install|update|status|start|stop|restart|url|reset-password|reset-2fa|uninstall
"""
from __future__ import annotations

import getpass
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from typing import Optional

ROOT = os.environ.get("PGB_ROOT", "/opt/pg-backup")
VENV = os.path.join(ROOT, "venv")
STATE = os.environ.get("PGB_STATE_DIR", "/var/lib/pg-backup-panel")
CONF_DIR = "/etc/pg-backup-panel"
PANEL_UNIT = "pg-backup-panel"
CADDY_UNIT = "pg-backup-caddy"
UNIT_DIR = os.environ.get("PGB_UNIT_DIR", "/etc/systemd/system")
REQS = ["starlette>=0.37,<2", "uvicorn>=0.30,<1", "cryptography>=42"]
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")


def say(msg: str) -> None:
    print(f"  [..]  {msg}", flush=True)


def die(msg: str) -> None:
    print(f"  [ERR] {msg}", flush=True)
    raise SystemExit(1)


def sh(cmd, check=True, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, text=True, capture_output=True, **kw)
    if check and r.returncode != 0:
        die(f"{' '.join(cmd) if isinstance(cmd, list) else cmd}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r


def port_free(port: int, host: str = "0.0.0.0") -> bool:
    s = socket.socket()
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


# ---------- rendering (pure, unit-tested) ----------
def render_panel_unit(port: int, backup_bin: str = "/usr/local/bin/PG-Backup") -> str:
    return f"""[Unit]
Description=PG-Backup web panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={ROOT}
Environment=PGB_STATE_DIR={STATE}
Environment="PGB_BACKUP_BIN=python3 {backup_bin}"
Environment=PYTHONPATH={ROOT}
ExecStart={VENV}/bin/python -m pgb.panel run --host 127.0.0.1 --port {port}
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
LockPersonality=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectClock=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
SystemCallArchitectures=native
UMask=0077

[Install]
WantedBy=multi-user.target
"""


def render_caddyfile(upstream_port: int, domain: Optional[str], https_port: int, email: Optional[str] = None) -> str:
    if email and not EMAIL_RE.match(email):
        raise ValueError("invalid email")
    if domain and not DOMAIN_RE.match(domain):
        raise ValueError("invalid domain")
    glob = "{\n    admin off\n" + (f"    email {email}\n" if email else "") + "}\n\n"
    if domain:
        site = f"{domain}:{https_port}" if https_port != 443 else domain
        tls = ""
    else:
        site = f":{https_port}"
        tls = "    tls internal\n"
    return (glob + f"{site} {{\n{tls}    encode zstd gzip\n    header {{\n        Strict-Transport-Security \"max-age=31536000\"\n"
            f"        X-Content-Type-Options nosniff\n        -Server\n    }}\n    reverse_proxy 127.0.0.1:{upstream_port}\n}}\n")


def render_caddy_unit(caddy_bin: str) -> str:
    return f"""[Unit]
Description=PG-Backup panel HTTPS proxy (Caddy)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=XDG_DATA_HOME=/var/lib/pg-backup-caddy
Environment=XDG_CONFIG_HOME=/var/lib/pg-backup-caddy
ExecStart={caddy_bin} run --config {CONF_DIR}/Caddyfile --adapter caddyfile
Restart=on-failure
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=full
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
UMask=0077

[Install]
WantedBy=multi-user.target
"""


# ---------- steps ----------
def need_root() -> None:
    if os.geteuid() != 0:
        die("run as root")


def ensure_venv() -> None:
    py = os.path.join(VENV, "bin", "python")
    if not os.path.exists(py):
        say("Creating Python virtual environment…")
        r = sh([sys.executable, "-m", "venv", VENV], check=False)
        if r.returncode != 0 and shutil.which("apt-get"):
            sh(["apt-get", "install", "-y", "python3-venv"])
            r = sh([sys.executable, "-m", "venv", VENV])
    say("Installing panel dependencies (starlette, uvicorn, cryptography)…")
    sh([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=False)
    sh([py, "-m", "pip", "install", "--quiet"] + REQS)


def ensure_caddy() -> str:
    path = shutil.which("caddy")
    if path:
        return path
    if not shutil.which("apt-get"):
        die("Caddy is not installed and this is not a Debian/Ubuntu system. Install Caddy manually, then re-run.")
    say("Installing Caddy…")
    was_active = sh(["systemctl", "is-active", "caddy"], check=False).stdout.strip() == "active"
    r = sh(["apt-get", "install", "-y", "caddy"], check=False)
    if r.returncode != 0:
        say("Adding the official Caddy repository…")
        sh(["apt-get", "install", "-y", "debian-keyring", "debian-archive-keyring", "apt-transport-https", "curl", "gnupg"])
        sh("curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg", shell=True)
        sh("curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list", shell=True)
        sh(["apt-get", "update"])
        sh(["apt-get", "install", "-y", "caddy"])
    if not was_active:                       # leave a pre-existing Caddy alone; stop the packaged default one
        sh(["systemctl", "disable", "--now", "caddy"], check=False)
    found = shutil.which("caddy")
    if not found:
        die("caddy binary not found after install")
    return found


def write(path: str, text: str, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def panel_env() -> dict:
    return dict(os.environ, PGB_STATE_DIR=STATE, PYTHONPATH=ROOT)


def pgb(*args: str, env_extra: Optional[dict] = None, capture: bool = True) -> subprocess.CompletedProcess:
    env = panel_env()
    env.update(env_extra or {})
    return subprocess.run([os.path.join(VENV, "bin", "python"), "-m", "pgb.panel", *args], env=env, cwd=ROOT,
                          text=True, capture_output=capture)


def ask_password() -> str:
    while True:
        a = getpass.getpass("  > Choose an admin password (min 10 chars, mixed case + digit): ")
        b = getpass.getpass("  > Repeat password: ")
        if a != b:
            print("  passwords differ")
            continue
        sys.path.insert(0, ROOT)
        from pgb.panel.security import check_password_strength
        bad = check_password_strength(a)
        if bad:
            print("  " + bad)
            continue
        return a


def current_url() -> Optional[str]:
    path = pgb("show-path").stdout.strip()
    if not path:
        return None
    caddy = os.path.join(CONF_DIR, "Caddyfile")
    site = ""
    if os.path.exists(caddy):
        m = re.search(r"^(\S+) \{$", open(caddy).read().split("}\n\n", 1)[-1], re.M)
        site = m.group(1) if m else ""
    if site.startswith(":"):
        ip = sh("hostname -I | awk '{print $1}'", check=False, shell=True).stdout.strip() or "SERVER_IP"
        return f"https://{ip}{site}/{path}/"
    return f"https://{site}/{path}/" if site else f"http://127.0.0.1:8787/{path}/ (no HTTPS proxy configured)"


def cmd_install(update: bool = False) -> None:
    need_root()
    if not os.path.isdir(os.path.join(ROOT, "pgb", "panel")):
        die("panel files are missing; run this through PG-Backup menu option 6")
    ensure_venv()
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    first = not pgb("show-path").stdout.strip()
    if first:
        user = input("  > Admin username [admin]: ").strip() or "admin"
        if not re.match(r"^[A-Za-z0-9_.-]{3,32}$", user):
            die("username must be 3-32 chars of letters, digits, . _ -")
        r = pgb("setup", "--username", user, env_extra={"PGB_NEW_PASSWORD": ask_password()})
        if r.returncode != 0:
            die(r.stderr.strip() or r.stdout.strip())
    if not update or not os.path.exists(os.path.join(CONF_DIR, "Caddyfile")):
        domain = input("  > Domain pointing to this server (empty = self-signed HTTPS on the IP): ").strip().lower() or None
        if domain and not DOMAIN_RE.match(domain):
            die("that does not look like a valid domain name")
        default_port = 443 if domain and port_free(443) else 8443
        raw = input(f"  > HTTPS port [{default_port}]: ").strip()
        https_port = int(raw) if raw.isdigit() else default_port
        if not 1 <= https_port <= 65535:
            die("invalid port")
        if not port_free(https_port) and sh(["systemctl", "is-active", CADDY_UNIT], check=False).stdout.strip() != "active":
            die(f"port {https_port} is already in use; pick another one")
        if domain and not port_free(80) and sh(["systemctl", "is-active", CADDY_UNIT], check=False).stdout.strip() != "active":
            say("WARNING: port 80 is busy — Let's Encrypt HTTP validation will fail. Free it or use no domain (self-signed).")
        email = input("  > Email for Let's Encrypt notices (optional): ").strip() or None
        if email and not EMAIL_RE.match(email):
            die("that does not look like a valid email address")
        caddy = ensure_caddy()
        write(os.path.join(CONF_DIR, "Caddyfile"), render_caddyfile(8787, domain, https_port, email), 0o640)
        write(os.path.join(UNIT_DIR, f"{CADDY_UNIT}.service"), render_caddy_unit(caddy))
    write(os.path.join(UNIT_DIR, f"{PANEL_UNIT}.service"), render_panel_unit(8787))
    sh(["systemctl", "daemon-reload"])
    for u in (PANEL_UNIT, CADDY_UNIT):
        sh(["systemctl", "enable", u], check=False)
        sh(["systemctl", "restart", u])
    say("Panel is running.")
    url = current_url()
    print(f"\n  Panel address (keep it secret — it contains a random path):\n    {url}\n")
    print("  Enable two-factor authentication from Security → TOTP after your first login.")


def cmd_status() -> None:
    for u in (PANEL_UNIT, CADDY_UNIT):
        print(f"  {u}: {sh(['systemctl', 'is-active', u], check=False).stdout.strip() or 'unknown'}")
    print(f"  address: {current_url() or 'not installed'}")


def cmd_unit(action: str) -> None:
    need_root()
    for u in (PANEL_UNIT, CADDY_UNIT):
        sh(["systemctl", action, u], check=False)
    cmd_status()


def cmd_reset(what: str) -> None:
    need_root()
    if what == "reset-password":
        r = pgb("reset-password", env_extra={"PGB_NEW_PASSWORD": ask_password()})
    else:
        r = pgb(what)
    print("  " + (r.stdout.strip() or r.stderr.strip()))


def cmd_uninstall() -> None:
    need_root()
    if input("  > Remove the panel (settings and panel database too)? Backups are kept. Type YES: ").strip() != "YES":
        return
    for u in (PANEL_UNIT, CADDY_UNIT):
        sh(["systemctl", "disable", "--now", u], check=False)
        try:
            os.remove(os.path.join(UNIT_DIR, f"{u}.service"))
        except OSError:
            pass
    sh(["systemctl", "daemon-reload"], check=False)
    for d in (VENV, STATE, CONF_DIR, "/var/lib/pg-backup-caddy"):
        shutil.rmtree(d, ignore_errors=True)
    print("  panel removed (the pgb files in /opt/pg-backup were left in place)")


def main(argv=None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "status"
    if cmd in ("install", "update"):
        cmd_install(update=(cmd == "update"))
    elif cmd == "status":
        cmd_status()
    elif cmd in ("start", "stop", "restart"):
        cmd_unit(cmd)
    elif cmd == "url":
        print(current_url() or "not installed")
    elif cmd in ("reset-password", "reset-2fa"):
        cmd_reset(cmd)
    elif cmd == "uninstall":
        cmd_uninstall()
    else:
        die("usage: install|update|status|start|stop|restart|url|reset-password|reset-2fa|uninstall")


if __name__ == "__main__":
    main()
