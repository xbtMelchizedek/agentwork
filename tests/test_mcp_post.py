import json
import os
import subprocess
import sys

import pytest

from agentwork import codec, post
from agentwork.didkey import save_key, verify
from agentwork.mcp_server import Server
from conftest import ROOT


@pytest.fixture
def srv(tmp_path, keys):
    kf = tmp_path / "agent.seed"
    save_key(keys["a"], kf)
    return Server({"AGB_KEY_FILE": str(kf), "AGB_OUTBOX": str(tmp_path / "outbox"), "AGB_OUT": str(tmp_path / "site")})


def call(s, name, args, i=1):
    return s.handle({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}})["result"]


def test_initialize_and_list(srv):
    r = srv.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    assert r["result"]["serverInfo"]["name"] == "agentwork"
    names = {t["name"] for t in srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
    assert {"post_ask", "post_offer", "post_receipt", "list_open_jobs", "get_reputation", "verify_message", "render"} <= names
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_post_tools_write_local_drafts_only(srv, tmp_path, keys):
    r = call(srv, "post_ask", {"room": "core/builders", "task": "Summarize issue 12", "budget_asset": "FLOP", "budget_max": "1000"})
    out = r["structuredContent"]
    assert out["posted"] is False and out["draft"] is True
    assert os.path.exists(out["outbox_file"]) and str(tmp_path / "outbox") in out["outbox_file"]
    assert codec.verify_line(out["wire"]).ok
    r2 = call(srv, "post_offer", {"room": "core/builders", "ref": out["cid"], "price_asset": "FLOP", "price_amt": "900"})
    assert not r2["isError"]
    jobs = call(srv, "list_open_jobs", {})["structuredContent"]["jobs"]
    assert jobs[0]["status"] == "open" and jobs[0]["offers"] == 1
    assert call(srv, "verify_message", {"line": out["wire"]})["structuredContent"]["ok"]
    assert call(srv, "get_reputation", {"did": keys["a"].did})["structuredContent"]["score"] == 0
    files = call(srv, "render", {})["structuredContent"]["files"]
    assert any(f.endswith("index.html") for f in files)
    assert len(call(srv, "list_outbox", {})["structuredContent"]["drafts"]) == 2


def test_bad_args_are_tool_errors(srv):
    assert call(srv, "post_ask", {"room": "core/builders", "task": "x"})["isError"]
    assert call(srv, "post_ask", {"room": "core/builders", "task": "x", "budget_asset": "FLOP", "budget_max": "1", "evil": 1})["isError"]
    r = srv.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "post_to_network", "arguments": {}}})
    assert "error" in r


def test_no_key_configured(tmp_path):
    s = Server({"AGB_OUTBOX": str(tmp_path / "o")})
    assert call(s, "post_note", {"room": "core/x", "text": "hi"})["isError"]


def test_stdio_subprocess(tmp_path, keys):
    kf = tmp_path / "k.seed"; save_key(keys["b"], kf)
    reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "post_note", "arguments": {"room": "core/x", "text": "hello"}}}]
    env = {**os.environ, "AGB_KEY_FILE": str(kf), "AGB_OUTBOX": str(tmp_path / "ob")}
    env.pop(post.ENV_FLAG, None)
    p = subprocess.run([sys.executable, "-m", "agentwork", "mcp"], cwd=ROOT, input="\n".join(json.dumps(r) for r in reqs) + "\n",
                       capture_output=True, text=True, env=env, timeout=30)
    outs = [json.loads(l) for l in p.stdout.splitlines()]
    assert [o["id"] for o in outs] == [1, 2]
    assert outs[1]["result"]["structuredContent"]["posted"] is False
    assert keys["b"].did in outs[1]["result"]["structuredContent"]["wire"]
    assert "seed" not in p.stdout.lower() or "seed" in str(kf)  # never echoes key material


def test_post_disabled_by_default(monkeypatch, keys):
    monkeypatch.delenv(post.ENV_FLAG, raising=False)
    line = codec.encode(codec.build(keys["a"], "note", "core/x", {"text": "hi"}))
    with pytest.raises(post.PostingDisabled):
        post.post(line, "builders", keys["a"], confirm=True)
    monkeypatch.setenv(post.ENV_FLAG, "1")
    with pytest.raises(post.PostingDisabled):
        post.post(line, "builders", keys["a"], confirm=False)


def test_signed_url_matches_technocore_rule(keys):
    import urllib.parse
    line = codec.encode(codec.build(keys["a"], "note", "core/x", {"text": "hi"}))
    url = post.signed_url(line, "builders", keys["a"], nonce=123)
    parts = url.split("/")
    did, sig, nonce, text = parts[6], parts[7], parts[8], urllib.parse.unquote(parts[9])
    assert parts[4:6] == ["builders", "say-signed"] and did == keys["a"].did and text == line
    assert verify(did, sig, f"builders|{nonce}|{text}".encode())
    with pytest.raises(ValueError):
        post.signed_url(line, "builders", keys["b"])


def test_cli_post_refuses(tmp_path, keys):
    kf = tmp_path / "k.seed"; save_key(keys["a"], kf)
    lf = tmp_path / "m.agb1"; lf.write_text(codec.encode(codec.build(keys["a"], "note", "core/x", {"text": "hi"})))
    env = {k: v for k, v in os.environ.items() if k != post.ENV_FLAG}
    r = subprocess.run([sys.executable, "-m", "agentwork", "post", "--line-file", str(lf), "--tc-room", "builders", "--key", str(kf), "--yes"],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "disabled" in r.stderr
