"""Migration engine: read -> plan (dry-run) -> backup -> apply -> verify."""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .dbio import BaseDB, DBError, SqliteDB
from .readers import detect_kind, read_source
from .writers import MigrationOptions, Plan, get_writer

SOURCE_KINDS = ("marzban", "marzneshin", "rebecca", "3xui", "pasarguard")
ALLOWED = {  # source -> targets
    "marzban": ("pasarguard",),
    "marzneshin": ("pasarguard",),
    "rebecca": ("pasarguard",),
    "3xui": ("pasarguard",),
    "pasarguard": ("pasarguard", "marzban"),
}


@dataclass
class Report:
    source_kind: str
    target_kind: str
    applied: bool = False
    source_users: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: List[Any] = field(default_factory=list)
    renamed: List[Any] = field(default_factory=list)
    admins_created: List[str] = field(default_factory=list)
    groups_created: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    backup_path: Optional[str] = None
    statements: int = 0
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.errors and not self.problems

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["ok"] = self.ok
        return d


def _report(src_kind, tgt_kind, plan: Plan, n_src: int) -> Report:
    return Report(source_kind=src_kind, target_kind=tgt_kind, source_users=n_src,
                  inserted=plan.inserts, updated=plan.updates, skipped=list(plan.skipped),
                  renamed=list(plan.renamed), admins_created=[a.username for a in plan.admins_new],
                  groups_created=[g for g, _ in plan.groups_new], warnings=list(plan.warnings),
                  errors=list(plan.errors))


def run(source: BaseDB, target: BaseDB, target_kind: str = "pasarguard", *,
        source_kind: Optional[str] = None, opts: Optional[MigrationOptions] = None,
        apply: bool = False, backup: Optional[Callable[[], Optional[str]]] = None,
        log: Callable[[str], None] = lambda m: None) -> Report:
    """Convert ``source`` into ``target``. With ``apply=False`` nothing is written."""
    t0 = time.time()
    opts = opts or MigrationOptions()
    kind = source_kind or detect_kind(source)
    if not kind:
        r = Report("unknown", target_kind)
        r.errors.append("Could not recognise the source database (not Marzban/Marzneshin/Rebecca/3x-ui/PasarGuard).")
        return r
    if target_kind not in ALLOWED.get(kind, ()):
        r = Report(kind, target_kind)
        r.errors.append(f"Conversion {kind} -> {target_kind} is not supported.")
        return r
    log(f"reading source ({kind})")
    src = read_source(source, kind, auth_algorithm=opts.auth_algorithm)
    log(f"source: {len(src.users)} users, {len(src.admins)} admins")
    writer = get_writer(target_kind, target, opts)
    plan = writer.plan(src)
    for side, db in (("source", source), ("target", target)):
        note = getattr(db, "credentials_note", "")
        if note:
            plan.warn(f"{side}: {note}")
    rep = _report(kind, target_kind, plan, len(src.users))
    if plan.errors or not apply:
        rep.seconds = time.time() - t0
        return rep
    stmts = writer.statements(plan)
    rep.statements = len(stmts)
    if backup:
        log("backing up target")
        try:
            rep.backup_path = backup()
        except Exception as e:  # never write without the safety net
            rep.errors.append(f"Backup of the target failed, nothing was written: {e}")
            return rep
    before = writer.counts()
    log(f"applying {len(stmts)} statements")
    try:
        target.execute(stmts)
    except DBError as e:
        rep.errors.append(str(e))
        return rep
    rep.applied = True
    rep.problems = writer.verify(plan, before)
    rep.seconds = time.time() - t0
    return rep


def sqlite_file_backup(path: str, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{os.path.basename(path)}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(path, dest)
    os.chmod(dest, 0o600)
    return dest
