"""Issue 11 follow-up: Rep JSON shows how many counted receipts hit the ring / attester-conflict rules."""
from agentwork import reputation

from test_reputation import World


def _j(reps, k):
    return reputation.get(reps, k.did)


def test_ring_hits_reported_per_payee(keys):
    a, b, c, z = keys["a"], keys["b"], keys["c"], keys["z"]
    w = World()
    for i, (p, q) in enumerate([(a, b), (b, c), (c, a)]):
        w.job(p, q, attesters=[z], rail_ref=f"r-{i}")
    for k in (a, b, c):
        j = _j(w.scores(), k)
        assert j["receipts_counted"] == 1 and j["receipts_ring_discounted"] == 1
        assert _j(w.scores(ring_window_ms=None), k)["receipts_ring_discounted"] == 0


def test_conflicted_attester_reported_when_receipt_still_counts(keys):
    a, b, c, z = keys["a"], keys["b"], keys["c"], keys["z"]
    w = World()
    w.job(b, c)                                  # C traded with payee B
    w.job(a, b, attesters=[c, z])                # C conflicted, Z independent: still counts
    j = _j(w.scores(), b)
    assert j["receipts_counted"] == 1 and j["receipts_attester_conflicted"] == 1
    assert _j(w.scores(attester_conflict_window_ms=None), b)["receipts_attester_conflicted"] == 0


def test_dropped_receipt_not_counted_as_conflicted(keys):
    a, b, c = keys["a"], keys["b"], keys["c"]
    w = World()
    w.job(b, c)
    w.job(a, b, attesters=[c])                   # only attester conflicted -> receipt not counted
    j = _j(w.scores(), b)
    assert j["receipts_attester_conflicted"] == 0 and j["receipts_counted"] == 0


def test_clean_trade_has_zero_counters_and_stable_keys(keys):
    a, b, z = keys["a"], keys["b"], keys["z"]
    w = World()
    w.job(a, b, attesters=[z])
    j = _j(w.scores(), b)
    assert j["receipts_ring_discounted"] == 0 and j["receipts_attester_conflicted"] == 0
    assert j["transferable"] is False
    assert _j(w.scores(), keys["d"])["receipts_ring_discounted"] == 0   # unknown DID
