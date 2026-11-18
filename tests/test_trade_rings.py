"""Issue 11 (part): trade-graph rings (A->B->C->A) are discounted even with an independent attester."""
import pytest

from agentwork import codec, reputation

from test_reputation import DAY, NOW, R, World, score


def _ring(w, members, attester, tag="ring", ts=None):
    n = len(members)
    return [w.job(members[i], members[(i + 1) % n], attesters=[attester], rail_ref=f"{tag}-{i}", ts=ts)
            for i in range(n)]


def test_three_ring_with_independent_attester_is_discounted(keys):
    a, b, c, z = keys["a"], keys["b"], keys["c"], keys["z"]
    w = World()
    _ring(w, [a, b, c], z)
    on, off = w.scores(), w.scores(ring_window_ms=None)
    for k in (a, b, c):
        assert score(off, k) > 0
        assert score(on, k) == pytest.approx(0.25 * score(off, k))
    assert score(w.scores(ring_weight=0.0), a) == 0


def test_open_chain_is_not_a_ring(keys):
    a, b, c, d, z = keys["a"], keys["b"], keys["c"], keys["d"], keys["z"]
    w = World()
    w.job(a, b, attesters=[z]); w.job(b, c, attesters=[z]); w.job(c, d, attesters=[z])
    assert w.scores() and all(score(w.scores(), k) == score(w.scores(ring_window_ms=None), k) for k in (b, c, d))


def test_pair_back_and_forth_left_to_pair_rules(keys):
    a, b, z = keys["a"], keys["b"], keys["z"]
    w = World()
    w.job(a, b, attesters=[z]); w.job(b, a, attesters=[z])
    assert score(w.scores(), a) == score(w.scores(ring_window_ms=None), a) > 0
    assert score(w.scores(ring_min_len=2), a) < score(w.scores(), a)


def test_ring_longer_than_max_len_not_flagged(keys):
    members = [keys[k] for k in "abcdefg"]           # 7-DID ring, default max 6
    w = World()
    _ring(w, members, keys["z"])
    assert score(w.scores(), members[1]) == score(w.scores(ring_window_ms=None), members[1])
    assert score(w.scores(ring_max_len=7), members[1]) < score(w.scores(), members[1])


def test_closing_edge_outside_window_not_a_ring(keys):
    a, b, c, z = keys["a"], keys["b"], keys["c"], keys["z"]
    w = World()
    w.job(c, a, attesters=[z], ts=NOW - 150 * DAY)   # closes the cycle, but 140 days apart
    w.job(a, b, attesters=[z]); w.job(b, c, attesters=[z])
    p = dict(window_ms=400 * DAY)
    assert score(w.scores(**p), b) == score(w.scores(ring_window_ms=None, **p), b) > 0


def test_payer_only_receipt_cannot_close_a_ring(keys):
    """C 'pays' A without A's claim: not an edge, so honest A->B->C stays undiscounted."""
    a, b, c, z = keys["a"], keys["b"], keys["c"], keys["z"]
    w = World()
    w.job(a, b, attesters=[z]); w.job(b, c, attesters=[z])
    w.job(c, a, link_claim=False)
    assert score(w.scores(), b) == score(w.scores(ring_window_ms=None), b) > 0


def test_refunded_edge_cannot_close_a_ring(keys):
    a, b, c, z = keys["a"], keys["b"], keys["c"], keys["z"]
    w = World()
    w.job(a, b, attesters=[z]); w.job(b, c, attesters=[z])
    rc = w.job(c, a)
    w.msgs.append(codec.build(c, "receipt", R, {**rc["b"], "outcome": "refunded", "supersedes": rc["cid"]},
                              ref=rc["ref"], ts=w.tick()))
    assert score(w.scores(), b) == score(w.scores(ring_window_ms=None), b) > 0


def test_five_did_trade_ring_independent_attester_scores_below_honest(keys):
    """Acceptance fixture, trade-graph case: same totals, outside attester for both groups."""
    ring = [keys[k] for k in "abcde"]
    honest = [keys[k] for k in "fghij"]
    payers = [keys[k] for k in "klmno"]
    z = keys["z"]
    w = World()
    _ring(w, ring, z)
    for i in range(5):
        w.job(payers[i], honest[i], attesters=[z], rail_ref=f"honest-{i}")
    reps, old = w.scores(), w.scores(ring_window_ms=None)
    assert sum(score(old, k) for k in ring) == pytest.approx(sum(score(old, k) for k in honest), rel=1e-4)
    assert sum(score(reps, k) for k in ring) < sum(score(reps, k) for k in honest)


def test_ring_length_helper_shortest_and_deterministic(keys):
    a, b, c, d, z = keys["a"], keys["b"], keys["c"], keys["d"], keys["z"]
    w = World()
    rab = w.job(a, b, attesters=[z])
    w.job(b, c); w.job(c, a)                          # 3-cycle via C
    w.job(b, d); w.job(d, c)                          # 4-cycle via D, C
    idx = reputation._valid(w.msgs)
    cur = reputation.receipt_tips(idx, NOW)
    directed = reputation._directed_edges(idx, cur, reputation.Params())
    assert reputation.ring_length(directed, rab, 90 * DAY) == 3
    assert reputation.ring_length(directed, rab, 90 * DAY, min_len=4) == 4
    assert reputation.ring_length(directed, rab, 90 * DAY, max_len=2) is None
    r1 = {k: v.to_json() for k, v in w.scores().items()}
    r2 = {k: v.to_json() for k, v in reputation.compute(list(reversed(w.msgs)), NOW).items()}
    assert r1 == r2


def test_search_budget_bounds_work(keys):
    a, b, c, z = keys["a"], keys["b"], keys["c"], keys["z"]
    w = World()
    _ring(w, [a, b, c], z)
    assert score(w.scores(ring_search_budget=0), b) == score(w.scores(ring_window_ms=None), b)
