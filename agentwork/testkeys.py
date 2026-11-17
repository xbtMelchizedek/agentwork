"""PUBLIC, deterministic test keys for vectors and demos. Never use for anything of value.

seed = sha256("agentwork public test key: " + label)
"""
from __future__ import annotations

import hashlib

from .didkey import SigningKey


def test_key(label: str) -> SigningKey:
    return SigningKey.from_seed(hashlib.sha256(b"agentwork public test key: " + label.encode()).digest())
