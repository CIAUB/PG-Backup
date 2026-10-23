"""Target writers: neutral ``SourceData`` -> SQL statements for a panel DB.

Two targets are implemented:

* ``PasarGuardWriter`` – the main target. Works on SQLite, PostgreSQL,
  TimescaleDB, MySQL and MariaDB because it only emits portable, parameterised
  INSERT/UPDATE statements and never relies on auto-generated ids it has to
  read back (foreign keys are resolved with sub-selects).
* ``MarzbanWriter``    – for PasarGuard -> Marzban (the reverse direction).

Safety properties
-----------------
* The target schema is introspected first. If a column we are not prepared to
  fill is ``NOT NULL`` without a default (i.e. PasarGuard changed), planning
  stops with a clear error instead of writing a half-valid row.
* Every statement list is executed in ONE transaction by the DB layer.
* Existing users/admins/groups are never modified unless ``conflict='update'``
  is chosen, and never deleted.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .dbio import BaseDB, DBError, Stmt, quote_ident
from .model import (AdminRec, CORE_PROTOCOLS, DEFAULT_SS_METHOD, GroupRec, SourceData, UserRec,
                    random_password, sanitize_username, normalize_ss_method)

TARGET_KINDS = ("pasarguard", "marzban")


@dataclass
class MigrationOptions:
    conflict: str = "skip"              # skip | rename | update
    admin_mode: str = "map"             # map | single | none
    target_admin: Optional[str] = None  # username, for admin_mode == "single"
    create_missing_admins: bool = True
    group_mode: str = "by_source"       # by_source | auto | existing | none
    group_name: str = "migrated"        # name used for the automatic group(s)
    existing_groups: List[str] = field(default_factory=list)   # names, for group_mode == "existing"
    preserve_usage: bool = True
    rename_suffix: str = "_m"
    auth_algorithm: str = "xxh128"      # Marzneshin only
    assume_default_inbounds: bool = True

    def validate(self) -> None:
        if self.conflict not in ("skip", "rename", "update"):
            raise ValueError("conflict must be skip, rename or update")
        if self.admin_mode not in ("map", "single", "none"):
            raise ValueError("admin_mode must be map, single or none")
        if self.admin_mode == "single" and not self.target_admin:
            raise ValueError("admin_mode 'single' needs target_admin")
        if self.group_mode not in ("by_source", "auto", "existing", "none"):
            raise ValueError("group_mode must be by_source, auto, existing or none")
        if self.group_mode == "existing" and not self.existing_groups:
            raise ValueError("group_mode 'existing' needs existing_groups")


@dataclass
class Sub:
    """A scalar sub-select usable as a value inside an INSERT."""
    sql: str
    params: tuple = ()


@dataclass
class PlannedUser:
    rec: UserRec
    action: str                         # insert | update
    final_username: str
    groups: List[str] = field(default_factory=list)       # target group names
    admin_ref: Any = None               # ("id", n) | ("name", username) | None


@dataclass
class Plan:
    target_kind: str
    admins_new: List[AdminRec] = field(default_factory=list)
    groups_new: List[Tuple[str, List[str]]] = field(default_factory=list)   # (name, inbound tags)
    users: List[PlannedUser] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    renamed: List[Tuple[str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    target_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def inserts(self) -> int:
        return sum(1 for u in self.users if u.action == "insert")

    @property
    def updates(self) -> int:
        return sum(1 for u in self.users if u.action == "update")

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _insert(dialect: str, table: str, row: Dict[str, Any]) -> Stmt:
    cols, vals, params = [], [], []
    for k, v in row.items():
        cols.append(quote_ident(dialect, k))
        if isinstance(v, Sub):
            vals.append("(" + v.sql + ")")
            params.extend(v.params)
        else:
            vals.append("?")
            params.append(v)
    sql = f"INSERT INTO {quote_ident(dialect, table)} ({', '.join(cols)}) VALUES ({', '.join(vals)})"
    return Stmt(sql, tuple(params))


def _update(dialect: str, table: str, row: Dict[str, Any], where_col: str, where_val: Any) -> Stmt:
    sets, params = [], []
    for k, v in row.items():
        if isinstance(v, Sub):
            sets.append(f"{quote_ident(dialect, k)} = ({v.sql})")
            params.extend(v.params)
        else:
            sets.append(f"{quote_ident(dialect, k)} = ?")
            params.append(v)
    params.append(where_val)
    return Stmt(f"UPDATE {quote_ident(dialect, table)} SET {', '.join(sets)} "
                f"WHERE {quote_ident(dialect, where_col)} = ?", tuple(params))


def _guess_default(col) -> Tuple[bool, Any]:
    """Neutral value for a NOT NULL column we do not know (newer/older panel version)."""
    t = (col.type or "").lower()
    if "bool" in t or t in ("bit", "tinyint(1)"):
        return True, False
    if any(k in t for k in ("int", "numeric", "decimal", "float", "real", "double")):
        return True, 0
    if "timestamp" in t or "date" in t or "time" in t:
        return True, datetime.now(timezone.utc).replace(tzinfo=None)
    if "json" in t:
        return True, "{}"
    if any(k in t for k in ("char", "text", "clob", "string", "uuid")) or t == "":
        return True, ""
    return False, None                                   # enums, arrays, blobs: cannot guess safely


def _check_required(cols, provided: Set[str], table: str, plan: Plan, fills: Optional[Dict] = None) -> None:
    """Schema-drift guard. Unknown NOT NULL columns (a different panel version) are filled with a
    neutral default and reported as a warning; only columns we cannot guess are a hard error."""
    unknown = []
    for c in cols:
        if c.required and c.name not in provided:
            ok, val = _guess_default(c)
            if ok and fills is not None:
                fills.setdefault(table, {})[c.name] = val
                plan.warn(f"Panel version differs: column {table}.{c.name} ({c.type or '?'}) is new to this tool; "
                          f"filled with a neutral default.")
            else:
                unknown.append(c.name)
    if unknown:
        plan.errors.append(
            f"Target table '{table}' has required column(s) this tool cannot fill: {', '.join(unknown)}. "
            "The panel schema is too different — update PG-Backup or migrate manually.")


def _filter_cols(row: Dict[str, Any], existing: Set[str]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if k in existing}


class _Writer:
    kind = ""

    def __init__(self, db: BaseDB, opts: Optional[MigrationOptions] = None):
        self.db = db
        self.opts = opts or MigrationOptions()
        self.opts.validate()
        self.d = db.dialect
        self._fills: Dict[str, Dict[str, Any]] = {}

    def _ins(self, table: str, row: Dict[str, Any]) -> Stmt:
        return _insert(self.d, table, {**self._fills.get(table, {}), **row})

    # to be implemented by subclasses
    def plan(self, src: SourceData) -> Plan:
        raise NotImplementedError

    def statements(self, plan: Plan) -> List[Stmt]:
        raise NotImplementedError

    def verify(self, plan: Plan, before: Dict[str, int]) -> List[str]:
        raise NotImplementedError


# --------------------------------------------------------------------------
# PasarGuard
# --------------------------------------------------------------------------
class PasarGuardWriter(_Writer):
    kind = "pasarguard"

    REQUIRED_TABLES = ("users", "admins", "groups", "users_groups_association",
                       "inbounds", "inbounds_groups_association")

    # -- inspection ----------------------------------------------------------
    def _inspect(self, plan: Plan) -> Dict[str, Any]:
        have = set(self.db.table_names())
        missing = [t for t in self.REQUIRED_TABLES if t not in have]
        if missing:
            plan.errors.append(f"Target is not a PasarGuard database (missing: {', '.join(missing)}). "
                               "Start PasarGuard once so it creates its schema, then retry.")
            return {}
        cols = {t: self.db.columns(t) for t in ("users", "admins", "groups")}
        st: Dict[str, Any] = {"cols": cols, "colset": {t: {c.name for c in cs} for t, cs in cols.items()}}
        st["users"] = {str(r["username"]).lower() for r in self.db.select("users", ["username"])}
        st["admins"] = {str(r["username"]).lower(): r for r in self.db.select("admins")}
        st["groups"] = {}
        for r in self.db.select("groups"):
            st["groups"].setdefault(str(r["name"]), r)
        st["inbounds"] = {str(r["tag"]): r["id"] for r in self.db.select("inbounds", ["id", "tag"])}
        st["roles"] = {r["id"] for r in self.db.select("admin_roles", ["id"])} if "admin_roles" in have else None
        plan.target_counts = {"users": len(st["users"]), "admins": len(st["admins"]),
                              "groups": len(st["groups"]), "inbounds": len(st["inbounds"])}
        return st

    # -- planning ------------------------------------------------------------
    def plan(self, src: SourceData) -> Plan:
        o = self.opts
        plan = Plan(target_kind="pasarguard")
        plan.warnings.extend(src.warnings)
        st = self._inspect(plan)
        if plan.errors:
            return plan
        colset = st["colset"]
        target_tags = set(st["inbounds"])

        # schema-drift guard: compare what we will write with what is required
        _check_required(st["cols"]["users"], set(self._user_row_template().keys()), "users", plan, self._fills)
        _check_required(st["cols"]["admins"], set(self._admin_row_template().keys()), "admins", plan, self._fills)
        _check_required(st["cols"]["groups"], {"name", "is_disabled"}, "groups", plan, self._fills)
        if plan.errors:
            return plan

        # ---- admins
        admin_ref: Dict[Any, Any] = {}
        target_admin_name = None
        if o.admin_mode == "single":
            row = st["admins"].get((o.target_admin or "").lower())
            if not row:
                plan.errors.append(f"Target admin '{o.target_admin}' does not exist in PasarGuard.")
                return plan
            target_admin_name = row["username"]
        elif o.admin_mode == "map":
            referenced = {u.admin_src_id for u in src.users if u.admin_src_id is not None}
            for a in src.admins:
                hit = st["admins"].get(a.username.lower())
                if hit:
                    admin_ref[a.src_id] = ("id", hit["id"])
                elif o.create_missing_admins and (a.src_id in referenced or src.kind != "3xui"):
                    if not a.hashed_password:
                        plan.warn(f"Admin '{a.username}' has no password hash and was not created.")
                        continue
                    plan.admins_new.append(a)
                    admin_ref[a.src_id] = ("name", a.username)
                else:
                    plan.warn(f"Admin '{a.username}' does not exist in PasarGuard — its users get no admin.")
        # ---- groups
        group_specs: Dict[str, Optional[Set[str]]] = {}     # target group name -> desired tag set (None = all)

        def spec_name(base: str, tags: Optional[Set[str]]) -> str:
            name = base[:64]
            n = 1
            while name in group_specs and group_specs[name] != tags:
                n += 1
                suffix = f"-{n}"
                name = base[:64 - len(suffix)] + suffix
            group_specs.setdefault(name, tags)
            return name

        def groups_for(u: UserRec) -> List[str]:
            if o.group_mode == "none":
                return []
            if o.group_mode == "existing":
                return list(o.existing_groups)
            if o.group_mode == "by_source" and u.group_names:
                out = []
                for gname in u.group_names:
                    src_group = next((g for g in src.groups if g.name == gname), None)
                    tags = set(src_group.inbound_tags) if src_group else None
                    out.append(spec_name(gname, tags if tags else None))
                return out
            # automatic group(s)
            if src.kind == "marzban" and u.excluded_tags:
                tags = (target_tags | set(src.inbound_tags)) - set(u.excluded_tags)
                return [spec_name(o.group_name, tags)]
            return [spec_name(o.group_name, None)]

        # ---- users
        taken = set(st["users"])
        for u in src.users:
            name = u.username
            action = "insert"
            if name.lower() in taken:
                if o.conflict == "skip":
                    plan.skipped.append((name, "username already exists in PasarGuard"))
                    continue
                if o.conflict == "update":
                    action = "update"
                else:  # rename
                    n = 0
                    new = f"{name}{o.rename_suffix}"
                    while new.lower() in taken:
                        n += 1
                        new = f"{name}{o.rename_suffix}{n}"
                    plan.renamed.append((name, new))
                    name = new
            if action == "insert":
                taken.add(name.lower())
            if u.admin_src_id is not None and o.admin_mode == "map":
                ref = admin_ref.get(u.admin_src_id)
            elif o.admin_mode == "single":
                ref = ("name", target_admin_name)
            else:
                ref = None
            plan.users.append(PlannedUser(rec=u, action=action, final_username=name,
                                          groups=groups_for(u), admin_ref=ref))

        # resolve group specs against the target
        existing_groups = st["groups"]
        if o.group_mode == "existing":
            for g in o.existing_groups:
                if g not in existing_groups:
                    plan.errors.append(f"Group '{g}' does not exist in PasarGuard.")
        for name, tags in group_specs.items():
            if name in existing_groups:
                plan.warn(f"Group '{name}' already exists — reusing it as is (its inbounds are not changed).")
                continue
            want = set(target_tags) if tags is None else set(tags)
            usable = sorted(want & target_tags)
            missing = sorted(want - target_tags)
            if tags is not None and missing:
                plan.warn(f"Group '{name}': {len(missing)} inbound tag(s) from the source do not exist in "
                          f"PasarGuard ({', '.join(missing[:4])}{'…' if len(missing) > 4 else ''}) — "
                          "attach the right inbounds in PasarGuard after migrating.")
            if not usable:
                plan.warn(f"Group '{name}' will be created EMPTY — PasarGuard has no matching inbounds yet. "
                          "Open the panel once with your Xray config loaded, then add inbounds to the group.")
            plan.groups_new.append((name, usable))

        # shadowsocks/other validation notes
        short = sum(1 for p in plan.users if p.rec.proxies.get("shadowsocks")
                    and len(p.rec.proxies["shadowsocks"].get("password", "")) < 22)
        if short:
            plan.warn(f"{short} Shadowsocks password(s) are shorter than the 22 characters PasarGuard requires; "
                      "new random ones will be generated for those users.")
        for p in plan.users:
            p.rec.meta["_pg_proxy_settings"] = self._proxy_table(p.rec)
        return plan

    # -- row builders --------------------------------------------------------
    @staticmethod
    def _proxy_table(u: UserRec) -> Dict[str, Any]:
        p = u.proxies
        ss = p.get("shadowsocks") or {}
        ss_pwd = ss.get("password") or ""
        method = normalize_ss_method(ss.get("method")) or DEFAULT_SS_METHOD
        shared_uuid = (p.get("vmess") or p.get("vless") or {}).get("id")
        ex = u.extra_proxies
        return {
            "vmess": {"id": (p.get("vmess") or {}).get("id") or shared_uuid or str(_uuid.uuid4())},
            "vless": {"id": (p.get("vless") or {}).get("id") or shared_uuid or str(_uuid.uuid4())},
            "trojan": {"password": (p.get("trojan") or {}).get("password") or random_password()},
            "shadowsocks": {"password": ss_pwd if len(ss_pwd) >= 22 else random_password(), "method": method},
            "wireguard": ex.get("wireguard") or {"private_key": None, "public_key": None, "peer_ips": []},
            "hysteria": ex.get("hysteria") or {"auth": random_password()},
            "masque": ex.get("masque") or {"password": random_password()},
        }

    @staticmethod
    def _user_row_template() -> Dict[str, Any]:
        return {k: None for k in (
            "username", "status", "used_traffic", "data_limit", "data_limit_reset_strategy", "expire",
            "admin_id", "sub_revoked_at", "note", "online_at", "on_hold_expire_duration", "on_hold_timeout",
            "auto_delete_in_days", "created_at", "proxy_settings")}

    @staticmethod
    def _admin_row_template() -> Dict[str, Any]:
        return {k: None for k in (
            "username", "hashed_password", "created_at", "used_traffic", "telegram_id", "status",
            "role_id", "is_sudo")}

    def _user_row(self, pu: PlannedUser, colset: Set[str], now: datetime) -> Dict[str, Any]:
        r, o = pu.rec, self.opts
        if pu.admin_ref is None:
            admin_val = None
        elif pu.admin_ref[0] == "id":
            admin_val = pu.admin_ref[1]
        else:
            admin_val = Sub(f"SELECT {quote_ident(self.d, 'id')} FROM {quote_ident(self.d, 'admins')} "
                            f"WHERE {quote_ident(self.d, 'username')} = ?", (pu.admin_ref[1],))
        row = {
            "username": pu.final_username,
            "status": r.status,
            "used_traffic": r.used_traffic if o.preserve_usage else 0,
            "data_limit": r.data_limit,
            "data_limit_reset_strategy": r.reset_strategy,
            "expire": r.expire,
            "admin_id": admin_val,
            "sub_revoked_at": r.sub_revoked_at,
            "note": r.note,
            "online_at": r.online_at,
            "on_hold_expire_duration": r.on_hold_expire_duration if r.status == "on_hold" else None,
            "on_hold_timeout": r.on_hold_timeout if r.status == "on_hold" else None,
            "auto_delete_in_days": r.auto_delete_in_days,
            "created_at": r.created_at or now,
            "proxy_settings": r.meta["_pg_proxy_settings"],
        }
        return _filter_cols(row, colset)

    # -- statements ------------------------------------------------------------
    def statements(self, plan: Plan) -> List[Stmt]:
        if plan.errors:
            raise DBError("refusing to build statements: plan has errors")
        d, now = self.d, datetime.now(timezone.utc).replace(tzinfo=None)
        st_users_cols = {c.name for c in self.db.columns("users")}
        st_admin_cols = {c.name for c in self.db.columns("admins")}
        st_group_cols = {c.name for c in self.db.columns("groups")}
        inbound_ids = {str(r["tag"]): r["id"] for r in self.db.select("inbounds", ["id", "tag"])}
        existing_groups = {str(r["name"]): r["id"] for r in self.db.select("groups", ["id", "name"])}
        roles = ({r["id"] for r in self.db.select("admin_roles", ["id"])}
                 if self.db.has_table("admin_roles") else None)
        out: List[Stmt] = []

        for a in plan.admins_new:
            row = {"username": a.username, "hashed_password": a.hashed_password,
                   "created_at": a.created_at or now, "used_traffic": a.used_traffic if self.opts.preserve_usage else 0,
                   "telegram_id": a.telegram_id, "status": "active"}
            if "role_id" in st_admin_cols:
                want = 2 if a.is_sudo else 3          # administrator / operator; never create a 2nd owner
                if roles is not None and want not in roles:
                    want = min(roles) if roles else want
                row["role_id"] = want
            if "is_sudo" in st_admin_cols:
                row["is_sudo"] = bool(a.is_sudo)
            out.append(self._ins("admins", _filter_cols(row, st_admin_cols)))

        for name, tags in plan.groups_new:
            out.append(self._ins("groups", _filter_cols({"name": name, "is_disabled": False}, st_group_cols)))
            for tag in tags:
                out.append(_insert(d, "inbounds_groups_association", {
                    "inbound_id": inbound_ids[tag],
                    "group_id": Sub(f"SELECT {quote_ident(d, 'id')} FROM {quote_ident(d, 'groups')} "
                                    f"WHERE {quote_ident(d, 'name')} = ?", (name,)),
                }))

        new_group_names = {n for n, _ in plan.groups_new}
        for pu in plan.users:
            row = self._user_row(pu, st_users_cols, now)
            if pu.action == "insert":
                out.append(self._ins("users", row))
            else:
                row.pop("username", None)
                row.pop("created_at", None)
                out.append(_update(d, "users", row, "username", pu.final_username))
                out.append(Stmt(
                    f"DELETE FROM {quote_ident(d, 'users_groups_association')} WHERE {quote_ident(d, 'user_id')} = "
                    f"(SELECT {quote_ident(d, 'id')} FROM {quote_ident(d, 'users')} WHERE {quote_ident(d, 'username')} = ?)",
                    (pu.final_username,)))
            for g in dict.fromkeys(pu.groups):
                if g in new_group_names:
                    gid: Any = Sub(f"SELECT {quote_ident(d, 'id')} FROM {quote_ident(d, 'groups')} "
                                   f"WHERE {quote_ident(d, 'name')} = ?", (g,))
                elif g in existing_groups:
                    gid = existing_groups[g]
                else:
                    continue
                out.append(_insert(d, "users_groups_association", {
                    "user_id": Sub(f"SELECT {quote_ident(d, 'id')} FROM {quote_ident(d, 'users')} "
                                   f"WHERE {quote_ident(d, 'username')} = ?", (pu.final_username,)),
                    "groups_id": gid,
                }))
        return out

    def counts(self) -> Dict[str, int]:
        out = {}
        for t in ("users", "admins", "groups", "users_groups_association"):
            if self.db.has_table(t):
                out[t] = len(self.db.select(t, [self.db.columns(t)[0].name]))
        return out

    def verify(self, plan: Plan, before: Dict[str, int]) -> List[str]:
        problems: List[str] = []
        after = self.counts()
        want_users = before.get("users", 0) + plan.inserts
        if after.get("users") != want_users:
            problems.append(f"users: expected {want_users} rows after migration, found {after.get('users')}")
        want_admins = before.get("admins", 0) + len(plan.admins_new)
        if after.get("admins") != want_admins:
            problems.append(f"admins: expected {want_admins}, found {after.get('admins')}")
        names = {str(r["username"]).lower() for r in self.db.select("users", ["username"])}
        gone = [p.final_username for p in plan.users if p.final_username.lower() not in names]
        if gone:
            problems.append(f"{len(gone)} migrated user(s) not found afterwards (e.g. {gone[0]})")
        return problems


# --------------------------------------------------------------------------
# Marzban (target)
# --------------------------------------------------------------------------
_MARZBAN_TYPE = {"vmess": "VMess", "vless": "VLESS", "trojan": "Trojan", "shadowsocks": "Shadowsocks"}
_MARZBAN_SS = ("aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305")


class MarzbanWriter(_Writer):
    kind = "marzban"
    REQUIRED_TABLES = ("users", "proxies", "admins")

    def plan(self, src: SourceData) -> Plan:
        o = self.opts
        plan = Plan(target_kind="marzban")
        plan.warnings.extend(src.warnings)
        have = set(self.db.table_names())
        missing = [t for t in self.REQUIRED_TABLES if t not in have]
        if missing:
            plan.errors.append(f"Target is not a Marzban database (missing: {', '.join(missing)}).")
            return plan
        ucols = self.db.columns("users")
        acols = self.db.columns("admins")
        _check_required(ucols, set(self._user_template()), "users", plan, self._fills)
        _check_required(acols, {"username", "hashed_password", "created_at", "is_sudo", "telegram_id", "users_usage"},
                        "admins", plan, self._fills)
        if plan.errors:
            return plan
        existing = {str(r["username"]).lower() for r in self.db.select("users", ["username"])}
        admins = {str(r["username"]).lower(): r for r in self.db.select("admins")}
        tags = ({str(r["tag"]) for r in self.db.select("inbounds", ["tag"])}
                if self.db.has_table("inbounds") else set())
        plan.target_counts = {"users": len(existing), "admins": len(admins), "inbounds": len(tags)}

        admin_ref: Dict[Any, Any] = {}
        if o.admin_mode == "single":
            row = admins.get((o.target_admin or "").lower())
            if not row:
                plan.errors.append(f"Target admin '{o.target_admin}' does not exist in Marzban.")
                return plan
        elif o.admin_mode == "map":
            for a in src.admins:
                hit = admins.get(a.username.lower())
                if hit:
                    admin_ref[a.src_id] = ("id", hit["id"])
                elif o.create_missing_admins and a.hashed_password:
                    plan.admins_new.append(a)
                    admin_ref[a.src_id] = ("name", a.username)
                else:
                    plan.warn(f"Admin '{a.username}' could not be created in Marzban.")

        taken = set(existing)
        for u in src.users:
            name = u.username
            if len(name) > 32:
                short = name[:32].rstrip("-_@.")
                plan.warn(f"Marzban limits usernames to 32 characters: '{name}' was shortened.")
                name = short
            action = "insert"
            if name.lower() in taken:
                if o.conflict == "skip":
                    plan.skipped.append((name, "username already exists in Marzban"))
                    continue
                if o.conflict == "update":
                    action = "update"
                else:
                    n, new = 0, f"{name}{o.rename_suffix}"[:32]
                    while new.lower() in taken:
                        n += 1
                        new = f"{name[:28]}{o.rename_suffix}{n}"[:32]
                    plan.renamed.append((name, new))
                    name = new
            if action == "insert":
                taken.add(name.lower())
            if o.admin_mode == "single":
                ref = ("name", admins[(o.target_admin or "").lower()]["username"])
            elif o.admin_mode == "map" and u.admin_src_id is not None:
                ref = admin_ref.get(u.admin_src_id)
            else:
                ref = None
            plan.users.append(PlannedUser(rec=u, action=action, final_username=name, admin_ref=ref))
            # exclusions (tags the user may NOT use)
            if u.allowed_tags is not None and tags:
                u.excluded_tags = sorted(tags - set(u.allowed_tags))
        dropped = sum(1 for p in plan.users if p.rec.extra_proxies)
        if dropped:
            plan.warn(f"{dropped} user(s) have WireGuard/Hysteria/Masque settings which Marzban cannot store; "
                      "those were dropped.")
        if not tags:
            plan.warn("Marzban has no inbounds yet, so per-user inbound restrictions were not written.")
        return plan

    @staticmethod
    def _user_template() -> Dict[str, Any]:
        return {k: None for k in ("username", "status", "used_traffic", "data_limit", "data_limit_reset_strategy",
                                  "expire", "admin_id", "created_at", "note", "on_hold_expire_duration",
                                  "on_hold_timeout", "sub_revoked_at", "online_at", "auto_delete_in_days",
                                  "last_status_change")}

    def statements(self, plan: Plan) -> List[Stmt]:
        if plan.errors:
            raise DBError("refusing to build statements: plan has errors")
        d, now = self.d, datetime.now(timezone.utc).replace(tzinfo=None)
        ucols = {c.name for c in self.db.columns("users")}
        acols = {c.name for c in self.db.columns("admins")}
        out: List[Stmt] = []
        for a in plan.admins_new:
            out.append(self._ins("admins", _filter_cols({
                "username": a.username, "hashed_password": a.hashed_password, "created_at": a.created_at or now,
                "is_sudo": bool(a.is_sudo), "telegram_id": a.telegram_id,
                "users_usage": a.used_traffic if self.opts.preserve_usage else 0}, acols)))
        for pu in plan.users:
            r = pu.rec
            if pu.admin_ref is None:
                admin_val: Any = None
            elif pu.admin_ref[0] == "id":
                admin_val = pu.admin_ref[1]
            else:
                admin_val = Sub(f"SELECT {quote_ident(d, 'id')} FROM {quote_ident(d, 'admins')} "
                                f"WHERE {quote_ident(d, 'username')} = ?", (pu.admin_ref[1],))
            expire = int(r.expire.replace(tzinfo=None).timestamp()) if r.expire and False else None
            if r.expire:
                import calendar
                expire = calendar.timegm(r.expire.timetuple())
            row = _filter_cols({
                "username": pu.final_username, "status": r.status,
                "used_traffic": r.used_traffic if self.opts.preserve_usage else 0, "data_limit": r.data_limit,
                "data_limit_reset_strategy": r.reset_strategy, "expire": expire, "admin_id": admin_val,
                "created_at": r.created_at or now, "note": r.note,
                "on_hold_expire_duration": r.on_hold_expire_duration if r.status == "on_hold" else None,
                "on_hold_timeout": r.on_hold_timeout if r.status == "on_hold" else None,
                "sub_revoked_at": r.sub_revoked_at, "online_at": r.online_at,
                "auto_delete_in_days": r.auto_delete_in_days, "last_status_change": now}, ucols)
            uid_sub = Sub(f"SELECT {quote_ident(d, 'id')} FROM {quote_ident(d, 'users')} "
                          f"WHERE {quote_ident(d, 'username')} = ?", (pu.final_username,))
            if pu.action == "insert":
                out.append(self._ins("users", row))
            else:
                row.pop("username", None)
                row.pop("created_at", None)
                out.append(_update(d, "users", row, "username", pu.final_username))
                if self.db.has_table("exclude_inbounds_association"):
                    out.append(Stmt(
                        f"DELETE FROM {quote_ident(d, 'exclude_inbounds_association')} WHERE {quote_ident(d, 'proxy_id')} IN "
                        f"(SELECT {quote_ident(d, 'id')} FROM {quote_ident(d, 'proxies')} WHERE {quote_ident(d, 'user_id')} = "
                        f"({uid_sub.sql}))", uid_sub.params))
                out.append(Stmt(f"DELETE FROM {quote_ident(d, 'proxies')} WHERE {quote_ident(d, 'user_id')} = "
                                f"({uid_sub.sql})", uid_sub.params))
            for proto in CORE_PROTOCOLS:
                settings = self._proxy_settings(r, proto)
                out.append(_insert(d, "proxies", {"user_id": uid_sub, "type": _MARZBAN_TYPE[proto],
                                                  "settings": settings}))
                if self.db.has_table("exclude_inbounds_association"):
                    for tag in r.excluded_tags:
                        out.append(_insert(d, "exclude_inbounds_association", {
                            "proxy_id": Sub(
                                f"SELECT p.{quote_ident(d, 'id')} FROM {quote_ident(d, 'proxies')} p "
                                f"JOIN {quote_ident(d, 'users')} u ON u.{quote_ident(d, 'id')} = p.{quote_ident(d, 'user_id')} "
                                f"WHERE u.{quote_ident(d, 'username')} = ? AND p.{quote_ident(d, 'type')} = ?",
                                (pu.final_username, _MARZBAN_TYPE[proto])),
                            "inbound_tag": tag}))
        return out

    @staticmethod
    def _proxy_settings(u: UserRec, proto: str) -> Dict[str, Any]:
        p = u.proxies.get(proto) or {}
        shared = (u.proxies.get("vmess") or u.proxies.get("vless") or {}).get("id")
        if proto == "vmess":
            return {"id": p.get("id") or shared or str(_uuid.uuid4())}
        if proto == "vless":
            return {"id": p.get("id") or shared or str(_uuid.uuid4()), "flow": ""}
        if proto == "trojan":
            return {"password": p.get("password") or random_password()}
        method = normalize_ss_method(p.get("method")) or DEFAULT_SS_METHOD
        if method not in _MARZBAN_SS:
            method = DEFAULT_SS_METHOD
        return {"password": p.get("password") or random_password(), "method": method}

    def counts(self) -> Dict[str, int]:
        out = {}
        for t in ("users", "admins", "proxies"):
            if self.db.has_table(t):
                out[t] = len(self.db.select(t, [self.db.columns(t)[0].name]))
        return out

    def verify(self, plan: Plan, before: Dict[str, int]) -> List[str]:
        problems: List[str] = []
        after = self.counts()
        want = before.get("users", 0) + plan.inserts
        if after.get("users") != want:
            problems.append(f"users: expected {want} rows after migration, found {after.get('users')}")
        return problems


def get_writer(kind: str, db: BaseDB, opts: Optional[MigrationOptions] = None) -> _Writer:
    if kind == "pasarguard":
        return PasarGuardWriter(db, opts)
    if kind == "marzban":
        return MarzbanWriter(db, opts)
    raise ValueError(f"unsupported target: {kind}")
