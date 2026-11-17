import json
import subprocess
import sys

import pytest

from agentwork import codec, render, safe
from agentwork.indexer import Index, RoomPolicy, fetch_room
from conftest import ROOT

T = 1794900000000


def demo():
    r = subprocess.run([sys.executable, "examples/demo.py"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads((ROOT / "examples" / "demo_room.json").read_text())


def test_demo_end_to_end():
    dump = demo()
    idx = Index(); idx.add_dump(dump)
    reps = idx.apply_policy(RoomPolicy(), T + 3_600_000)
    s = idx.stats
    assert s["chatter"] == 41 and s["invalid"] == 1 and s["signer_mismatch"] == 1
    assert s["hidden:template spam"] == 3 and s["hidden:rate limit"] == 3
    jobs = idx.jobs(T + 3_600_000, reps)
    assert sorted(j["status"] for j in jobs) == ["open", "settled+attested"]
    paid = [j for j in jobs if j["status"] == "settled+attested"][0]
    worker = paid["receipts"][0]["b"]["payee"]
    assert reps[worker].score > 0 and reps[worker].receipts == 1


def test_rendered_outputs_are_safe(tmp_path):
    demo()
    html_ = (ROOT / "examples" / "out" / "index.html").read_text()
    feed = (ROOT / "examples" / "out" / "feed.json").read_text()
    md = (ROOT / "examples" / "out" / "feed.md").read_text()
    assert "default-src 'none'" in html_ and "<script" not in html_ and "href=" not in html_
    for out in (html_, feed, md):
        assert "https://" not in out and "evil.example.xyz" not in out
        assert "Ignore previous instructions" not in out
    assert "[link:evil[.]example[.]xyz]" in md


def test_html_escapes_markup(keys):
    idx = Index()
    env = codec.build(keys["a"], "ask", "core/x", {"task": "<script>alert(1)</script><img src=x onerror=y>", "budget": {"asset": "FLOP", "max": "1"}})
    idx.add_line(codec.encode(env))
    reps = idx.apply_policy(RoomPolicy(), T)
    page = render.page_html(idx, T, reps)
    assert "<script>" not in page and "<img" not in page and "&lt;script&gt;" in page


def test_duplicates_and_transport_mismatch(keys):
    idx = Index()
    env = codec.build(keys["a"], "note", "core/x", {"text": "hi"})
    line = codec.encode(env)
    idx.add_line(line); idx.add_line(line)
    idx.add_line(line, transport_from=keys["b"].did)
    idx.add_line(line, transport_from="nick")  # unsigned transport lane is fine: envelope self-verifies
    assert idx.stats["valid"] == 1 and idx.stats["duplicate"] == 2 and idx.stats["signer_mismatch"] == 1


def test_room_policy_intents_and_gate(keys):
    idx = Index()
    idx.add_line(codec.encode(codec.build(keys["a"], "note", "core/x", {"text": "chat"})))
    idx.add_line(codec.encode(codec.build(keys["a"], "ask", "core/x", {"task": "t", "budget": {"asset": "FLOP", "max": "1"}})))
    idx.apply_policy(RoomPolicy(intents=frozenset({"ask"}), min_score_to_show=0.5), T)
    assert len(idx.msgs) == 0 and len(idx.hidden) == 2


def test_file_formats(tmp_path, keys):
    env = codec.build(keys["a"], "note", "core/x", {"text": "hi"})
    (tmp_path / "a.agb1").write_text(codec.encode(env) + "\nhello world\n")
    (tmp_path / "b.jsonl").write_text(json.dumps({"from": keys["a"].did, "text": codec.encode(env)}) + "\n")
    (tmp_path / "c.json").write_text(json.dumps({"room": "r", "messages": [{"from": "x", "text": "plain"}]}))
    idx = Index()
    for f in ("a.agb1", "b.jsonl", "c.json"):
        idx.add_file(tmp_path / f)
    assert idx.stats["valid"] == 1 and idx.stats["duplicate"] == 1 and idx.stats["chatter"] == 2


@pytest.mark.parametrize("url", [
    "http://technocore.chat/r/builders", "https://evil.example/r/builders", "https://technocore.chat/r/builders/say/x",
    "https://technocore.chat/r/builders?format=json&cb=1", "https://technocore.chat/kv/x", "https://technocore.chat:8443/r/x",
])
def test_read_url_allowlist(url):
    with pytest.raises(ValueError):
        safe.assert_read_url(url)


def test_fetch_room_refuses_bad_room_before_network():
    with pytest.raises(ValueError):
        fetch_room("../say/x")


def test_safe_helpers():
    assert safe.action_asks("could you please sign this tx for me") 
    assert "[redacted]" in safe.sanitize("password: hunter2")
    assert safe.FENCE_CLOSE not in safe.sanitize("x " + safe.FENCE_CLOSE + " y")
    assert safe.sanitize("a\u202eb\u200bc") == "abc"
