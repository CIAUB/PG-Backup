#!/usr/bin/env python3
# ============================================================
#   Pasarguard Backup Utility  v3.0
#   Dev by: EOAMIR
#   GitHub: https://github.com/EOAMIR
# ============================================================

import os, sys, subprocess, datetime, shutil
import time, urllib.request, urllib.error, uuid, threading, itertools

# ── ANSI Colors (readable: light on dark) ───────────────────
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    # Foregrounds
    WHITE   = "\033[97m"
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    RED     = "\033[91m"
    GRAY    = "\033[37m"
    DKGRAY  = "\033[90m"

def clr():
    os.system("clear")

def tw():
    try: return os.get_terminal_size().columns
    except: return 80

def hline(ch="─", color=C.DKGRAY):
    return color + ch * tw() + C.RESET

def center(s, raw_len=None):
    l = raw_len if raw_len is not None else len(s)
    pad = max(0, (tw() - l) // 2)
    return " " * pad + s

# ── Status helpers ───────────────────────────────────────────
def ok(msg):   print(f"  {C.GREEN}✔{C.RESET}  {msg}")
def err(msg):  print(f"  {C.RED}✘{C.RESET}  {msg}")
def info(msg): print(f"  {C.CYAN}▸{C.RESET}  {msg}")
def warn(msg): print(f"  {C.YELLOW}!{C.RESET}  {msg}")

def print_success(msg): print(f"{C.GREEN}✅ [SUCCESS]{C.RESET} {msg}")
def print_error(msg):   print(f"{C.RED}❌ [ERROR]{C.RESET}   {msg}")
def print_info(msg):    print(f"{C.CYAN}ℹ️  [INFO]{C.RESET}   {msg}")
def print_warning(msg): print(f"{C.YELLOW}⚠️  [WARNING]{C.RESET} {msg}")

def pause_and_return():
    input(f"\n{C.YELLOW}Press [ENTER] to return to the main menu...{C.RESET}")

# ── Spinner ──────────────────────────────────────────────────
class Spinner:
    def __init__(self, message="Processing..."):
        self.cycle = itertools.cycle(['-', '\\', '|', '/'])
        self.stop  = threading.Event()
        self.msg   = message
        self.thread = threading.Thread(target=self._spin)

    def _spin(self):
        while not self.stop.is_set():
            sys.stdout.write(f"\r{C.YELLOW}⏳ {next(self.cycle)} {self.msg}{C.RESET}")
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
try:
    import paramiko
except ImportError:
    print(f"{C.YELLOW}⏳ Required libraries not found. Installing...{C.RESET}")
    with Spinner("Installing Paramiko... Please wait"):
        try:
            subprocess.check_call(["apt-get", "update"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["apt-get", "install", "-y", "python3-paramiko", "python3-pip", "unzip"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "--quiet"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import paramiko
    print(f"{C.GREEN}✅ Libraries installed successfully!{C.RESET}\n")

# ── Paths ────────────────────────────────────────────────────
PASARGUARD_DIR      = "/opt/pasarguard"
PG_NODE_DIR         = "/opt/pg-node"
PASARGUARD_DATA_DIR = "/var/lib/pasarguard"
PG_NODE_DATA_DIR    = "/var/lib/pg-node"

COMPOSE_DOWN_TIMEOUT     = 30
POSTGRES_READY_MAX_WAIT  = 120
POSTGRES_READY_INTERVAL  = 2
COMPOSE_UP_MAX_WAIT      = 120
COMPOSE_UP_INTERVAL      = 3
COMPOSE_STOP_RETRIES     = 3

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
        print(center(C.CYAN + C.BOLD + line + C.RESET, LOGO_W))

    sub = C.DKGRAY + C.DIM + "B A C K U P   U T I L I T Y   ·   v 3 . 0   ·   E O A M I R" + C.RESET
    print(center(sub, 69))
    print()
    print(hline("─", C.DKGRAY))
    if title:
        print()
        print(center(C.WHITE + C.BOLD + title + C.RESET, len(title)))
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
    print(f"  {C.CYAN}🌐{C.RESET}  {description}...")
    exit_status, out, err = ssh_shell(ssh, command)
    if exit_status == 0:
        ok("Done.")
    else:
        err_msg = err or out
        print_error("Command failed!")
        if err_msg:
            print_error(f"Details: {err_msg}")
    return exit_status == 0 if required else True

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
    print_info("Waiting for timescaledb to become ready...")
    deadline = time.time() + POSTGRES_READY_MAX_WAIT
    while time.time() < deadline:
        ok_v, _, _ = local_shell(
            "docker compose exec -T timescaledb pg_isready -U pasarguard -d postgres", cwd=PASARGUARD_DIR)
        if ok_v:
            print_success("Database is ready.")
            return True
        time.sleep(POSTGRES_READY_INTERVAL)
    print_error("Database did not become ready in time.")
    return False

def wait_postgres_ssh(ssh):
    print_info("Waiting for timescaledb to become ready...")
    deadline = time.time() + POSTGRES_READY_MAX_WAIT
    while time.time() < deadline:
        ec, _, _ = ssh_shell(
            ssh, f"cd {PASARGUARD_DIR} && docker compose exec -T timescaledb pg_isready -U pasarguard -d postgres")
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
    svc = " ".join(services) if services else ""
    print_info(f"Starting {label} containers{f' ({svc})' if svc else ''}...")
    if not run_command(f"docker compose up -d {svc}".strip(), cwd=d):
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
    svc = " ".join(services) if services else ""
    print_info(f"Starting {label} containers{f' ({svc})' if svc else ''}...")
    ec, _, er = ssh_shell(ssh, f"cd {d} && docker compose up -d {svc}".strip())
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
def send_telegram_file(token, chat_id, file_path, caption=""):
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    boundary = f"----WKF{uuid.uuid4().hex}"
    if not os.path.exists(file_path):
        return False, "File not found"
    try:
        with open(file_path, "rb") as f:
            fc = f.read()
    except Exception as e:
        return False, str(e)
    fn = os.path.basename(file_path)
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
    req = urllib.request.Request(url, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Content-Length", str(len(body)))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return True, r.read().decode()
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode()}"
    except Exception as e:
        return False, str(e)

# ── Backup creation ──────────────────────────────────────────
def ask_backup_scope():
    """Ask user whether to back up only PasarGuard or both."""
    print()
    print(f"  {C.CYAN}What do you want to back up?{C.RESET}")
    print()
    print(f"  {C.WHITE}1{C.RESET}{C.DKGRAY} ─{C.RESET}  {C.GREEN}PasarGuard only{C.RESET}  {C.DKGRAY}(/opt/pasarguard + DB + /var/lib/pasarguard){C.RESET}")
    print(f"  {C.WHITE}2{C.RESET}{C.DKGRAY} ─{C.RESET}  {C.YELLOW}PasarGuard + PG-Node{C.RESET}  {C.DKGRAY}(everything above + /opt/pg-node + /var/lib/pg-node){C.RESET}")
    print()
    while True:
        choice = input(f"  {C.CYAN}▸  Enter 1 or 2: {C.RESET}").strip()
        if choice in ("1", "2"):
            return choice == "2"
        print_error("Invalid choice. Enter 1 or 2.")

def create_backup(include_node=True):
    scope_label = "PasarGuard + PG-Node" if include_node else "PasarGuard only"
    print_info(f"Starting backup — scope: {C.BOLD}{scope_label}{C.RESET}")

    ts         = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    scope_tag  = "full" if include_node else "pg"
    backup_name = f"backup_{scope_tag}_{ts}"
    tmp_dir     = f"/tmp/{backup_name}"
    final_base  = os.path.join(os.getcwd(), backup_name)
    zip_path    = f"{final_base}.zip"

    pg_dump_dir       = os.path.join(tmp_dir, "pg_dump")
    pg_data_dest      = os.path.join(tmp_dir, "pasarguard_data")
    node_opt_dest     = os.path.join(tmp_dir, "pg_node_opt")
    node_data_dest    = os.path.join(tmp_dir, "pg_node_data")

    try:
        os.makedirs(pg_dump_dir, exist_ok=True)

        print_info("Copying PasarGuard config files...")
        for fn in ("docker-compose.yml", ".env"):
            src = os.path.join(PASARGUARD_DIR, fn)
            if os.path.exists(src):
                shutil.copy(src, tmp_dir)

        print_info("Exporting PostgreSQL globals...")
        run_command("docker compose exec -T timescaledb pg_dumpall -U pasarguard --globals-only",
                    output_file=os.path.join(pg_dump_dir, "globals.sql"), cwd=PASARGUARD_DIR)

        print_info("Exporting PasarGuard database... (may take a while)")
        run_command("docker compose exec -T timescaledb pg_dump -U pasarguard -d pasarguard",
                    output_file=os.path.join(pg_dump_dir, "db-001.sql"), cwd=PASARGUARD_DIR)

        print_info("Writing manifest...")
        with open(os.path.join(pg_dump_dir, "manifest.tsv"), "w") as f:
            f.write("pasarguard\tpasarguard\t1\tdb-001.sql\t2.28.1\n")

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
        shutil.make_archive(final_base, "zip", tmp_dir)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print_success(f"Archive: {zip_path}")
        return zip_path

    except Exception as e:
        print_error(f"Backup failed: {e}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

# ── Workflow 1: Auto transfer to new server ──────────────────
def workflow_transfer():
    print_header("Auto Backup & Transfer to New Server")

    include_node = ask_backup_scope()
    zip_path = create_backup(include_node)
    if not zip_path or not os.path.exists(zip_path):
        print_error("Aborting — backup failed.")
        return

    print()
    send_tg = input(f"{C.CYAN}🤖 Send backup to Telegram first? (y/n): {C.RESET}").strip().lower()
    if send_tg == "y":
        bot_token = input(f"  {C.CYAN}Bot Token: {C.RESET}").strip()
        admin_id  = input(f"  {C.CYAN}Admin Chat ID: {C.RESET}").strip()
        print_info("Uploading to Telegram...")
        cap = (f"📦 PasarGuard{'+ PG-Node' if include_node else ''} Manual Transfer Backup\n"
               f"🕒 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        success, details = send_telegram_file(bot_token, admin_id, zip_path, cap)
        if success: print_success("Sent to Telegram!")
        else:       print_error(f"Telegram upload failed: {details}")

    print(f"\n{C.BOLD}--- New Server Information ---{C.RESET}")
    new_ip   = input(f"  {C.CYAN}New Server IP: {C.RESET}").strip()
    confirm  = input(f"  {C.YELLOW}User MUST be root. Confirm? (y/n): {C.RESET}").strip().lower()
    if confirm != "y":
        print_error("Root access required. Aborting.")
        return
    new_pass = input(f"  {C.CYAN}Root Password: {C.RESET}").strip()

    print_info(f"Connecting to {new_ip}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(hostname=new_ip, username="root", password=new_pass, timeout=10)
        print_success("Connected!")
        print()

        execute_ssh_command(ssh, "apt-get update >/dev/null 2>&1 && apt-get install -y unzip >/dev/null 2>&1",
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

        print(f"  {C.CYAN}🌐{C.RESET}  Uploading backup file (depends on internet speed)...")
        sftp = ssh.open_sftp()
        zip_fn         = os.path.basename(zip_path)
        remote_zip     = f"/opt/pasarguard/{zip_fn}"
        sftp.put(zip_path, remote_zip)
        sftp.close()
        print_success("Upload completed.")

        execute_ssh_command(ssh, f"cd /opt/pasarguard && unzip -q -o {zip_fn}",
                            "Extracting files")

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

        if not start_compose_ssh(ssh, PASARGUARD_DIR, "Pasarguard DB",
                                  services=["timescaledb"], wait_postgres=True):
            print_error("timescaledb did not start. Aborting.")
            return

        execute_ssh_command(ssh,
            'cd /opt/pasarguard && docker compose exec -T timescaledb psql -U pasarguard -d postgres '
            '-c "DROP DATABASE IF EXISTS pasarguard WITH (FORCE);"',
            "Dropping old database")

        execute_ssh_command(ssh,
            'cd /opt/pasarguard && docker compose exec -T timescaledb psql -U pasarguard -d postgres '
            '-c "CREATE DATABASE pasarguard;"',
            "Creating fresh database")

        execute_ssh_command(ssh,
            "cd /opt/pasarguard && cat pg_dump/globals.sql | docker compose exec -T timescaledb psql -U pasarguard",
            "Restoring globals.sql")

        execute_ssh_command(ssh,
            "cd /opt/pasarguard && cat pg_dump/db-001.sql | docker compose exec -T timescaledb psql -U pasarguard -d pasarguard",
            "Restoring db-001.sql (may take a while for large DBs)")

        if not start_compose_ssh(ssh, PASARGUARD_DIR, "Pasarguard"):
            print_error("Pasarguard did not start. Aborting.")
            return

        if include_node and not start_compose_ssh(ssh, PG_NODE_DIR, "PG-Node"):
            print_error("PG-Node did not start.")

        print_header("Transfer & Restore Completed Successfully! 🎉")
        print_success("PasarGuard" + (" and PG-Node are" if include_node else " is") + " running on the new server.")

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
def workflow_backup_bot():
    print_header("Auto Backup to Telegram Bot (Scheduled)")

    include_node = ask_backup_scope()

    bot_token = input(f"  {C.CYAN}Bot Token: {C.RESET}").strip()
    while not bot_token:
        bot_token = input(f"  {C.RED}Cannot be empty!{C.RESET} {C.CYAN}Bot Token: {C.RESET}").strip()

    admin_id = input(f"  {C.CYAN}Admin Chat ID (numeric): {C.RESET}").strip()
    while not admin_id or not admin_id.lstrip("-").isdigit():
        admin_id = input(f"  {C.RED}Invalid!{C.RESET} {C.CYAN}Admin Chat ID: {C.RESET}").strip()

    try:
        interval_h = float(input(f"  {C.CYAN}Interval in hours (e.g. 1, 0.5): {C.RESET}").strip())
    except ValueError:
        print_warning("Invalid number. Defaulting to 1.0 hour.")
        interval_h = 1.0

    interval_s = int(interval_h * 3600)
    scope_label = "PasarGuard + PG-Node" if include_node else "PasarGuard only"
    print_info(f"Scheduler started — scope: {C.BOLD}{scope_label}{C.RESET}, every {interval_h}h.")
    print_warning("Press Ctrl+C to stop.")
    print(hline())

    try:
        while True:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n{C.BOLD}⏳ Starting scheduled backup at {now_str}...{C.RESET}")

            zip_path = create_backup(include_node)
            if zip_path and os.path.exists(zip_path):
                print_info("Uploading to Telegram...")
                cap = (f"🤖 PasarGuard{'+ PG-Node' if include_node else ''} Auto Backup\n"
                       f"🕒 {now_str}\n⏱ Interval: {interval_h}h")
                success, details = send_telegram_file(bot_token, admin_id, zip_path, cap)
                if success: print_success("Backup sent to Telegram!")
                else:       print_error(f"Send failed: {details}")
                try:
                    os.remove(zip_path)
                    print_info("Local archive removed.")
                except Exception:
                    pass
            else:
                print_error("Backup failed — skipping upload.")

            print_info(f"Sleeping {interval_h}h...")
            time.sleep(interval_s)

    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}🚪 Scheduler stopped.{C.RESET}")

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
def workflow_manual_restore():
    print_header("Manual Restore (Local)")

    include_node = ask_backup_scope()
    scope_label  = "PasarGuard + PG-Node" if include_node else "PasarGuard only"
    print_info(f"Scope: {C.BOLD}{scope_label}{C.RESET}")

    zip_name = input(f"  {C.CYAN}Backup ZIP filename (e.g. backup_full_20260101.zip): {C.RESET}").strip()
    if not os.path.exists(zip_name):
        print_error(f"File '{zip_name}' not found in current directory.")
        return

    confirm = input(
        f"  {C.RED}⚠️  This will overwrite current config and database. Continue? (y/n): {C.RESET}"
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
        if not run_command(f"unzip -q -o {zip_name} -d /opt/pasarguard"):
            print_error("Extraction failed.")
            return

        print_info("Restoring PasarGuard data...")
        run_command("cp -a /opt/pasarguard/pasarguard_data/. /var/lib/pasarguard/ 2>/dev/null || true")
        run_command("rm -rf /opt/pasarguard/pasarguard_data")

        if include_node:
            print_info("Restoring PG-Node config and data...")
            run_command("cp -a /opt/pasarguard/pg_node_opt/. /opt/pg-node/ 2>/dev/null || true")
            run_command("cp -a /opt/pasarguard/pg_node_data/. /var/lib/pg-node/ 2>/dev/null || true")
            run_command("rm -rf /opt/pasarguard/pg_node_opt /opt/pasarguard/pg_node_data")

        if not start_compose_local(PASARGUARD_DIR, "Pasarguard DB",
                                    services=["timescaledb"], wait_postgres=True):
            raise Exception("timescaledb did not start")

        print_info("Dropping old database...")
        if not run_command('cd /opt/pasarguard && docker compose exec -T timescaledb psql -U pasarguard -d postgres '
                           '-c "DROP DATABASE IF EXISTS pasarguard WITH (FORCE);"'):
            raise Exception("Failed to drop old database")

        print_info("Creating fresh database...")
        if not run_command('cd /opt/pasarguard && docker compose exec -T timescaledb psql -U pasarguard -d postgres '
                           '-c "CREATE DATABASE pasarguard;"'):
            raise Exception("Failed to create database")

        print_info("Restoring globals.sql...")
        if not run_command("cd /opt/pasarguard && cat pg_dump/globals.sql | docker compose exec -T timescaledb psql -U pasarguard"):
            raise Exception("Failed to restore globals.sql")

        print_info("Restoring db-001.sql (may take a while)...")
        if not run_command("cd /opt/pasarguard && cat pg_dump/db-001.sql | docker compose exec -T timescaledb psql -U pasarguard -d pasarguard"):
            raise Exception("Failed to restore db-001.sql")

        if not start_compose_local(PASARGUARD_DIR, "Pasarguard"):
            raise Exception("Pasarguard did not start")

        if include_node and not start_compose_local(PG_NODE_DIR, "PG-Node"):
            print_error("PG-Node did not start.")

        print_header("Local Restore Completed Successfully! 🎉")
        print_success("PasarGuard" + (" and PG-Node are" if include_node else " is") + " running.")

    except Exception as e:
        print_error(f"Restore error: {e}")
        print_warning("System may be in a partially restored state.")

# ── Main menu ─────────────────────────────────────────────────
MENU = [
    ("1", "🚀", "Auto Backup & Transfer to New Server"),
    ("2", "🤖", "Auto Backup to Telegram Bot (Scheduled)"),
    ("3", "💾", "Manual Backup (Save locally)"),
    ("4", "🔄", "Manual Restore (From local zip)"),
    ("5", "🚪", "Exit"),
]

def main():
    while True:
        print_header()

        print(f"  {C.DKGRAY}{'─'*50}{C.RESET}")
        for num, icon, label in MENU:
            num_col = C.RED if num == "5" else C.WHITE
            print(f"  {num_col}{num}{C.RESET}{C.DKGRAY} ─{C.RESET}  {icon}  {C.WHITE}{label}{C.RESET}")
        print(f"  {C.DKGRAY}{'─'*50}{C.RESET}")
        print()

        choice = input(f"  {C.CYAN}▸  Select option (1-5): {C.RESET}").strip()
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
            print(f"  {C.YELLOW}🚪 Goodbye!{C.RESET}\n")
            sys.exit(0)
        else:
            print_error("Invalid option. Please enter 1-5.")
            time.sleep(1.5)

if __name__ == "__main__":
    main()
