"""Pure-Python XXH3-128 (seed 0, default secret) for inputs up to 240 bytes.

Marzneshin derives every user's VMess/VLESS UUID and Trojan/Shadowsocks
password from the user's 'key' column with xxhash.xxh128. Re-deriving
those credentials must be bit-exact, otherwise migrated users could not
connect. We use the real xxhash module when it is installed and fall back
to this implementation, which is verified against libxxhash in the tests
(every length 0..240). Keys in Marzneshin are at most 64 bytes, so the
long-input (>240 byte) code path is intentionally not implemented.
"""
from __future__ import annotations

_M64 = (1 << 64) - 1
_M32 = (1 << 32) - 1

_SECRET = bytes.fromhex(
    "b8fe6c3923a44bbe7c01812cf721ad1cded46de9839097db7240a4a4b7b3671fcb79e64eccc0e578825ad07dccff7221b8084674f743248ee03590e6813a264c3c2852bb91c300cb88d0658b1b532ea371644897a20df94e3819ef46a9deacd8a8fa763fe39c343ff9dcbbc7c70b4f1d8a51e04bcdb45931c89f7ec9d9787364eac5ac8334d3ebc3c581a0fffa1363eb170ddd51b7f0da49d316552629d4689e2b16be587d47a1fc8ff8b8d17ad031ce45cb3a8f95160428afd7fbcabb4b407e"
)

_P32_1 = 0x9E3779B1
_P32_2 = 0x85EBCA77
_P32_3 = 0xC2B2AE3D
_P64_1 = 0x9E3779B185EBCA87
_P64_2 = 0xC2B2AE3D27D4EB4F
_P64_3 = 0x165667B19E3779F9
_P64_4 = 0x85EBCA77C2B2AE63
_P64_5 = 0x27D4EB2F165667C5
_MX1 = 0x165667919E3779F9
_MX2 = 0x9FB21C651E98DF25


def _r32(b, o):
    return int.from_bytes(b[o:o + 4], "little")


def _r64(b, o):
    return int.from_bytes(b[o:o + 8], "little")


def _rotl64(x, r):
    return ((x << r) | (x >> (64 - r))) & _M64


def _rotl32(x, r):
    return ((x << r) | (x >> (32 - r))) & _M32


def _swap32(x):
    return int.from_bytes(x.to_bytes(4, "little"), "big")


def _swap64(x):
    return int.from_bytes(x.to_bytes(8, "little"), "big")


def _xxh64_avalanche(h):
    h ^= h >> 33
    h = (h * _P64_2) & _M64
    h ^= h >> 29
    h = (h * _P64_3) & _M64
    h ^= h >> 32
    return h


def _xxh3_avalanche(h):
    h ^= h >> 37
    h = (h * _MX1) & _M64
    h ^= h >> 32
    return h


def _mul128(a, b):
    p = a * b
    return p & _M64, p >> 64


def _mul128_fold64(a, b):
    lo, hi = _mul128(a, b)
    return lo ^ hi


def _mix16(data, o, secret, so):
    lo = _r64(data, o)
    hi = _r64(data, o + 8)
    return _mul128_fold64(lo ^ _r64(secret, so), hi ^ _r64(secret, so + 8))


def _mix32(acc, data, o1, o2, secret, so, seed=0):
    lo, hi = acc
    lo = (lo + _mix16(data, o1, secret, so)) & _M64
    lo ^= (_r64(data, o2) + _r64(data, o2 + 8)) & _M64
    hi = (hi + _mix16(data, o2, secret, so + 16)) & _M64
    hi ^= (_r64(data, o1) + _r64(data, o1 + 8)) & _M64
    return lo, hi


def _finish_mid(acc, n):
    lo, hi = acc
    h_lo = (lo + hi) & _M64
    h_hi = (lo * _P64_1 + hi * _P64_4 + n * _P64_2) & _M64
    h_lo = _xxh3_avalanche(h_lo)
    h_hi = (0 - _xxh3_avalanche(h_hi)) & _M64
    return h_lo, h_hi


def _xxh3_128_pair(data):
    n = len(data)
    s = _SECRET
    if n == 0:
        flip_l = _r64(s, 64) ^ _r64(s, 72)
        flip_h = _r64(s, 80) ^ _r64(s, 88)
        return _xxh64_avalanche(flip_l), _xxh64_avalanche(flip_h)
    if n <= 3:
        c1, c2, c3 = data[0], data[n >> 1], data[n - 1]
        comb_l = (c1 << 16) | (c2 << 24) | c3 | (n << 8)
        comb_h = _rotl32(_swap32(comb_l), 13)
        flip_l = _r32(s, 0) ^ _r32(s, 4)
        flip_h = _r32(s, 8) ^ _r32(s, 12)
        return _xxh64_avalanche((comb_l ^ flip_l) & _M64), _xxh64_avalanche((comb_h ^ flip_h) & _M64)
    if n <= 8:
        in_lo = _r32(data, 0)
        in_hi = _r32(data, n - 4)
        in64 = in_lo + (in_hi << 32)
        flip = _r64(s, 16) ^ _r64(s, 24)
        keyed = in64 ^ flip
        lo, hi = _mul128(keyed, (_P64_1 + (n << 2)) & _M64)
        hi = (hi + (lo << 1)) & _M64
        lo ^= hi >> 3
        lo ^= lo >> 35
        lo = (lo * _MX2) & _M64
        lo ^= lo >> 28
        hi = _xxh3_avalanche(hi)
        return lo, hi
    if n <= 16:
        flip_l = _r64(s, 32) ^ _r64(s, 40)
        flip_h = _r64(s, 48) ^ _r64(s, 56)
        in_lo = _r64(data, 0)
        in_hi = _r64(data, n - 8)
        m_lo, m_hi = _mul128(in_lo ^ in_hi ^ flip_l, _P64_1)
        m_lo = (m_lo + ((n - 1) << 54)) & _M64
        in_hi ^= flip_h
        m_hi = (m_hi + in_hi + (in_hi & _M32) * (_P32_2 - 1)) & _M64
        m_lo ^= _swap64(m_hi)
        h_lo, h_hi = _mul128(m_lo, _P64_2)
        h_hi = (h_hi + m_hi * _P64_2) & _M64
        return _xxh3_avalanche(h_lo), _xxh3_avalanche(h_hi)
    if n <= 128:
        acc = ((n * _P64_1) & _M64, 0)
        if n > 32:
            if n > 64:
                if n > 96:
                    acc = _mix32(acc, data, 48, n - 64, s, 96)
                acc = _mix32(acc, data, 32, n - 48, s, 64)
            acc = _mix32(acc, data, 16, n - 32, s, 32)
        acc = _mix32(acc, data, 0, n - 16, s, 0)
        return _finish_mid(acc, n)
    if n <= 240:
        rounds = n // 32
        acc = ((n * _P64_1) & _M64, 0)
        for i in range(32, 160, 32):
            acc = _mix32(acc, data, i - 32, i - 16, s, i - 32)
        acc = (_xxh3_avalanche(acc[0]), _xxh3_avalanche(acc[1]))
        for i in range(160, rounds * 32 + 1, 32):
            acc = _mix32(acc, data, i - 32, i - 16, s, 3 + i - 160)
        acc = _mix32(acc, data, n - 16, n - 32, s, 136 - 17 - 16)
        return _finish_mid(acc, n)
    raise ValueError("pure-python XXH3-128 supports inputs up to 240 bytes")


def xxh128_digest(data: bytes) -> bytes:
    """Canonical (big-endian, high64 first) 16-byte digest — matches
    xxhash.xxh128(data).digest()."""
    lo, hi = _xxh3_128_pair(bytes(data))
    return hi.to_bytes(8, "big") + lo.to_bytes(8, "big")


def xxh128_hexdigest(data: bytes) -> str:
    return xxh128_digest(data).hex()


try:  # prefer the C implementation when available
    import xxhash as _xxhash  # type: ignore

    def digest(data: bytes) -> bytes:
        return _xxhash.xxh128(data).digest()
except Exception:  # pragma: no cover - depends on the environment
    digest = xxh128_digest
