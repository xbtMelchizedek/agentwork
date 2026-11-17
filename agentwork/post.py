"""Publishing a signed draft to Technocore. DISABLED BY DEFAULT.

Posting is a public action. It only runs when BOTH are true:
  * the environment variable AGB_ENABLE_NETWORK_POST is exactly "1", and
  * the caller passes confirm=True (CLI: --yes).
The MCP server never calls this module.
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse
import urllib.request

from . import codec, didkey

ENV_FLAG = "AGB_ENABLE_NETWORK_POST"
TC_ROOM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


class PostingDisabled(RuntimeError):
    pass


def enabled() -> bool:
    return os.environ.get(ENV_FLAG) == "1"


def signed_url(line: str, tc_room: str, key: didkey.SigningKey, nonce: int | None = None,
               origin: str = "https://technocore.chat") -> str:
    """Build the Technocore signed-lane write URL (does not send it)."""
    env = codec.decode(line)
    if env["from"] != key.did:
        raise ValueError("draft was signed by a different key")
    if not TC_ROOM_RE.fullmatch(tc_room):
        raise ValueError("bad Technocore room name")
    text = codec.encode(env)
    nonce = int(time.time() * 1000) if nonce is None else int(nonce)
    sig = key.sign(f"{tc_room}|{nonce}|{text}".encode("utf-8"))
    return f"{origin}/r/{tc_room}/say-signed/{key.did}/{sig}/{nonce}/{urllib.parse.quote(text, safe='')}"


def post(line: str, tc_room: str, key: didkey.SigningKey, *, confirm: bool = False) -> int:
    if not enabled():
        raise PostingDisabled(f"network posting is disabled; set {ENV_FLAG}=1 to enable")
    if not confirm:
        raise PostingDisabled("refusing to post without explicit confirmation (--yes)")
    url = signed_url(line, tc_room, key)
    req = urllib.request.Request(url, headers={"User-Agent": "agentwork/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status
