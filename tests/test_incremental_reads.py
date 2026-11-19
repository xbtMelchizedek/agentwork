"""Issue 01: incremental Technocore reads with per-room cursors (no network)."""
import io
import json
import urllib.error

import pytest

from agentwork import indexer, safe
from agentwork.indexer import fetch_room, fetch_room_since, load_cursors, room_url, save_cursors


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class StubOpener:
    """Serves a fixed room log; honours `since` like the server would (or ignores it)."""

    def __init__(self, seqs, honour_since=True):
        self.seqs, self.honour_since, self.urls = list(seqs), honour_since, []

    def open(self, req, timeout=None):
        url = req.full_url
        self.urls.append(url)
        since = 0
        if self.honour_since and "since=" in url:
            since = int(url.split("since=")[1].split("&")[0])
        msgs = [{"seq": s, "from": "did:key:z6MkTEST", "text": f"m{s}"} for s in self.seqs if s > since]
        return _Resp(json.dumps({"messages": msgs}).encode())


def test_since_url_shape_passes_allowlist():
    url = room_url("builders", 50, since=123)
    assert url == "https://technocore.chat/r/builders?format=json&limit=50&since=123"
    assert safe.assert_read_url(url) == url
    with pytest.raises(ValueError):
        room_url("builders", since=-1)


def test_fetch_room_uses_since_and_stub_opener():
    op = StubOpener([1, 2, 3])
    d = fetch_room("builders", 10, since=2, opener=op)
    assert op.urls == ["https://technocore.chat/r/builders?format=json&limit=10&since=2"]
    assert [m["seq"] for m in d["messages"]] == [3]


@pytest.mark.parametrize("honour", [True, False])
def test_two_calls_second_only_new(tmp_path, honour):
    cur = tmp_path / "cursors.json"
    op = StubOpener([5, 6, 7], honour_since=honour)
    d1 = fetch_room_since("builders", cur, opener=op)
    assert [m["seq"] for m in d1["messages"]] == [5, 6, 7]
    assert load_cursors(cur) == {"builders": 7}
    op.seqs += [8, 9]
    d2 = fetch_room_since("builders", cur, opener=op)
    assert [m["seq"] for m in d2["messages"]] == [8, 9]
    assert "since=7" in op.urls[-1]
    assert load_cursors(cur) == {"builders": 9}
    d3 = fetch_room_since("builders", cur, opener=op)
    assert d3["messages"] == [] and d3["last_seq"] == 9


def test_cursor_is_per_room(tmp_path):
    cur = tmp_path / "c.json"
    fetch_room_since("builders", cur, opener=StubOpener([1, 2]))
    fetch_room_since("lobby", cur, opener=StubOpener([40]))
    assert load_cursors(cur) == {"builders": 2, "lobby": 40}


def test_reaped_room_resets_without_crash(tmp_path):
    cur = tmp_path / "c.json"
    save_cursors(cur, {"builders": 500})
    logs = []
    # server ignores since; room was reaped and restarted at seq 1
    d = fetch_room_since("builders", cur, opener=StubOpener([1, 2, 3], honour_since=False), log=logs.append)
    assert d["cursor_reset"] is True
    assert [m["seq"] for m in d["messages"]] == [1, 2, 3]
    assert load_cursors(cur) == {"builders": 3}
    assert logs and "reaped" in logs[0]


def test_atomic_save_and_corrupt_file(tmp_path):
    cur = tmp_path / "c.json"
    cur.write_text("{not json")
    assert load_cursors(cur) == {}
    cur.write_text(json.dumps({"ok": 3, "neg": -1, "bad": "x", "b": True}))
    assert load_cursors(cur) == {"ok": 3}
    save_cursors(cur, {"a": 1})
    assert json.loads(cur.read_text()) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["c.json"]  # no temp files left behind


def test_redirects_still_refused():
    h = indexer._NoRedirect()
    assert h.redirect_request(None, None, 302, "Found", {}, "https://evil.example/") is None
    with pytest.raises(ValueError):
        fetch_room("../say/x", since=1, opener=StubOpener([]))
