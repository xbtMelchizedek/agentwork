"""Ed25519 `did:key` helpers: key generation, DID encoding, sign and verify.

Uses the `cryptography` package. Private keys never leave this module except as
an opaque `SigningKey` object or a mode-600 key file written by `save_key`.
"""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

PREFIX = "did:key:"
MULTICODEC_ED25519 = b"\xed\x01"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58)}
DID_RE = re.compile(r"^did:key:z6Mk[1-9A-HJ-NP-Za-km-z]{44}$")
SIG_RE = re.compile(r"^[A-Za-z0-9_-]{85}[AQgw]$")


class DidError(ValueError):
    pass


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    lead = len(data) - len(data.lstrip(b"\x00"))
    return "1" * lead + out


def b58decode(raw: str) -> bytes:
    n = 0
    for ch in raw:
        d = _B58_INDEX.get(ch)
        if d is None:
            raise DidError("not base58btc")
        n = n * 58 + d
    lead = len(raw) - len(raw.lstrip("1"))
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * lead + body


def did_from_public(pub: bytes) -> str:
    if len(pub) != 32:
        raise DidError("Ed25519 public key must be 32 bytes")
    return PREFIX + "z" + b58encode(MULTICODEC_ED25519 + pub)


def public_key(did: str) -> bytes:
    if not isinstance(did, str) or not DID_RE.fullmatch(did):
        raise DidError("bad did:key (expected did:key:z6Mk… Ed25519)")
    raw = b58decode(did[len(PREFIX) + 1:])
    if len(raw) != 34 or not raw.startswith(MULTICODEC_ED25519):
        raise DidError("bad did:key: only ed25519-pub keys")
    return raw[2:]


def is_did(value: object) -> bool:
    try:
        public_key(value)  # type: ignore[arg-type]
        return True
    except DidError:
        return False


def abbreviate(did: str) -> str:
    mb = did[len(PREFIX):] if isinstance(did, str) and did.startswith(PREFIX) else str(did)
    return f"{mb[:4]}…{mb[-4:]}" if len(mb) > 10 else mb


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SigningKey:
    """An Ed25519 signing key. repr() never shows secret material."""

    __slots__ = ("_sk", "did")

    def __init__(self, sk: Ed25519PrivateKey):
        self._sk = sk
        pub = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.did = did_from_public(pub)

    @classmethod
    def generate(cls) -> "SigningKey":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_seed(cls, seed: bytes) -> "SigningKey":
        if len(seed) != 32:
            raise ValueError("seed must be 32 bytes")
        return cls(Ed25519PrivateKey.from_private_bytes(seed))

    def sign(self, message: bytes) -> str:
        return b64u(self._sk.sign(message))

    def __repr__(self) -> str:
        return f"SigningKey(did={self.did!r})"


def verify(did: str, sig: str, message: bytes) -> bool:
    try:
        pub = public_key(did)
    except DidError:
        return False
    if not isinstance(sig, str) or not SIG_RE.fullmatch(sig):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(b64u_decode(sig), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def save_key(key: SigningKey, path: Path) -> None:
    """Write a raw 32-byte seed with mode 600. Refuses to overwrite."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = key._sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(seed)


def load_key(path: Path) -> SigningKey:
    data = Path(path).read_bytes()
    if len(data) != 32:
        raise ValueError("key file must hold a raw 32-byte Ed25519 seed")
    return SigningKey.from_seed(data)
