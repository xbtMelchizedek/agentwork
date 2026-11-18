"""Issue 11 (part): attestations from DIDs that traded with either party are discounted."""
from agentwork import codec, reputation

from test_reputation import DAY, NOW, R, World, score


def test_attester_who_traded_with_payee_is_ignored(keys):
    a, b, c = keys["a"], keys["b"], keys["c"]
    w = World()
    w.job(b, c)                      # edge: B paid C (C claimed), no attestation needed
    w.job(a, b, attesters=[c])       # C attests A->B
    assert score(w.scores(), b) == 0
    assert score(w.scores(attester_conflict_window_ms=None), b) > 0  # old behaviour when disabled


def test_attester_who_traded_with_payer_is_ignored(keys):
    a, b, c = keys["a"], keys["b"], keys["c"]
    w = World()
    w.job(c, a)                      # edge: C paid A
    w.job(a, b, attesters=[c])
    assert score(w.scores(), b) == 0


def test_edge_outside_window_does_not_conflict(keys):
    a, b, c = keys["a"], keys["b"], keys["c"]
    w = World()
    w.job(b, c, ts=NOW - 150 * DAY)  # > 90 days before the attested receipt
    w.job(a, b, attesters=[c])
    assert score(w.scores(), b) > 0


def test_payer_cannot_conflict_attester_unilaterally(keys):
    """A receipt naming C as payee without C's own claim is not a trade edge."""
    a, b, c = keys["a"], keys["b"], keys["c"]
    w = World()
    w.job(a, c, link_claim=False)    # payer-only receipt pointing at A's ask
    w.job(a, b, attesters=[c])
    assert score(w.scores(), b) > 0


def test_non_settled_edge_does_not_conflict(keys):
    a, b, c = keys["a"], keys["b"], keys["c"]
    w = World()
    w.job(b, c, outcome="refunded")
    w.job(a, b, attesters=[c])
    assert score(w.scores(), b) > 0


def test_superseded_edge_does_not_conflict(keys):
    a, b, c = keys["a"], keys["b"], keys["c"]
    w = World()
    rc = w.job(b, c)
    w.msgs.append(codec.build(b, "receipt", R, {**rc["b"], "outcome": "refunded", "supersedes": rc["cid"]},
                              ref=rc["ref"], ts=w.tick()))
    w.job(a, b, attesters=[c])
    assert score(w.scores(), b) > 0


def test_conflicted_invalid_cannot_cancel_and_weight_param(keys):
    a, b, c, d = keys["a"], keys["b"], keys["c"], keys["d"]
    w = World()
    w.job(b, c)
    w.job(a, b, attesters=[d, c], verdicts=["valid", "invalid"])
    assert score(w.scores(), b) > 0                               # conflicted C's "invalid" weighs 0
    assert score(w.scores(attester_conflict_weight=1.0), b) == 0  # full weight: tie cancels


def test_five_did_ring_scores_below_honest_workers(keys):
    """Ring r_i pays r_{i+1}, attested by r_{i+2} (in-ring); honest: outside payers + outside attester."""
    ring = [keys[k] for k in "abcde"]
    honest = [keys[k] for k in "fghij"]
    payers = [keys[k] for k in "klmno"]
    attester = keys["z"]
    w = World()
    for i in range(5):
        w.job(ring[i], ring[(i + 1) % 5], attesters=[ring[(i + 2) % 5]], rail_ref=f"ring-{i}")
    for i in range(5):
        w.job(payers[i], honest[i], attesters=[attester], rail_ref=f"honest-{i}")
    reps = w.scores()
    ring_total = sum(score(reps, k) for k in ring)
    honest_total = sum(score(reps, k) for k in honest)
    assert ring_total == 0 and honest_total > 0
    old = w.scores(attester_conflict_window_ms=None)
    assert sum(score(old, k) for k in ring) > 0  # without the check the ring would have scored


def test_pure_and_deterministic(keys):
    a, b, c, d = keys["a"], keys["b"], keys["c"], keys["d"]
    w = World()
    w.job(b, c)
    w.job(a, b, attesters=[c, d])
    r1 = {k: v.to_json() for k, v in w.scores().items()}
    r2 = {k: v.to_json() for k, v in reputation.compute(list(reversed(w.msgs)), NOW).items()}
    assert r1 == r2 and r1[b.did]["score"] > 0  # D is independent, C is not
