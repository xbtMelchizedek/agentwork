"""SQLite persistence for validated agb1 messages and a read-only JSON API (issue 09).

The database is a cache, not a source of truth: it stores the canonical wire line of
each message that passed `codec.decode`, keyed by cid, and every read re-decodes and
re-verifies those lines through `Index.add_line`. A tampered row is dropped, never served.

The HTTP server is GET-only. It never fetches, posts or signs; room and intent filters
come from the query string and only select from what is already in the database.
"""
from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qs, urlsplit

from . import codec, render
from .indexer import Index, RoomPolicy

SCHEMA = """
CREATE TABLE IF NOT EXISTS msgs (
  ord    INTEGER PRIMARY KEY AUTOINCREMENT,
  cid    TEXT NOT NULL UNIQUE,
  line   TEXT NOT NULL,
  room   TEXT,
  seq    INTEGER,
  signed INTEGER NOT NULL DEFAULT 0
);
"""
MAX_QUERY = 256


def connect(path: Path | str) -> sqlite3.Connection:
    db = sqlite3.connect(str(path))
    db.executescript(SCHEMA)
    return db


def ingest(db: sqlite3.Connection, idx: Index) -> int:
    """Insert every validated message of `idx` in arrival order. Dedupe by cid; returns rows added."""
    before = db.total_changes
    for cid in sorted(idx.msgs, key=lambda c: idx.arrival.get(c, 1 << 62)):
        env = idx.msgs[cid]
        tr = idx.transport.get(cid) or {}
        seq = tr.get("seq") if isinstance(tr.get("seq"), int) and not isinstance(tr.get("seq"), bool) else None
        db.execute("INSERT OR IGNORE INTO msgs (cid, line, room, seq, signed) VALUES (?,?,?,?,?)",
                   (cid, codec.encode(env), tr.get("room") if isinstance(tr.get("room"), str) else None,
                    seq, 1 if tr.get("signed") else 0))
    db.commit()
    return db.total_changes - before


def ingest_files(db: sqlite3.Connection, paths: Iterable[Path]) -> int:
    idx = Index()
    for p in paths:
        idx.add_file(Path(p))
    return ingest(db, idx)


def load(db: sqlite3.Connection) -> Index:
    """Rebuild an Index from the database, re-verifying every stored line."""
    idx = Index()
    for line, room, seq, signed in db.execute("SELECT line, room, seq, signed FROM msgs ORDER BY ord"):
        frm = None
        if signed:
            try:
                frm = json.loads(line[len(codec.PREFIX):]).get("from")
            except (ValueError, AttributeError):
                frm = None
        idx.add_line(line, frm if isinstance(frm, str) else None, room, seq)
    return idx


def feed(db: sqlite3.Connection, now_ms: int, tz: str = "UTC") -> dict:
    idx = load(db)
    reps = idx.apply_policy(RoomPolicy(), now_ms)
    return render.feed_json(idx, now_ms, reps, tz)


def _one(qs: dict, key: str) -> str | None:
    v = qs.get(key)
    if not v:
        return None
    if len(v) != 1 or len(v[0]) > MAX_QUERY:
        raise ValueError(f"bad {key}")
    return v[0]


def query(doc: dict, path: str, qs: dict) -> dict:
    """Apply GET /feed or GET /jobs filters to a feed_json document. Raises KeyError/ValueError."""
    room = _one(qs, "room")
    if path == "/feed":
        intent, since = _one(qs, "intent"), _one(qs, "since")
        if since is not None and not since.isdigit():
            raise ValueError("since must be a non-negative integer (ms)")
        items = [i for i in doc["items"]
                 if (room is None or i["room"] == room) and (intent is None or i["t"] == intent)
                 and (since is None or i["ts"] > int(since))]
        return {"format": doc["format"], "generated_ms": doc["generated_ms"], "items": items, "note": doc["note"]}
    if path == "/jobs":
        status = _one(qs, "status")
        jobs = [j for j in doc["jobs"] if (room is None or j["room"] == room) and (status is None or j["status"] == status)]
        return {"format": doc["format"], "generated_ms": doc["generated_ms"], "jobs": jobs, "note": doc["note"]}
    raise KeyError(path)


def make_handler(db_path: Path | str, now: Callable[[], int], tz: str = "UTC"):
    class Handler(BaseHTTPRequestHandler):
        server_version = "agentwork"
        sys_version = ""

        def _send(self, code: int, body: dict | None, extra: dict | None = None):
            raw = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)

        def do_GET(self):
            u = urlsplit(self.path)
            try:
                qs = parse_qs(u.query, keep_blank_values=False, max_num_fields=8)
                db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    doc = feed(db, now(), tz)
                finally:
                    db.close()
                self._send(200, query(doc, u.path, qs))
            except KeyError:
                self._send(404, {"error": "not found", "endpoints": ["/feed", "/jobs"]})
            except ValueError as e:
                self._send(400, {"error": str(e)[:120]})

        def do_OPTIONS(self):
            self._send(204, None, {"Access-Control-Max-Age": "600"})

        def _no(self):
            self._send(405, {"error": "read-only"}, {"Allow": "GET, OPTIONS"})

        do_POST = do_PUT = do_PATCH = do_DELETE = _no

        def log_message(self, *a):  # request lines can carry untrusted text; keep stdout clean
            pass

    return Handler


def serve(db_path: Path | str, host: str = "127.0.0.1", port: int = 8787, now=None, tz: str = "UTC") -> ThreadingHTTPServer:
    import time
    clock = now or (lambda: int(time.time() * 1000))
    connect(db_path).close()  # create the schema so a fresh db serves an empty feed
    return ThreadingHTTPServer((host, port), make_handler(db_path, clock, tz))
