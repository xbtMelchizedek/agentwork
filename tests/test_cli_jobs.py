
"""agentwork jobs: the job board as JSON, filterable by status."""
import json
import subprocess
import sys

from conftest import ROOT

T = 1794900000000


def _jobs(*extra):
    subprocess.run([sys.executable, "examples/demo.py"], cwd=ROOT, check=True, capture_output=True)
    r = subprocess.run([sys.executable, "-m", "agentwork", "jobs", "--src", "examples/demo_room.json",
                        "--now", str(T + 3_600_000), *extra], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_jobs_lists_every_ask_with_status():
    rows = _jobs()
    assert sorted(r["status"] for r in rows) == ["open", "settled+attested"]
    paid = [r for r in rows if r["status"] == "settled+attested"][0]
    assert paid["receipt"] and paid["outcome"] == "settled" and paid["attests"] >= 1 and paid["payees"]
    for r in rows:
        assert r["cid"] and r["from"].startswith("did:key:")


def test_jobs_status_filter():
    rows = _jobs("--status", "open")
    assert [r["status"] for r in rows] == ["open"] and rows[0]["receipt"] is None


def test_jobs_rejects_unknown_status():
    r = subprocess.run([sys.executable, "-m", "agentwork", "jobs", "--status", "bogus"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 2 and "Traceback" not in r.stderr

