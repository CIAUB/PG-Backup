"""Minimal Telegram Bot API client (send message / get bot) with optional proxy."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple


def _call(token: str, method: str, data: dict, proxy: Optional[str], timeout: int = 20) -> Tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = urllib.parse.urlencode(data).encode()
    handlers = []
    ctx = None
    if proxy:
        p = urllib.parse.urlparse(proxy)
        if p.scheme in ("http", "https"):
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        elif p.scheme.startswith("socks"):
            try:
                import socks  # type: ignore
            except ImportError:
                return False, "PySocks is not installed (needed for SOCKS proxies)"
            kind = socks.SOCKS4 if p.scheme.startswith("socks4") else socks.SOCKS5
            orig = socket.socket
            socks.set_default_proxy(kind, p.hostname, p.port, rdns=p.scheme.endswith("h") or p.scheme == "socks4a",
                                    username=p.username, password=p.password)
            socket.socket = socks.socksocket
            ctx = orig
        else:
            return False, f"Unsupported proxy scheme '{p.scheme}'"
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(urllib.request.Request(url, data=body), timeout=timeout) as r:
            return True, r.read().decode()
    except urllib.error.HTTPError as e:
        try:
            desc = json.loads(e.read().decode()).get("description", "")
        except Exception:
            desc = ""
        return False, f"HTTP {e.code} {desc}".strip()
    except Exception as e:
        return False, str(e).replace(token, "***")
    finally:
        if ctx is not None:
            socket.socket = ctx


def test(token: str, chat: str, proxy: Optional[str]) -> Tuple[bool, str]:
    ok, res = _call(token, "getMe", {}, proxy)
    if not ok:
        return False, "Token/connection problem: " + res
    name = json.loads(res)["result"].get("username", "bot")
    ok, res = _call(token, "sendMessage", {"chat_id": chat, "text": "✅ PG-Backup panel: test message"}, proxy)
    return (True, f"Message sent via @{name}") if ok else (False, f"Bot @{name} is valid but sending failed: {res}")
