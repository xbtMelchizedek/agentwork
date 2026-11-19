import json
import socket
import sqlite3
import threading
import urllib.error
import urllib.request

import pytest

from agentwork import codec, render, store
from agentwork.indexer import Index, RoomPolicy
from test_indexer_render import T, demo

NOW = T + 3_600_000
_CREATE, _CONNECT = socket.create_connection, socket.socket.connect


@pytest.fixture
def loopback_only(monkeypatch):
    """Relax conftest's network guard for 127.0.0.1 only (the test's own server)."""
    def create(addr, *a, **k):
        if addr[0] != "127.0.0.1":
            raise RuntimeError("network access attempted in tests")
        return _CREATE(addr, *a, **k)

    def connect(self, addr):
        if not (isinstance(addr, tuple) and addr[0] == "127.0.0.1"):
            raise RuntimeError("network access attempted in tests")
        return _CONNECT(self, addr)
    monkeypatch.setattr(socket, "create_connection", create)
    monkeypatch.setattr(socket.socket, "connect", connect)


def direct(dump):
    idx = Index(); idx.add_dump(dump)
    reps = idx.apply_policy(RoomPolicy(), NOW)
    return render.feed_json(idx, NOW, reps)


@pytest.fixture
def demo_db(tmp_path):
    demo()
    db = store.connect(tmp_path / "index.sqlite")
    from conftest import ROOT
    store.ingest_files(db, [ROOT / "examples" / "demo_room.json"])
    return db, tmp_path / "index.sqlite"


def test_same_output_as_feed_json(demo_db):
    db, _ = demo_db
    want = direct(demo())
    got = store.feed(db, NOW)
    for k in ("items", "jobs", "social", "format", "note"):
        assert got.get(k) == want.get(k), k
    # the db keeps only validated messages: chatter/invalid/mismatch are not persisted
    assert got["stats"]["valid"] == want["stats"]["valid"]
    assert "chatter" not in got["stats"]


def test_ingest_twice_is_idempotent(demo_db):
    db, _ = demo_db
    from conftest import ROOT
    n = db.execute("SELECT COUNT(*) FROM msgs").fetchone()[0]
    assert n > 0
    assert store.ingest_files(db, [ROOT / "examples" / "demo_room.json"]) == 0
    assert db.execute("SELECT COUNT(*) FROM msgs").fetchone()[0] == n
    assert store.feed(db, NOW) == store.feed(db, NOW)


def test_tampered_row_is_dropped_on_read(demo_db):
    db, _ = demo_db
    cid, line = db.execute("SELECT cid, line FROM msgs ORDER BY ord LIMIT 1").fetchone()
    db.execute("UPDATE msgs SET line=? WHERE cid=?", (line.replace('"ts":', '"ts": ', 1), cid))
    db.commit()
    idx = store.load(db)
    assert cid not in idx.msgs and idx.stats["invalid"] == 1


def test_query_filters(demo_db):
    db, _ = demo_db
    doc = store.feed(db, NOW)
    asks = store.query(doc, "/feed", {"intent": ["ask"]})["items"]
    assert asks and all(i["t"] == "ask" for i in asks)
    room = doc["items"][0]["room"]
    assert all(i["room"] == room for i in store.query(doc, "/feed", {"room": [room]})["items"])
    assert store.query(doc, "/feed", {"room": ["nope/none"]})["items"] == []
    newest = max(i["ts"] for i in doc["items"])
    assert store.query(doc, "/feed", {"since": [str(newest)]})["items"] == []
    open_ = store.query(doc, "/jobs", {"status": ["open"]})["jobs"]
    assert [j["status"] for j in open_] == ["open"]
    with pytest.raises(ValueError):
        store.query(doc, "/feed", {"since": ["-1"]})
    with pytest.raises(ValueError):
        store.query(doc, "/feed", {"room": ["a", "b"]})
    with pytest.raises(KeyError):
        store.query(doc, "/admin", {})


def _req(base, path, method="GET"):
    r = urllib.request.Request(base + path, method=method, data=b"{}" if method in ("POST", "PUT") else None)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_http_read_only(demo_db, loopback_only):
    _, path = demo_db
    before = sqlite3.connect(path).execute("SELECT COUNT(*) FROM msgs").fetchone()[0]
    srv = store.serve(path, "127.0.0.1", 0, now=lambda: NOW)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        code, h, body = _req(base, "/feed?intent=ask")
        assert code == 200 and h["Access-Control-Allow-Origin"] == "*"
        assert "POST" not in h["Access-Control-Allow-Methods"]
        assert all(i["t"] == "ask" for i in json.loads(body)["items"])
        code, _, body = _req(base, "/jobs")
        assert code == 200 and json.loads(body)["jobs"] == direct(demo())["jobs"]
        for m in ("POST", "PUT", "DELETE", "PATCH"):
            code, h, _ = _req(base, "/feed", m)
            assert code == 405 and h["Allow"] == "GET, OPTIONS"
        assert _req(base, "/feed", "OPTIONS")[0] == 204
        assert _req(base, "/")[0] == 404
        assert _req(base, "/feed?since=x")[0] == 400
    finally:
        srv.shutdown(); srv.server_close()
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM msgs").fetchone()[0] == before


def test_fresh_db_serves_empty(tmp_path):
    db = store.connect(tmp_path / "e.sqlite")
    doc = store.feed(db, NOW)
    assert doc["items"] == [] and doc["jobs"] == []
    assert codec.PREFIX == "agb1 "
