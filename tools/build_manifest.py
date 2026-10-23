#!/usr/bin/env python3
"""Release helper: regenerate pgb-manifest.json and embed its SHA-256 in pg_backup.py.

Run from the repository root after changing anything under pgb/:
    python3 tools/build_manifest.py
Commit pgb-manifest.json and pg_backup.py together.
"""
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__"}


def main() -> int:
    files = {}
    for dp, dn, fns in os.walk(os.path.join(ROOT, "pgb")):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
        for fn in sorted(fns):
            if fn.endswith((".pyc", ".new")):
                continue
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            files[rel] = hashlib.sha256(open(full, "rb").read()).hexdigest()
    src = open(os.path.join(ROOT, "pg_backup.py"), encoding="utf-8").read()
    ver = re.search(r'^PGB_VERSION = "([^"]+)"', src, re.M).group(1)
    raw = (json.dumps({"version": ver, "files": files}, indent=1, sort_keys=True) + "\n").encode()
    open(os.path.join(ROOT, "pgb-manifest.json"), "wb").write(raw)
    digest = hashlib.sha256(raw).hexdigest()
    new = re.sub(r'^PGB_MANIFEST_SHA256 = "[0-9a-f]{64}"', f'PGB_MANIFEST_SHA256 = "{digest}"', src, flags=re.M)
    open(os.path.join(ROOT, "pg_backup.py"), "w", encoding="utf-8").write(new)
    print(f"manifest: {len(files)} files, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
