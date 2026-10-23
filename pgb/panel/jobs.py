"""Run PG-Backup CLI commands as background jobs with a streamed log."""
from __future__ import annotations

import os
import re
import subprocess
import threading
from typing import Dict, List, Optional

from .store import Store

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class JobRunner:
    def __init__(self, store: Store):
        self.store = store
        self._procs: Dict[int, subprocess.Popen] = {}
        self._lock = threading.Lock()
        store.fail_stale_jobs()

    def busy(self, kind: Optional[str] = None) -> bool:
        with self._lock:
            return any(p.poll() is None for p in self._procs.values())

    def start(self, kind: str, title: str, argv: List[str], env: Optional[Dict[str, str]] = None,
              cwd: Optional[str] = None, redact: Optional[List[str]] = None) -> int:
        jid = self.store.job_new(kind, title)
        full_env = dict(os.environ, PYTHONUNBUFFERED="1", NO_COLOR="1", TERM="dumb")
        full_env.update(env or {})
        try:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 env=full_env, cwd=cwd, bufsize=0)
        except OSError as e:
            self.store.job_append(jid, f"cannot start: {e}\n")
            self.store.job_finish(jid, 127)
            return jid
        with self._lock:
            self._procs[jid] = p
        secrets_ = [s for s in (redact or []) if s]

        def pump():
            buf = b""
            while True:
                chunk = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    self.store.job_append(jid, self._clean(ln, secrets_) + "\n")
            if buf:
                self.store.job_append(jid, self._clean(buf, secrets_) + "\n")
            code = p.wait()
            self.store.job_finish(jid, code)
            with self._lock:
                self._procs.pop(jid, None)

        threading.Thread(target=pump, daemon=True).start()
        return jid

    def run_callable(self, kind: str, title: str, fn) -> int:
        """Run ``fn(log)`` in a thread; log(str) appends a line. Return value 0/None = success."""
        jid = self.store.job_new(kind, title)

        def work():
            code = 0
            try:
                code = fn(lambda m: self.store.job_append(jid, str(m) + "\n")) or 0
            except Exception as e:  # surfaced in the log, never crashes the panel
                self.store.job_append(jid, f"ERROR: {e}\n")
                code = 1
            self.store.job_finish(jid, code)

        threading.Thread(target=work, daemon=True).start()
        return jid

    def cancel(self, jid: int) -> bool:
        with self._lock:
            p = self._procs.get(jid)
        if p and p.poll() is None:
            p.terminate()
            return True
        return False

    @staticmethod
    def _clean(raw: bytes, secrets_: List[str]) -> str:
        s = _ANSI.sub("", raw.decode("utf-8", "replace")).replace("\r", "")
        for sec in secrets_:
            s = s.replace(sec, "***")
        return s
