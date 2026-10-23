"""python -m pgb.panel <run|setup|reset-password|reset-2fa|show-path|rotate-path>"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import security as sec


def _store():
    from .store import Store
    return Store(os.environ.get("PGB_STATE_DIR", "/var/lib/pg-backup-panel"))


def _ask_password(args) -> str:
    pw = os.environ.get("PGB_NEW_PASSWORD") or (getpass.getpass("New password: ") if sys.stdin.isatty() else sys.stdin.readline().strip())
    bad = sec.check_password_strength(pw)
    if bad:
        sys.exit(bad)
    return pw


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="pgb.panel")
    ap.add_argument("cmd", choices=["run", "setup", "reset-password", "reset-2fa", "show-path", "rotate-path"])
    ap.add_argument("--username", default="admin")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        import uvicorn
        uvicorn.run("pgb.panel.app:app_from_env", factory=True, host=a.host, port=a.port, log_level="warning",
                    proxy_headers=True, forwarded_allow_ips="127.0.0.1", server_header=False)
        return
    st = _store()
    if a.cmd == "setup":
        if st.get("admin"):
            sys.exit("Panel is already set up. Use reset-password to change the credentials.")
        st.set("admin", {"username": a.username, "hash": sec.hash_password(_ask_password(a)), "sv": 0})
        if not st.get("secret_path"):
            st.set("secret_path", sec.random_path())
        print(st.get("secret_path"))
    elif a.cmd == "reset-password":
        adm = st.get("admin") or {"username": a.username, "sv": 0}
        adm["hash"] = sec.hash_password(_ask_password(a))
        adm["sv"] = adm.get("sv", 0) + 1
        st.set("admin", adm)
        print("password updated; all sessions were signed out")
    elif a.cmd == "reset-2fa":
        adm = st.get("admin")
        if not adm:
            sys.exit("not set up")
        adm.pop("totp", None)
        adm["recovery"] = []
        adm["sv"] = adm.get("sv", 0) + 1
        st.set("admin", adm)
        print("2FA disabled")
    elif a.cmd == "show-path":
        print(st.get("secret_path") or "")
    elif a.cmd == "rotate-path":
        st.set("secret_path", sec.random_path())
        print(st.get("secret_path"))


if __name__ == "__main__":
    main()
