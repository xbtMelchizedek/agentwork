
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


def test_jobs_task_filter_is_plain_case_insensitive_text():
    rows = _jobs()
    word = next(w for r in rows for w in str(r["task"] or "").split() if len(w) > 3)
    hit = _jobs("--task", word.upper())
    assert hit and all(word.casefold() in str(r["task"]).casefold() for r in hit)
    assert _jobs("--task", ".*") == []
    assert _jobs("--task", word, "--count")["total"] == len(hit)


def test_jobs_task_rejects_blank():
    r = subprocess.run([sys.executable, "-m", "agentwork", "jobs", "--src", "examples/demo_room.json", "--task", "  "],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 2 and "Traceback" not in r.stderr and "--task" in r.stderr



def test_jobs_newest_keeps_newest_after_filters():
    rows = _jobs()
    newest = max(rows, key=lambda r: r["ts"])
    one = _jobs("--newest", "1")
    assert [r["cid"] for r in one] == [newest["cid"]]
    assert len(_jobs("--newest", "50")) == len(rows)
    assert _jobs("--status", "open", "--newest", "1", "--count") == {"total": 1, "by_status": {"open": 1}}


def test_jobs_newest_rejects_non_positive():
    for bad in ("0", "-3", "x"):
        r = subprocess.run([sys.executable, "-m", "agentwork", "jobs", "--src", "examples/demo_room.json", "--newest", bad],
                           cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 2 and "Traceback" not in r.stderr and "--newest" in r.stderr


def test_jobs_room_filter_is_exact():
    rows = _jobs()
    room = rows[0]["room"]
    assert len(_jobs("--room", room)) == len([r for r in rows if r["room"] == room])
    assert len(_jobs("--room", "/" + room + "/")) == len(_jobs("--room", room))
    assert _jobs("--room", room.split("/")[0]) == []
    assert _jobs("--room", "no/such-room") == []


def test_jobs_room_rejects_blank():
    r = subprocess.run([sys.executable, "-m", "agentwork", "jobs", "--src", "examples/demo_room.json", "--room", " / "],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 2 and "Traceback" not in r.stderr and "--room" in r.stderr


def test_jobs_asset_filter():
    rows = _jobs()
    flop = [r for r in rows if (r.get("budget") or {}).get("asset") == "FLOP"]
    assert flop and _jobs("--asset", "FLOP") == flop
    assert _jobs("--asset", " flop ") == flop
    assert _jobs("--asset", "USDC") == []


def test_jobs_asset_rejects_blank():
    r = subprocess.run([sys.executable, "-m", "agentwork", "jobs", "--src", "examples/demo_room.json", "--asset", "  "],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 2 and "Traceback" not in r.stderr and "--asset" in r.stderr


def test_jobs_max_offers_filter():
    rows = _jobs()
    for n in (0, 1, 2):
        assert _jobs("--max-offers", str(n)) == [r for r in rows if r["offers"] <= n]
    top = max(r["offers"] for r in rows)
    assert len(_jobs("--max-offers", str(top))) == len(rows)


def test_jobs_max_offers_rejects_negative():
    for bad in ("-1", "x"):
        r = subprocess.run([sys.executable, "-m", "agentwork", "jobs", "--src", "examples/demo_room.json", "--max-offers", bad],
                           cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 2 and "Traceback" not in r.stderr and "--max-offers" in r.stderr


def test_jobs_min_budget_filter():
    from decimal import Decimal
    rows = _jobs()
    priced = [r for r in rows if isinstance(r.get("budget"), dict) and r["budget"].get("max") is not None]
    assert priced
    lo = min(Decimal(str(r["budget"]["max"])) for r in priced)
    assert _jobs("--min-budget", "0") == priced
    assert _jobs("--min-budget", str(lo)) == [r for r in priced if Decimal(str(r["budget"]["max"])) >= lo]
    assert _jobs("--min-budget", "1e30") == []


def test_jobs_min_budget_rejects_bad():
    for bad in ("-1", "x", "  ", "nan", "inf"):
        r = subprocess.run([sys.executable, "-m", "agentwork", "jobs", "--src", "examples/demo_room.json", "--min-budget", bad],
                           cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 2 and "Traceback" not in r.stderr and "--min-budget" in r.stderr
