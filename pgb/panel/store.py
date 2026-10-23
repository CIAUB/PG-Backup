"""Panel state: SQLite file under a private directory + Fernet-encrypted secrets."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken


class Store:
    def __init__(self, state_dir: str):
        self.dir = state_dir
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        os.chmod(state_dir, 0o700)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(os.path.join(state_dir, "panel.db"), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        os.chmod(os.path.join(state_dir, "panel.db"), 0o600)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, title TEXT, status TEXT,
            started REAL, finished REAL, exit_code INTEGER, log TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, user TEXT, ip TEXT, event TEXT, detail TEXT);
        """)
        kp = os.path.join(state_dir, "master.key")
        if not os.path.exists(kp):
            fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
        self._f = Fernet(open(kp, "rb").read().strip())

    # kv
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            r = self.db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(r["v"]) if r else default

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                            (key, json.dumps(value)))

    def delete(self, key: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM kv WHERE k=?", (key,))

    # secrets
    def set_secret(self, key: str, value: str) -> None:
        self.set("secret:" + key, self._f.encrypt(value.encode()).decode())

    def get_secret(self, key: str) -> Optional[str]:
        tok = self.get("secret:" + key)
        if not tok:
            return None
        try:
            return self._f.decrypt(tok.encode()).decode()
        except InvalidToken:
            return None

    def signing_key(self) -> bytes:
        k = self.get_secret("session_key")
        if not k:
            import secrets
            k = secrets.token_hex(32)
            self.set_secret("session_key", k)
        return k.encode()

    # audit
    def audit(self, user: str, ip: str, event: str, detail: str = "") -> None:
        with self._lock:
            self.db.execute("INSERT INTO audit(ts,user,ip,event,detail) VALUES(?,?,?,?,?)",
                            (time.time(), user, ip, event, detail[:500]))
            self.db.execute("DELETE FROM audit WHERE id <= (SELECT MAX(id) FROM audit) - 2000")

    def audit_tail(self, n: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (n,))]

    # jobs
    def job_new(self, kind: str, title: str) -> int:
        with self._lock:
            return self.db.execute("INSERT INTO jobs(kind,title,status,started) VALUES(?,?,?,?)",
                                   (kind, title, "running", time.time())).lastrowid

    def job_append(self, jid: int, text: str) -> None:
        with self._lock:
            self.db.execute("UPDATE jobs SET log = substr(log || ?, -200000) WHERE id=?", (text, jid))

    def job_finish(self, jid: int, code: int) -> None:
        with self._lock:
            self.db.execute("UPDATE jobs SET status=?, exit_code=?, finished=? WHERE id=?",
                            ("done" if code == 0 else "failed", code, time.time(), jid))

    def job_get(self, jid: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            r = self.db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return dict(r) if r else None

    def jobs(self, n: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.db.execute(
                "SELECT id,kind,title,status,started,finished,exit_code FROM jobs ORDER BY id DESC LIMIT ?", (n,))]

    def fail_stale_jobs(self) -> None:
        with self._lock:
            self.db.execute("UPDATE jobs SET status='failed', finished=? WHERE status='running'", (time.time(),))
