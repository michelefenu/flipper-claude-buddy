"""JSON protocol for Flipper <-> Host Bridge communication."""

import json
import time
import unicodedata
import uuid

def make_id() -> str:
    return uuid.uuid4().hex[:8]


_ASCII_MAP = str.maketrans({
    "—": "-", "–": "-",          # em/en dash
    "“": '"', "”": '"',          # curly double quotes
    "‘": "'", "’": "'",          # curly single quotes
    "…": "...", "→": "->", "•": "*",
})


def to_ascii(text: str) -> str:
    """Flipper fonts are ASCII-only and its JSON parser doesn't decode \\uXXXX:
    strip accents, map common symbols, drop anything else (emoji, CJK...).

    >>> to_ascii("perché — “sì” … ✅")
    'perche - "si" ... '
    """
    decomposed = unicodedata.normalize("NFKD", text.translate(_ASCII_MAP))
    return decomposed.encode("ascii", "ignore").decode()


def encode(msg_type: str, data: dict | None = None) -> bytes:
    msg = {
        "v": 1,
        "t": msg_type,
        "d": {k: to_ascii(v) if isinstance(v, str) else v for k, v in (data or {}).items()},
    }
    return json.dumps(msg, separators=(",", ":")).encode() + b"\n"


def decode(line: bytes) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
        if isinstance(msg, dict) and "t" in msg:
            return msg
    except json.JSONDecodeError:
        pass
    return None


# Flipper wraps the subtext over up to 3 lines; its field buffer is 64 bytes.
SUBTEXT_MAX = 63


def notify_msg(sound: str, vibro: bool = True, text: str = "", subtext: str = "") -> bytes:
    d: dict = {"sound": sound, "vibro": vibro, "text": text}
    if subtext:
        d["sub"] = subtext[:SUBTEXT_MAX]
    return encode("notify", d)


def state_msg(claude_connected: bool = False) -> bytes:
    return encode("state", {"claude": claude_connected})


def status_msg(line1: str, line2: str = "") -> bytes:
    d: dict = {"line1": line1[:21]}
    if line2:
        d["line2"] = line2[:SUBTEXT_MAX]
    return encode("status", d)


def ping_msg(rssi: int | None = None) -> bytes:
    d: dict[str, int] = {}
    if rssi is not None:
        d["rssi"] = int(rssi)
    return encode("ping", d)


def menu_msg(items: list[str]) -> bytes:
    return encode("menu", {"items": "|".join(items)})


def perm_msg(tool: str, detail: str = "") -> bytes:
    d: dict = {"tool": tool[:21]}
    if detail:
        d["detail"] = detail[:21]
    return encode("perm", d)
