"""Terminal front-end for panel migration (used by `PG-Backup --cli migrate` and the menu).

Non-interactive example:
  migrate --from marzban --to pasarguard --dry-run
  migrate --from 3xui:/etc/x-ui/x-ui.db --to pasarguard --apply --yes --conflict rename
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from .discover import KNOWN, backup_target, discover, open_panel, restart_panel
from .engine import ALLOWED, run
from .writers import MigrationOptions


def _spec(text: str):
    kind, _, path = text.partition(":")
    if kind not in KNOWN:
        raise SystemExit(f"unknown panel '{kind}' (choose from: {', '.join(KNOWN)})")
    return kind, (path or None)


def _print_report(rep) -> None:
    print(f"\n  {rep.source_kind} -> {rep.target_kind}")
    print(f"  source users : {rep.source_users}")
    print(f"  will insert  : {rep.inserted}   update: {rep.updated}   skip: {len(rep.skipped)}")
    if rep.admins_created:
        print(f"  new admins   : {', '.join(rep.admins_created)}")
    if rep.groups_created:
        print(f"  new groups   : {', '.join(rep.groups_created)}")
    for r in rep.renamed[:10]:
        print(f"  renamed      : {r[0]} -> {r[1]}")
    for s in rep.skipped[:10]:
        print(f"  skipped      : {s[0]} ({s[1]})")
    for w in rep.warnings:
        print(f"  [!] {w}")
    for e in rep.errors:
        print(f"  [ERROR] {e}")
    for p in rep.problems:
        print(f"  [VERIFY FAILED] {p}")
    if rep.backup_path:
        print(f"  target backup: {rep.backup_path}")
    if rep.applied:
        print("  APPLIED" + ("" if rep.ok else " (with verification problems)"))


def main(argv=None, backup_dir: str = "/var/backups/pg-backup/pre-migration") -> int:
    ap = argparse.ArgumentParser(prog="migrate")
    ap.add_argument("--from", dest="src", help="kind[:sqlite_path]  e.g. marzban, 3xui:/etc/x-ui/x-ui.db")
    ap.add_argument("--to", dest="dst", help="kind[:sqlite_path]  (pasarguard or marzban)")
    ap.add_argument("--apply", action="store_true", help="write to the target (default is a dry-run)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--conflict", default="skip", choices=["skip", "rename", "update"])
    ap.add_argument("--admin-mode", default="map", choices=["map", "single", "none"])
    ap.add_argument("--target-admin")
    ap.add_argument("--group-mode", default="by_source", choices=["by_source", "auto", "none"])
    ap.add_argument("--group-name", default="migrated")
    ap.add_argument("--no-usage", action="store_true", help="do not copy used traffic")
    ap.add_argument("--auth-algorithm", default="xxh128", choices=["xxh128", "plain"])
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)

    interactive = not (a.src and a.dst)
    if interactive:
        found = discover()
        print("\n  Panels found on this server:")
        for i, f in enumerate(found, 1):
            print(f"   {i}. {f['label']:<14} {f['path'] or f['dir']}  ({f['engine']})")
        if not found:
            print("   (none) — pass --from/--to with a path, e.g. marzban:/path/db.sqlite3")
            return 1
        try:
            si = int(input("\n  Source number: ")) - 1
            ti = int(input("  Target number: ")) - 1
            src_f, dst_f = found[si], found[ti]
        except (ValueError, IndexError):
            print("  invalid choice")
            return 1
        a.src, a.dst = src_f["kind"] + (":" + src_f["path"] if src_f["path"] else ""), \
            dst_f["kind"] + (":" + dst_f["path"] if dst_f["path"] else "")
        c = input("  If a username already exists [skip/rename/update] (skip): ").strip() or "skip"
        a.conflict = c if c in ("skip", "rename", "update") else "skip"

    skind, spath = _spec(a.src)
    tkind, tpath = _spec(a.dst)
    if tkind not in ALLOWED.get(skind, ()):
        print(f"  conversion {skind} -> {tkind} is not supported")
        return 2
    opts = MigrationOptions(conflict=a.conflict, admin_mode=a.admin_mode, target_admin=a.target_admin,
                            group_mode=a.group_mode, group_name=a.group_name, preserve_usage=not a.no_usage,
                            auth_algorithm=a.auth_algorithm)
    try:
        opts.validate()
        src = open_panel(skind, None, spath)
        dst_dir = next((d for d in KNOWN[tkind]["dirs"] if os.path.isdir(d)), None)
        want_apply = a.apply and not a.dry_run
        tgt = open_panel(tkind, dst_dir, tpath, writable=want_apply)
    except Exception as e:
        print(f"  [ERROR] {e}")
        return 2
    try:
        rep = run(src, tgt, tkind, source_kind=skind, opts=opts, apply=False)
        if a.json:
            print(json.dumps(rep.to_dict(), ensure_ascii=False, default=str))
        else:
            _print_report(rep)
        if rep.errors or not want_apply:
            if not want_apply and not rep.errors:
                print("\n  Dry-run only — nothing was written. Re-run with --apply to migrate.")
            return 1 if rep.errors else 0
        if not a.yes and input("\n  Type MIGRATE to write these changes: ").strip() != "MIGRATE":
            print("  aborted")
            return 1
        rep = run(src, tgt, tkind, source_kind=skind, opts=opts, apply=True,
                  backup=lambda: backup_target(tgt, backup_dir), log=lambda m: print("  " + m))
        if not a.json:
            _print_report(rep)
        if rep.applied and not a.no_restart:
            print("  " + restart_panel(tkind, dst_dir))
        return 0 if rep.ok else 1
    finally:
        src.close()
        tgt.close()


if __name__ == "__main__":
    sys.exit(main())
