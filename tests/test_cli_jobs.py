
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



def test_jobs_did_filter():
    rows = _jobs()
    poster = rows[0]["from"]
    mine = _jobs("--did", poster)
    assert mine and all(r["from"] == poster or poster in r["payees"] for r in mine)
    payee = [r for r in rows if r["payees"]][0]["payees"][0]
    paid = _jobs("--did", payee)
    assert paid and all(r["from"] == payee or payee in r["payees"] for r in paid)
    assert _jobs("--did", "did:key:z6MkNobody") == []


def test_jobs_did_rejects_non_did():
    r = subprocess.run([sys.executable, "-m", "agentwork", "jobs", "--src", "examples/demo_room.json", "--did", "alice"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 2 and "Traceback" not in r.stderr and "--did" in r.stderr


def test_jobs_md_table_escapes_board_text():
    r = subprocess.run([sys.executable, '-m', 'agentwork', 'jobs', '--src', 'examples/demo_room.json',
                        '--now', str(T + 3_600_000), '--md'], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert lines[0].startswith('| status | task |') and len(lines) == 2 + len(_jobs())


def test_md_cell_neutralises_markup():
    from agentwork.cli import _md_cell, jobs_markdown
    bt, bs = chr(96), chr(92)
    got = _md_cell('a|b [x](y) <s> ' + bt + 'c\nz')
    assert got == 'a' + bs + '|b ' + bs + '[x' + bs + '](y) ' + bs + '<s' + bs + '> ' + bs + bt + 'c z'
    assert _md_cell('x' * 100, 10) == 'x' * 10 + '...'
    assert jobs_markdown([]) == '_no jobs match_'



def test_jobs_count_summary():
    c = _jobs("--count")
    assert c == {"total": 2, "by_status": {"open": 1, "settled+attested": 1}}


def test_jobs_count_respects_filters():
    assert _jobs("--status", "open", "--count") == {"total": 1, "by_status": {"open": 1}}
