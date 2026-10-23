"""Source readers: panel database -> neutral ``SourceData``.

Supported sources
-----------------
* ``marzban``     – Gozargah/Marzban (users + proxies + exclude_inbounds_association)
* ``marzneshin``  – marzneshin (services, per-user ``key``; credentials are
                    *derived* from the key with xxh128 or used verbatim)
* ``rebecca``     – rebeccapanel/Rebecca (credential_key + services)
* ``3xui``        – MHSanaei/3x-ui and x-ui forks (clients embedded in inbound
                    JSON, plus the newer ``clients`` table)
* ``pasarguard``  – PasarGuard itself (used for backend conversion and for
                    PasarGuard -> Marzban)

Every reader is read-only. Anything that cannot be represented in the neutral
model is recorded in ``SourceData.warnings`` instead of being guessed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from .dbio import BaseDB, DBError, parse_dt, parse_json
from .model import (AdminRec, CORE_PROTOCOLS, GroupRec, SourceData, UserRec,
                    normalize_ss_method, sanitize_username, username_is_valid, uuid_from_hex,
                    DEFAULT_SS_METHOD)
from . import xxh128

SOURCE_KINDS = ("marzban", "marzneshin", "rebecca", "3xui", "pasarguard")


# --------------------------------------------------------------------------
# small coercion helpers
# --------------------------------------------------------------------------
def _i(v: Any, default: int = 0) -> int:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return int(v)
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


def _in(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    r = _i(v, default=-(1 << 62))
    return None if r == -(1 << 62) else r


def _b(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "t", "true", "yes", "y")
    return bool(v)


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    return s if s != "" else None


def _status(v: Any) -> str:
    s = str(v or "active").strip().lower()
    if "." in s:                       # e.g. "UserStatus.active"
        s = s.rsplit(".", 1)[1]
    return s if s in ("active", "disabled", "limited", "expired", "on_hold") else "active"


def _reset(v: Any) -> str:
    s = str(v or "no_reset").strip().lower()
    if "." in s:
        s = s.rsplit(".", 1)[1]
    return s if s in ("no_reset", "day", "week", "month", "year") else "no_reset"


def _limit(v: Any) -> Optional[int]:
    n = _in(v)
    return n if n and n > 0 else None


def _note(*parts: Optional[str]) -> Optional[str]:
    text = " | ".join(p for p in parts if p)
    return text[:500] if text else None


def _clean_ss(settings: Dict[str, Any], sd: SourceData, who: str) -> Optional[Dict[str, Any]]:
    pwd = settings.get("password")
    if not pwd:
        return None
    method = normalize_ss_method(settings.get("method"))
    if method is None:
        sd.warn(f"Shadowsocks method '{settings.get('method')}' has no PasarGuard equivalent "
                f"(first seen on {who}) — default {DEFAULT_SS_METHOD} is used; the user must re-import the link.")
        method = DEFAULT_SS_METHOD
    return {"password": str(pwd), "method": method}


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------
def detect_kind(db: BaseDB) -> Optional[str]:
    """Identify which panel a database belongs to (None if unrecognised)."""
    tables = set(db.table_names())
    if "client_traffics" in tables and "inbounds" in tables:
        return "3xui"
    if "users" not in tables:
        return None
    ucols = set(db.column_names("users"))
    if "credential_key" in ucols:
        return "rebecca"
    if "key" in ucols and "services" in tables:
        return "marzneshin"
    if "proxy_settings" in ucols and "groups" in tables:
        return "pasarguard"
    if "proxies" in tables:
        return "marzban"
    return None


def read_source(db: BaseDB, kind: Optional[str] = None, **opts: Any) -> SourceData:
    kind = kind or detect_kind(db)
    if kind not in SOURCE_KINDS:
        raise DBError("could not recognise this database as Marzban, Marzneshin, Rebecca, 3x-ui or PasarGuard")
    reader = {"marzban": read_marzban, "marzneshin": read_marzneshin, "rebecca": read_rebecca,
              "3xui": read_3xui, "pasarguard": read_pasarguard}[kind]
    return reader(db, **opts)


def _require(db: BaseDB, tables: List[str], kind: str) -> None:
    have = set(db.table_names())
    missing = [t for t in tables if t not in have]
    if missing:
        raise DBError(f"not a {kind} database: missing table(s) {', '.join(missing)}")


def _admin(a: Dict[str, Any], sudo: bool) -> AdminRec:
    return AdminRec(src_id=a.get("id"), username=str(a.get("username")),
                    hashed_password=_s(a.get("hashed_password")), is_sudo=sudo,
                    telegram_id=_in(a.get("telegram_id")), created_at=parse_dt(a.get("created_at")),
                    used_traffic=_i(a.get("users_usage") if a.get("users_usage") is not None
                                    else a.get("used_traffic")))


def _finish(sd: SourceData) -> SourceData:
    """Validate/repair usernames and make them unique (case-insensitive)."""
    seen: Dict[str, int] = {}
    for u in sd.users:
        original = u.username
        name = original if username_is_valid(original) else sanitize_username(original)
        key = name.lower()
        if key in seen:
            seen[key] += 1
            suffix = f"_{seen[key]}"
            name = (name[:128 - len(suffix)] + suffix)
            key = name.lower()
            seen.setdefault(key, 0)
        else:
            seen[key] = 0
        if name != original:
            u.original_username = original
            u.username = name
    renamed = [u for u in sd.users if u.original_username]
    if renamed:
        sd.warn(f"{len(renamed)} username(s) were not valid for PasarGuard/Marzban and were sanitised "
                f"(e.g. '{renamed[0].original_username}' -> '{renamed[0].username}').")
    return sd


# --------------------------------------------------------------------------
# Marzban
# --------------------------------------------------------------------------
def _marzban_proxy(ptype: str, settings: Dict[str, Any], sd: SourceData, who: str) -> Optional[Dict[str, Any]]:
    ptype = ptype.lower().rsplit(".", 1)[-1]
    if ptype in ("vmess", "vless"):
        uid = uuid_from_hex(settings.get("id", ""))
        return {"id": uid} if uid else None
    if ptype == "trojan":
        return {"password": str(settings["password"])} if settings.get("password") else None
    if ptype == "shadowsocks":
        return _clean_ss(settings, sd, who)
    return None


def read_marzban(db: BaseDB, **_: Any) -> SourceData:
    _require(db, ["users", "proxies", "admins"], "Marzban")
    sd = SourceData(kind="marzban")

    for a in db.select("admins"):
        sd.admins.append(_admin(a, _b(a.get("is_sudo"))))

    tags = []
    if db.has_table("inbounds"):
        tags = [r["tag"] for r in db.select("inbounds", ["tag"]) if r.get("tag")]
    sd.inbound_tags = sorted(set(tags))

    excluded: Dict[Any, Set[str]] = {}
    if db.has_table("exclude_inbounds_association"):
        for r in db.select("exclude_inbounds_association"):
            excluded.setdefault(r.get("proxy_id"), set()).add(r.get("inbound_tag"))

    by_user: Dict[Any, Dict[str, Dict[str, Any]]] = {}
    excl_by_user: Dict[Any, Set[str]] = {}
    for p in db.select("proxies"):
        uid = p.get("user_id")
        ptype = str(p.get("type") or "").lower().rsplit(".", 1)[-1]
        settings = parse_json(p.get("settings"), {}) or {}
        if ptype not in CORE_PROTOCOLS:
            continue
        clean = _marzban_proxy(ptype, settings, sd, f"user_id={uid}")
        if clean is None:
            sd.warn(f"A {ptype} proxy had unusable credentials and was skipped (a new random one will be generated).")
            continue
        by_user.setdefault(uid, {})[ptype] = clean
        if settings.get("flow"):
            sd.notes.append("vless 'flow' is per-user in Marzban but per-host in PasarGuard — "
                            "set it on the PasarGuard host/inbound if you used XTLS-Vision.")
        excl_by_user.setdefault(uid, set()).update(excluded.get(p.get("id"), set()))

    for u in db.select("users"):
        status = _status(u.get("status"))
        exp = _in(u.get("expire"))
        expire = parse_dt(exp) if exp else None
        rec = UserRec(
            src_id=u.get("id"), username=str(u.get("username")), status=status,
            used_traffic=_i(u.get("used_traffic")), data_limit=_limit(u.get("data_limit")),
            reset_strategy=_reset(u.get("data_limit_reset_strategy")),
            expire=expire if status != "on_hold" else None,
            created_at=parse_dt(u.get("created_at")), note=_note(_s(u.get("note"))),
            admin_src_id=u.get("admin_id"),
            on_hold_expire_duration=_in(u.get("on_hold_expire_duration")),
            on_hold_timeout=parse_dt(u.get("on_hold_timeout")),
            sub_revoked_at=parse_dt(u.get("sub_revoked_at")), online_at=parse_dt(u.get("online_at")),
            auto_delete_in_days=_in(u.get("auto_delete_in_days")),
            proxies=by_user.get(u.get("id"), {}),
            excluded_tags=sorted(t for t in excl_by_user.get(u.get("id"), set()) if t),
        )
        if status == "on_hold" and not rec.on_hold_expire_duration:
            sd.warn(f"on-hold user '{rec.username}' has no duration; imported as active instead.")
            rec.status = "active"
        sd.users.append(rec)
    return _finish(sd)


# --------------------------------------------------------------------------
# Marzneshin
# --------------------------------------------------------------------------
def _marzneshin_creds(key: str, algo: str) -> Dict[str, Dict[str, Any]]:
    if algo == "plain":
        uid = uuid_from_hex(key)
        password = key
    else:
        uid = str(__import__("uuid").UUID(bytes=xxh128.digest(key.encode())))
        password = xxh128.digest(key.encode()).hex()
    out: Dict[str, Dict[str, Any]] = {}
    if uid:
        out["vmess"] = {"id": uid}
        out["vless"] = {"id": uid}
    out["trojan"] = {"password": password}
    if len(password) >= 22:
        out["shadowsocks"] = {"password": password, "method": DEFAULT_SS_METHOD}
    return out


def read_marzneshin(db: BaseDB, auth_algorithm: str = "xxh128", **_: Any) -> SourceData:
    _require(db, ["users", "services", "users_services", "inbounds", "inbounds_services"], "Marzneshin")
    algo = (auth_algorithm or "xxh128").lower()
    if algo not in ("xxh128", "plain"):
        raise DBError("auth_algorithm must be 'xxh128' or 'plain'")
    sd = SourceData(kind="marzneshin")
    sd.notes.append(f"Credentials re-derived from each user's key using AUTH_GENERATION_ALGORITHM={algo}. "
                    "If your Marzneshin .env sets it differently, re-run with the other value.")

    for a in db.select("admins"):
        sd.admins.append(_admin(a, _b(a.get("is_sudo"))))

    inbound_tag_by_id = {r["id"]: r["tag"] for r in db.select("inbounds", ["id", "tag"])}
    sd.inbound_tags = sorted(set(inbound_tag_by_id.values()))
    services = {r["id"]: r["name"] for r in db.select("services", ["id", "name"])}
    svc_tags: Dict[Any, Set[str]] = {sid: set() for sid in services}
    for r in db.select("inbounds_services"):
        tag = inbound_tag_by_id.get(r["inbound_id"])
        if tag and r["service_id"] in svc_tags:
            svc_tags[r["service_id"]].add(tag)
    for sid, name in services.items():
        sd.groups.append(GroupRec(name=str(name)[:64], inbound_tags=sorted(svc_tags[sid])))

    user_services: Dict[Any, List[str]] = {}
    for r in db.select("users_services"):
        nm = services.get(r["service_id"])
        if nm:
            user_services.setdefault(r["user_id"], []).append(str(nm)[:64])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for u in db.select("users"):
        if _b(u.get("removed")):
            sd.skipped.append(f"{u.get('username')} (removed)")
            continue
        key = _s(u.get("key")) or ""
        strategy = str(u.get("expire_strategy") or "never").lower().rsplit(".", 1)[-1]
        expire = parse_dt(u.get("expire_date"))
        hold = None
        on_hold_timeout = parse_dt(u.get("activation_deadline"))
        used = _i(u.get("used_traffic"))
        limit = _limit(u.get("data_limit"))
        if not _b(u.get("enabled", True)):
            status = "disabled"
        elif strategy == "start_on_first_use" and expire is None:
            hold = _in(u.get("usage_duration"))
            status = "on_hold" if hold else "active"
        elif expire is not None and expire < now:
            status = "expired"
        elif limit is not None and used >= limit:
            status = "limited"
        else:
            status = "active"
        if strategy == "never":
            expire = None
        proxies: Dict[str, Dict[str, Any]] = {}
        if key:
            try:
                proxies = _marzneshin_creds(key, algo)
            except Exception as e:  # pragma: no cover - defensive
                sd.warn(f"could not derive credentials for '{u.get('username')}': {e}")
        if "vmess" not in proxies:
            sd.warn("Some users have a key that is not a UUID in 'plain' mode; their VMess/VLESS UUID "
                    "was regenerated (they must re-import the subscription).")
        svcs = user_services.get(u.get("id"), [])
        sd.users.append(UserRec(
            src_id=u.get("id"), username=str(u.get("username")), status=status, used_traffic=used,
            data_limit=limit, reset_strategy=_reset(u.get("data_limit_reset_strategy")),
            expire=expire if status != "on_hold" else None, created_at=parse_dt(u.get("created_at")),
            note=_note(_s(u.get("note"))), admin_src_id=u.get("admin_id"),
            on_hold_expire_duration=hold, on_hold_timeout=on_hold_timeout,
            sub_revoked_at=parse_dt(u.get("sub_revoked_at")), online_at=parse_dt(u.get("online_at")),
            proxies=proxies, group_names=svcs,
            allowed_tags=sorted({t for s in svcs for sid, n in services.items() if str(n)[:64] == s
                                 for t in svc_tags[sid]}),
        ))
    return _finish(sd)


# --------------------------------------------------------------------------
# Rebecca
# --------------------------------------------------------------------------
def _rebecca_creds(key: str) -> Dict[str, Dict[str, Any]]:
    k = key.strip().lower().replace("-", "")
    if len(k) != 32 or any(c not in "0123456789abcdef" for c in k):
        return {}
    uid = f"{k[:8]}-{k[8:12]}-{k[12:16]}-{k[16:20]}-{k[20:]}"
    pw = lambda label: hashlib.sha256(f"{label}:{k}".encode()).hexdigest()[:32]
    return {"vmess": {"id": uid}, "vless": {"id": uid}, "trojan": {"password": pw("trojan")},
            "shadowsocks": {"password": pw("shadowsocks"), "method": DEFAULT_SS_METHOD}}


def read_rebecca(db: BaseDB, **_: Any) -> SourceData:
    _require(db, ["users"], "Rebecca")
    sd = SourceData(kind="rebecca")
    sd.notes.append("Rebecca credentials are derived from credential_key exactly as Rebecca's own code does "
                    "(UUID = key, passwords = sha256(label:key)[:32]); explicit rows in its proxies table win.")

    if db.has_table("admins"):
        acols = set(db.column_names("admins"))
        if {"username", "hashed_password"} <= acols:
            for a in db.select("admins"):
                role = str(a.get("role") or "").lower()
                sd.admins.append(_admin(a, role in ("owner", "full_access", "sudo", "admin")))
    services = {}
    if db.has_table("services"):
        services = {r["id"]: str(r["name"])[:64] for r in db.select("services", ["id", "name"])}
        for name in services.values():
            sd.groups.append(GroupRec(name=name, inbound_tags=[]))
        if services:
            sd.notes.append("Rebecca services became groups, but its service->host mapping is not copied; "
                            "attach inbounds to the new groups in PasarGuard.")
    if db.has_table("inbounds") and "tag" in db.column_names("inbounds"):
        sd.inbound_tags = sorted({r["tag"] for r in db.select("inbounds", ["tag"]) if r.get("tag")})

    explicit: Dict[Any, Dict[str, Dict[str, Any]]] = {}
    if db.has_table("proxies"):
        for p in db.select("proxies"):
            ptype = str(p.get("type") or "").lower().rsplit(".", 1)[-1]
            if ptype in CORE_PROTOCOLS:
                st = parse_json(p.get("settings"), {}) or {}
                clean = _marzban_proxy(ptype, st, sd, f"user_id={p.get('user_id')}")
                if clean:
                    explicit.setdefault(p.get("user_id"), {})[ptype] = clean

    for u in db.select("users"):
        status = _status(u.get("status"))
        if str(u.get("status") or "").lower() == "deleted":
            sd.skipped.append(f"{u.get('username')} (deleted)")
            continue
        exp = _in(u.get("expire"))
        creds = _rebecca_creds(_s(u.get("credential_key")) or "")
        creds.update(explicit.get(u.get("id"), {}))
        if not creds:
            sd.warn("Some Rebecca users had no usable credentials; random ones were generated.")
        sd.users.append(UserRec(
            src_id=u.get("id"), username=str(u.get("username")), status=status,
            used_traffic=_i(u.get("used_traffic")), data_limit=_limit(u.get("data_limit")),
            reset_strategy=_reset(u.get("data_limit_reset_strategy")),
            expire=parse_dt(exp) if exp and status != "on_hold" else None,
            created_at=parse_dt(u.get("created_at")), note=_note(_s(u.get("note"))),
            admin_src_id=u.get("admin_id"),
            on_hold_expire_duration=_in(u.get("on_hold_expire_duration")),
            online_at=parse_dt(u.get("online_at")), proxies=creds,
            group_names=[services[u["service_id"]]] if u.get("service_id") in services else [],
        ))
    return _finish(sd)


# --------------------------------------------------------------------------
# 3x-ui
# --------------------------------------------------------------------------
_XUI_PROTOS = ("vmess", "vless", "trojan", "shadowsocks")


def _xui_user(email: str, client: Dict[str, Any], inbound: Dict[str, Any], traffic: Dict[str, Any],
              sd: SourceData, now: datetime) -> UserRec:
    proto = str(inbound.get("protocol") or "").lower()
    settings = parse_json(inbound.get("settings"), {}) or {}

    expiry_ms = _i(traffic.get("expiry_time", client.get("expiryTime", client.get("expiry_time", 0))))
    total = _i(traffic.get("total", client.get("totalGB", client.get("total_gb", 0))))
    used = _i(traffic.get("up")) + _i(traffic.get("down"))
    enable = _b(traffic["enable"]) if "enable" in traffic else _b(client.get("enable", True))

    expire: Optional[datetime] = None
    hold: Optional[int] = None
    if expiry_ms < 0:                       # 3x-ui: negative = "start after first use" (duration in ms)
        hold = max(1, abs(expiry_ms) // 1000)
    elif expiry_ms > 0:
        expire = parse_dt(expiry_ms)
    limit = total if total > 0 else None
    if not enable:
        status = "disabled"
    elif hold:
        status = "on_hold"
    elif expire is not None and expire < now:
        status = "expired"
    elif limit is not None and used >= limit:
        status = "limited"
    else:
        status = "active"

    proxies: Dict[str, Dict[str, Any]] = {}
    cid = client.get("id") or client.get("uuid")
    if proto in ("vmess", "vless") and cid:
        uid = uuid_from_hex(cid)
        if uid:
            proxies["vmess"] = {"id": uid}
            proxies["vless"] = {"id": uid}
    elif proto == "trojan" and client.get("password"):
        proxies["trojan"] = {"password": str(client["password"])}
    elif proto == "shadowsocks" and client.get("password"):
        ss = _clean_ss({"password": client.get("password"),
                        "method": client.get("method") or settings.get("method")}, sd, email)
        if ss:
            proxies["shadowsocks"] = ss
    elif proto not in _XUI_PROTOS:
        sd.warn(f"Protocol '{proto}' (client '{email}') is not supported by PasarGuard users and was imported "
                "without credentials for it.")

    reset_days = _i(client.get("reset", 0))
    strategy = {1: "day", 7: "week", 30: "month", 365: "year"}.get(reset_days, "no_reset")
    if reset_days and strategy == "no_reset":
        sd.warn(f"Client '{email}' resets traffic every {reset_days} days — PasarGuard only supports "
                "day/week/month/year, so no reset was configured for it.")
    comment = _s(client.get("comment"))
    tg = _in(client.get("tgId", client.get("tg_id")))
    created = parse_dt(client.get("created_at", client.get("createdAt")))
    return UserRec(
        src_id=email, username=email, status=status, used_traffic=used, data_limit=limit,
        reset_strategy=strategy, expire=expire, created_at=created,
        note=_note(comment, f"tg:{tg}" if tg else None), on_hold_expire_duration=hold,
        proxies=proxies, group_names=[], allowed_tags=[str(inbound.get("tag"))] if inbound.get("tag") else None,
        meta={"sub_id": client.get("subId", client.get("sub_id")), "limit_ip": _i(client.get("limitIp", client.get("limit_ip")))},
    )


def read_3xui(db: BaseDB, **_: Any) -> SourceData:
    _require(db, ["inbounds", "client_traffics"], "3x-ui")
    sd = SourceData(kind="3xui")
    sd.notes.append("3x-ui has a single panel admin and no admin-owned users, so every migrated user is "
                    "assigned to the admin you choose. Subscription IDs and IP limits cannot be carried over.")
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    inbounds = {r["id"]: r for r in db.select("inbounds")}
    sd.inbound_tags = sorted({str(r.get("tag")) for r in inbounds.values() if r.get("tag")})
    traffic: Dict[str, Dict[str, Any]] = {}
    for r in db.select("client_traffics"):
        if r.get("email"):
            traffic[str(r["email"])] = r

    seen: Set[str] = set()
    # newer schema: dedicated clients table + client_inbounds link table
    if db.has_table("clients") and db.select("clients", ["id"]):
        links: Dict[Any, List[Any]] = {}
        if db.has_table("client_inbounds"):
            for r in db.select("client_inbounds"):
                links.setdefault(r["client_id"], []).append(r["inbound_id"])
        for c in db.select("clients"):
            email = _s(c.get("email"))
            if not email or email in seen:
                continue
            seen.add(email)
            ibs = [inbounds[i] for i in links.get(c.get("id"), []) if i in inbounds] or [{}]
            # prefer the first inbound that carries a protocol we can map
            ib = next((x for x in ibs if str(x.get("protocol", "")).lower() in _XUI_PROTOS), ibs[0])
            client = dict(c)
            client.setdefault("id", c.get("uuid"))
            if not client.get("id") and c.get("uuid"):
                client["id"] = c["uuid"]
            rec = _xui_user(email, client, ib, traffic.get(email, {}), sd, now)
            # a client attached to several inbounds can carry several protocols' credentials
            for other in ibs:
                p = str(other.get("protocol", "")).lower()
                if p == "trojan" and c.get("password"):
                    rec.proxies.setdefault("trojan", {"password": str(c["password"])})
                if p == "shadowsocks" and c.get("password"):
                    ss = _clean_ss({"password": c.get("password"),
                                    "method": (parse_json(other.get("settings"), {}) or {}).get("method")}, sd, email)
                    if ss:
                        rec.proxies.setdefault("shadowsocks", ss)
            rec.allowed_tags = sorted({str(x.get("tag")) for x in ibs if x.get("tag")}) or None
            if _s(c.get("group_name")):
                rec.group_names = [str(c["group_name"])[:64]]
            sd.users.append(rec)
    else:
        for ib in inbounds.values():
            settings = parse_json(ib.get("settings"), {}) or {}
            for client in settings.get("clients", []) or []:
                email = _s(client.get("email"))
                if not email:
                    sd.skipped.append(f"client without email on inbound {ib.get('id')}")
                    continue
                if email in seen:
                    sd.warn(f"Client email '{email}' appears on more than one inbound; the first one was kept.")
                    continue
                seen.add(email)
                sd.users.append(_xui_user(email, client, ib, traffic.get(email, {}), sd, now))
        sd.groups = []
    for ib in inbounds.values():
        if str(ib.get("protocol", "")).lower() in ("wireguard", "hysteria", "tuic", "mtproto", "amneziawg", "masque"):
            sd.warn(f"Inbound '{ib.get('remark') or ib.get('tag')}' uses protocol {ib.get('protocol')}; "
                    "its clients were not migrated.")
    return _finish(sd)


# --------------------------------------------------------------------------
# PasarGuard (as a source)
# --------------------------------------------------------------------------
def read_pasarguard(db: BaseDB, **_: Any) -> SourceData:
    _require(db, ["users", "admins", "groups"], "PasarGuard")
    sd = SourceData(kind="pasarguard")
    for a in db.select("admins"):
        role_id = _i(a.get("role_id"), 3)
        sd.admins.append(_admin(a, role_id in (1, 2) or _b(a.get("is_sudo"))))
    inb = {}
    if db.has_table("inbounds"):
        inb = {r["id"]: r["tag"] for r in db.select("inbounds", ["id", "tag"])}
    sd.inbound_tags = sorted(inb.values())
    groups = {r["id"]: r for r in db.select("groups")}
    g_tags: Dict[Any, Set[str]] = {gid: set() for gid in groups}
    if db.has_table("inbounds_groups_association"):
        for r in db.select("inbounds_groups_association"):
            if r["group_id"] in g_tags and r["inbound_id"] in inb:
                g_tags[r["group_id"]].add(inb[r["inbound_id"]])
    for gid, g in groups.items():
        sd.groups.append(GroupRec(name=str(g["name"]), inbound_tags=sorted(g_tags[gid]),
                                  disabled=_b(g.get("is_disabled"))))
    u_groups: Dict[Any, List[Any]] = {}
    if db.has_table("users_groups_association"):
        for r in db.select("users_groups_association"):
            u_groups.setdefault(r["user_id"], []).append(r["groups_id"])

    for u in db.select("users"):
        ps = parse_json(u.get("proxy_settings"), {}) or {}
        proxies: Dict[str, Dict[str, Any]] = {}
        extra: Dict[str, Any] = {}
        for proto, val in ps.items():
            if proto in ("vmess", "vless") and val.get("id"):
                proxies[proto] = {"id": str(val["id"])}
            elif proto == "trojan" and val.get("password"):
                proxies[proto] = {"password": val["password"]}
            elif proto == "shadowsocks" and val.get("password"):
                proxies[proto] = {"password": val["password"],
                                  "method": val.get("method") or DEFAULT_SS_METHOD}
            elif proto in ("wireguard", "hysteria", "masque"):
                extra[proto] = val
        gids = u_groups.get(u.get("id"), [])
        gnames = [str(groups[g]["name"]) for g in gids if g in groups]
        allowed = sorted({t for g in gids if g in g_tags and not _b(groups[g].get("is_disabled")) for t in g_tags[g]})
        sd.users.append(UserRec(
            src_id=u.get("id"), username=str(u.get("username")), status=_status(u.get("status")),
            used_traffic=_i(u.get("used_traffic")), data_limit=_limit(u.get("data_limit")),
            reset_strategy=_reset(u.get("data_limit_reset_strategy")), expire=parse_dt(u.get("expire")),
            created_at=parse_dt(u.get("created_at")), note=_s(u.get("note")), admin_src_id=u.get("admin_id"),
            on_hold_expire_duration=_in(u.get("on_hold_expire_duration")),
            on_hold_timeout=parse_dt(u.get("on_hold_timeout")), sub_revoked_at=parse_dt(u.get("sub_revoked_at")),
            online_at=parse_dt(u.get("online_at")), auto_delete_in_days=_in(u.get("auto_delete_in_days")),
            proxies=proxies, extra_proxies=extra, group_names=gnames, allowed_tags=allowed,
        ))
    return _finish(sd)
