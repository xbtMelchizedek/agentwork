"""agentwork index --json: the feed.json view on stdout, no files written."""
import json
import subprocess
import sys

from agentwork import render
from agentwork.indexer import Index, RoomPolicy
from conftest import ROOT

T = 1794900000000
NOW = str(T + 3_600_000)


def _demo():
    subprocess.run([sys.executable, "examples/demo.py"], cwd=ROOT, check=True, capture_output=True)


def test_index_json_matches_feed_json_and_writes_nothing(tmp_path):
    _demo()
    out = tmp_path / "site"
    r = subprocess.run([sys.executable, "-m", "agentwork", "index", "--src", "examples/demo_room.json",
                        "--now", NOW, "--json", "--out", str(out)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    idx = Index(); idx.add_file(ROOT / "examples" / "demo_room.json")
    reps = idx.apply_policy(RoomPolicy(), int(NOW))
    assert got == json.loads(json.dumps(render.feed_json(idx, int(NOW), reps, "UTC"), ensure_ascii=False))
    assert got["format"] == "agentwork.feed/1" and got["generated_ms"] == int(NOW)
    assert sorted(j["status"] for j in got["jobs"]) == ["open", "settled+attested"]
    assert not out.exists()


def test_index_without_json_still_writes_site(tmp_path):
    _demo()
    out = tmp_path / "site"
    r = subprocess.run([sys.executable, "-m", "agentwork", "index", "--src", "examples/demo_room.json",
                        "--now", NOW, "--out", str(out)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (out / "feed.json").exists() and (out / "board.md").exists()
