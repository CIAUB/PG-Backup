"""Neutral intermediate model shared by every reader and writer.

A migration is ``source DB --reader--> SourceData --writer--> target DB``.
Readers never know about the target; writers never know about the source.
"""
from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

# Protocols every PasarGuard / Marzban user can carry credentials for.
CORE_PROTOCOLS = ("vmess", "vless", "trojan", "shadowsocks")

USER_STATUSES = ("active", "disabled", "limited", "expired", "on_hold")
RESET_STRATEGIES = ("no_reset", "day", "week", "month", "year")

# Shadowsocks methods PasarGuard accepts for a user.
PG_SS_METHODS = ("aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "xchacha20-poly1305")
DEFAULT_SS_METHOD = "chacha20-ietf-poly1305"
_SS_ALIASES = {
    "chacha20-poly1305": "chacha20-ietf-poly1305",
    "chacha20-ietf-poly1305": "chacha20-ietf-poly1305",
    "xchacha20-ietf-poly1305": "xchacha20-poly1305",
    "xchacha20-poly1305": "xchacha20-poly1305",
    "aes-128-gcm": "aes-128-gcm",
    "aes-256-gcm": "aes-256-gcm",
}


def normalize_ss_method(method: Optional[str]) -> Optional[str]:
    """Map a source method name to a PasarGuard-supported one, or None when it
    has no equivalent (e.g. the 2022-blake3 family)."""
    if not method:
        return DEFAULT_SS_METHOD
    return _SS_ALIASES.get(str(method).strip().lower())


def random_password() -> str:
    """Same format PasarGuard itself generates (``secrets.token_urlsafe(24)``)."""
    return secrets.token_urlsafe(24)


@dataclass
class AdminRec:
    src_id: Any
    username: str
    hashed_password: Optional[str] = None
    is_sudo: bool = False
    telegram_id: Optional[int] = None
    created_at: Optional[datetime] = None
    used_traffic: int = 0


@dataclass
class GroupRec:
    name: str
    inbound_tags: List[str] = field(default_factory=list)
    disabled: bool = False


@dataclass
class UserRec:
    src_id: Any
    username: str
    status: str = "active"
    used_traffic: int = 0
    data_limit: Optional[int] = None          # bytes, None = unlimited
    reset_strategy: str = "no_reset"
    expire: Optional[datetime] = None         # naive UTC
    created_at: Optional[datetime] = None
    note: Optional[str] = None
    admin_src_id: Any = None
    on_hold_expire_duration: Optional[int] = None   # seconds
    on_hold_timeout: Optional[datetime] = None
    sub_revoked_at: Optional[datetime] = None
    online_at: Optional[datetime] = None
    auto_delete_in_days: Optional[int] = None
    # protocol -> settings. vmess/vless: {"id"}; trojan: {"password"};
    # shadowsocks: {"password","method"}. Missing protocols get fresh random
    # credentials when written (that is what PasarGuard itself does).
    proxies: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # extra PasarGuard-only protocol blocks (wireguard/hysteria/masque) that
    # must survive a PasarGuard -> PasarGuard conversion untouched.
    extra_proxies: Dict[str, Any] = field(default_factory=dict)
    group_names: List[str] = field(default_factory=list)       # source groups / services
    allowed_tags: Optional[List[str]] = None                   # inbound tags the user may use
    excluded_tags: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    original_username: Optional[str] = None                    # set when renamed during sanitising


@dataclass
class SourceData:
    kind: str                                   # marzban / marzneshin / rebecca / 3xui / pasarguard
    admins: List[AdminRec] = field(default_factory=list)
    users: List[UserRec] = field(default_factory=list)
    groups: List[GroupRec] = field(default_factory=list)
    inbound_tags: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)


# --------------------------------------------------------------------------
# username handling
# --------------------------------------------------------------------------
_USERNAME_OK = re.compile(r"^[a-zA-Z0-9\-_@.]+$")
_SPECIAL_RUN = re.compile(r"[-_@.]{2,}")


def username_is_valid(name: str) -> bool:
    return (name is not None and 3 <= len(name) <= 128 and bool(_USERNAME_OK.match(name))
            and not _SPECIAL_RUN.search(name))


def sanitize_username(name: str) -> str:
    """Turn any string into a username PasarGuard/Marzban accept.

    Rules mirrored from PasarGuard's ``UserValidator.validate_username``:
    3–128 chars, ``[a-zA-Z0-9-_@.]`` only and no run of consecutive special
    characters. Already-valid names are returned unchanged."""
    if username_is_valid(name):
        return name
    s = re.sub(r"[^a-zA-Z0-9\-_@.]", "_", (name or "").strip())
    s = re.sub(r"[-_@.]{2,}", lambda m: m.group(0)[0], s)
    s = s.strip("-_@.") or "user"
    if len(s) < 3:
        s = s + "x" * (3 - len(s))
    return s[:128]


def uuid_from_hex(value: str) -> Optional[str]:
    """Return a canonical UUID string for a 32-hex or dashed UUID, else None."""
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        return None
