"""reputation.find_rings: report-only detection of settled-receipt rings among DIDs (issue 11)."""
import json
import subprocess
import sys

import pytest

from agentwork import codec, reputation
from conftest import ROOT
from test_reputation import DAY, NOW, World, _correct


def _ring(w, ks, **kw):
    for a, b in zip(ks, ks[1:] + ks[:1]):
        w.job(a, b, **kw)


def _dids(ks):
    return sorted(k.did for k in ks)


def test_five_did_ring_found_once_rotated_to_smallest(keys):
    ks = [keys[c] for c in "abcde"]
    w = World(); _ring(w, ks)
    rings = reputation.find_rings(w.msgs, NOW)
    assert len(rings) == 1
    r = rings[0]
    assert r["len"] == 5 and r["receipts"] == 5 and sorted(r["dids"]) == _dids(ks)
    assert r["dids"][0] == min(r["dids"])


def test_reciprocal_pair_is_len_2_and_can_be_excluded(keys):
    w = World(); w.job(keys["a"], keys["b"]); w.job(keys["b"], keys["a"]); w.job(keys["b"], keys["a"], rail_ref="tx-2")
    rings = reputation.find_rings(w.msgs, NOW)
    assert [(r["len"], r["receipts"]) for r in rings] == [(2, 3)]
    assert reputation.find_rings(w.msgs, NOW, min_len=3) == []


def test_honest_star_and_chain_have_no_rings(keys):
    w = World()
    for c in "bcdef":
        w.job(keys["a"], keys[c], attesters=[keys["z"]])   # one payer, many workers
    w.job(keys["b"], keys["c"]); w.job(keys["c"], keys["d"])  # open chain, no way back
    assert reputation.find_rings(w.msgs, NOW) == []


def test_max_len_bounds_cycle_length(keys):
    ks = [keys[c] for c in "abcdef"]
    w = World(); _ring(w, ks)
    assert reputation.find_rings(w.msgs, NOW) == []          # default max_len=5
    assert len(reputation.find_rings(w.msgs, NOW, max_len=6)) == 1


def test_unsettled_unlinked_and_old_receipts_do_not_close_a_ring(keys):
    a, b, c = keys["a"], keys["b"], keys["c"]
    for closing in ({"outcome": "refunded"}, {"link_claim": False}, {"ts": NOW - 400 * DAY}):
        w = World(); w.job(a, b); w.job(b, c); w.job(c, a, **closing)
        assert reputation.find_rings(w.msgs, NOW) == [], closing
    w = World(); w.job(a, b); w.job(b, c); w.job(c, a, link_claim=False)
    assert len(reputation.find_rings(w.msgs, NOW, reputation.Params(require_claim_link=False))) == 1


def test_refund_correction_breaks_ring(keys):
    a, b, c = keys["a"], keys["b"], keys["c"]
    w = World(); w.job(a, b); w.job(b, c); rc = w.job(c, a)
    assert len(reputation.find_rings(w.msgs, NOW)) == 1
    _correct(w, c, a, rc, "refunded")
    assert reputation.find_rings(w.msgs, NOW) == []


def test_pure_deterministic_and_does_not_change_scores(keys):
    ks = [keys[c] for c in "abcd"]
    w = World(); _ring(w, ks, attesters=[keys["z"]])
    before = {d: r.to_json() for d, r in w.scores().items()}
    r1 = reputation.find_rings(list(w.msgs), NOW)
    r2 = reputation.find_rings(list(reversed(w.msgs)), NOW)
    assert r1 == r2 and len(r1) == 1
    assert {d: r.to_json() for d, r in w.scores().items()} == before


def test_budget_caps_work_and_bad_lengths_raise(keys):
    ks = [keys[c] for c in "abc"]
    w = World(); _ring(w, ks)
    assert reputation.find_rings(w.msgs, NOW, budget=0) == []
    with pytest.raises(ValueError):
        reputation.find_rings(w.msgs, NOW, min_len=1)
    with pytest.raises(ValueError):
        reputation.find_rings(w.msgs, NOW, min_len=4, max_len=3)


def test_cli_rep_rings(keys, tmp_path):
    ks = [keys[c] for c in "abc"]
    w = World(); _ring(w, ks); w.job(keys["d"], keys["e"])
    src = tmp_path / "log.agb1"
    src.write_text("\n".join(codec.encode(m) for m in w.msgs) + "\n")
    base = [sys.executable, "-m", "agentwork", "rep", "--src", str(src), "--now", str(NOW), "--rings"]
    r = subprocess.run(base, cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rings = json.loads(r.stdout)
    assert [sorted(x["dids"]) for x in rings] == [_dids(ks)]
    r = subprocess.run(base + ["--did", keys["d"].did], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout) == []
