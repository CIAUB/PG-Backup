"""Authentication primitives for the web panel (stdlib + cryptography only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from typing import Dict, List, Optional, Tuple

# ---- passwords --------------------------------------------------------------
_N, _R, _P = 2 ** 15, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=128 * 1024 * 1024, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              maxmem=128 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


def check_password_strength(pw: str) -> Optional[str]:
    if len(pw) < 10:
        return "Password must be at least 10 characters."
    if pw.lower() == pw or pw.upper() == pw or not any(c.isdigit() for c in pw):
        return "Password must mix upper/lower case letters and digits."
    return None


# ---- TOTP (RFC 6238) --------------------------------------------------------
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return f"{(struct.unpack('>I', mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** 6:06d}"


def totp_now(secret: str, at: Optional[float] = None) -> str:
    return _hotp(secret, int((at or time.time()) // 30))


def verify_totp(secret: str, code: str, last_step: int = 0, at: Optional[float] = None) -> Optional[int]:
    """Return the matched time-step (so the caller can store it and refuse replays) or None."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    step = int((at or time.time()) // 30)
    for s in (step - 1, step, step + 1):
        if s > last_step and hmac.compare_digest(_hotp(secret, s), code):
            return s
    return None


def totp_uri(secret: str, account: str, issuer: str = "PG-Backup") -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


def new_recovery_codes(n: int = 8) -> Tuple[List[str], List[str]]:
    plain = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return plain, [hashlib.sha256(c.encode()).hexdigest() for c in plain]


def use_recovery_code(code: str, hashes: List[str]) -> Optional[List[str]]:
    h = hashlib.sha256((code or "").strip().lower().encode()).hexdigest()
    if h in hashes:
        return [x for x in hashes if x != h]
    return None


# ---- signed sessions --------------------------------------------------------
class Signer:
    def __init__(self, key: bytes):
        self.key = key

    def sign(self, payload: Dict, ttl: int) -> str:
        body = dict(payload, exp=int(time.time()) + ttl)
        raw = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).rstrip(b"=")
        sig = base64.urlsafe_b64encode(hmac.new(self.key, raw, hashlib.sha256).digest()).rstrip(b"=")
        return (raw + b"." + sig).decode()

    def load(self, token: str) -> Optional[Dict]:
        try:
            raw, sig = token.encode().split(b".")
            good = base64.urlsafe_b64encode(hmac.new(self.key, raw, hashlib.sha256).digest()).rstrip(b"=")
            if not hmac.compare_digest(sig, good):
                return None
            data = json.loads(base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4)))
            return data if data.get("exp", 0) > time.time() else None
        except Exception:
            return None


# ---- rate limit / lockout ---------------------------------------------------
class RateLimiter:
    """Per-key failure counter with exponential lockout. Keys are usernames AND
    source addresses (for throttling only — there is deliberately no IP allow-list)."""

    def __init__(self, max_fail: int = 5, base_lock: int = 30, max_lock: int = 900):
        self.max_fail, self.base_lock, self.max_lock = max_fail, base_lock, max_lock
        self._s: Dict[str, List[float]] = {}   # key -> [fails, locked_until]

    def retry_after(self, key: str) -> int:
        st = self._s.get(key)
        return max(0, int(st[1] - time.time())) if st else 0

    def fail(self, key: str) -> None:
        st = self._s.setdefault(key, [0, 0.0])
        st[0] += 1
        if st[0] >= self.max_fail:
            st[1] = time.time() + min(self.max_lock, self.base_lock * 2 ** (st[0] - self.max_fail))
        if len(self._s) > 5000:
            self._s = {k: v for k, v in self._s.items() if v[1] > time.time()}

    def ok(self, key: str) -> None:
        self._s.pop(key, None)


def random_path() -> str:
    return secrets.token_urlsafe(18).replace("_", "x").replace("-", "y")
