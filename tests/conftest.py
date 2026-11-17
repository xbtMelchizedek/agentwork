import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never touch the network."""
    def guard(*a, **k):
        raise RuntimeError("network access attempted in tests")
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture
def keys():
    from agentwork.didkey import SigningKey
    return {n: SigningKey.generate() for n in "abcdefghijklmnopqrstuvwxyz"}  # fresh keys, never persisted
