import math

from agentwork import codec, reputation
from agentwork.didkey import SigningKey

NOW = 1795000000000
DAY = reputation.DAY_MS
F = 10**18
R = "core/jobs"


class World:
    def __init__(self):
        self.msgs = []
        self.t = NOW - 10 * DAY

    def tick(self):
        self.t += 1000
        return self.t

    def job(self, payer, payee, amt=F, attesters=(), verdicts=None, outcome="settled", rail_ref="tx-1", asset="FLOP",
            link_claim=True, ts=None):
        ask = codec.build(payer, "ask", R, {"task": "t", "budget": {"asset": asset, "max": str(amt)}}, ts=ts or self.tick())
        claim = codec.build(payee, "claim", R, {"deliverable": "d"}, ref=ask["cid"], ts=ts or self.tick())
        ref = claim["cid"] if link_claim else ask["cid"]
        rc = codec.build(payer, "receipt", R, {"outcome": outcome, "payee": payee.did, "amount": {"asset": asset, "amt": str(amt)},
                                                "rail": "flop-htlc", "railRef": rail_ref}, ref=ref, ts=ts or self.tick())
        self.msgs += [ask, claim, rc]
        for i, a in enumerate(attesters):
            v = (verdicts or ["valid"] * len(attesters))[i]
            self.msgs.append(codec.build(a, "attest", R, {"verdict": v, "method": "rerun"}, ref=rc["cid"], ts=ts or self.tick()))
        return rc

    def scores(self, **p):
        return reputation.compute(self.msgs, NOW, reputation.Params(**p))


def score(reps, k):
    return reps.get(k.did, reputation.Rep(k.did)).score


def test_needs_attestation(keys):
    w = World(); w.job(keys["a"], keys["b"])
    assert score(w.scores(), keys["b"]) == 0


def test_attestation_from_party_does_not_count(keys):
    w = World(); w.job(keys["a"], keys["b"], attesters=[keys["a"], keys["b"]])
    assert score(w.scores(), keys["b"]) == 0


def test_independent_attestation_counts(keys):
    w = World(); w.job(keys["a"], keys["b"], attesters=[keys["c"]])
    s = score(w.scores(), keys["b"])
    assert s > 0 and score(w.scores(), keys["a"]) == 0  # payer earns nothing from paying


def test_invalid_attest_cancels(keys):
    w = World(); w.job(keys["a"], keys["b"], attesters=[keys["c"], keys["d"]], verdicts=["valid", "invalid"])
    assert score(w.scores(), keys["b"]) == 0


def test_unsettled_unknown_asset_self_and_no_ref(keys):
    w = World()
    w.job(keys["a"], keys["b"], attesters=[keys["c"]], outcome="refunded")
    w.job(keys["a"], keys["b"], attesters=[keys["c"]], asset="USDC")
    w.job(keys["a"], keys["a"], attesters=[keys["c"]])
    w.job(keys["a"], keys["b"], attesters=[keys["c"]], rail_ref=" ")
    w.job(keys["a"], keys["b"], attesters=[keys["c"]], link_claim=False)
    assert score(w.scores(), keys["b"]) == 0 and score(w.scores(), keys["a"]) == 0


def test_verified_settlements_filter(keys):
    w = World(); w.job(keys["a"], keys["b"], attesters=[keys["c"]], rail_ref="tx-9")
    assert score(w.scores(verified_settlements=frozenset()), keys["b"]) == 0
    assert score(w.scores(verified_settlements=frozenset({("flop-htlc", "tx-9")})), keys["b"]) > 0


def test_sqrt_value(keys):
    w1 = World(); w1.job(keys["a"], keys["b"], amt=F, attesters=[keys["c"]], ts=NOW - 1)
    w2 = World(); w2.job(keys["a"], keys["b"], amt=100 * F, attesters=[keys["c"]], ts=NOW - 1)
    assert math.isclose(score(w2.scores(), keys["b"]) / score(w1.scores(), keys["b"]), 10.0, rel_tol=1e-9)


def test_sybil_dids_without_receipts_are_zero():
    sybils = [SigningKey.generate() for _ in range(200)]
    msgs = [codec.build(s, "note", R, {"text": "gm"}) for s in sybils]
    msgs += [codec.build(s, "review", R, {"score": 5}, ref="0x" + "11" * 32) for s in sybils]
    reps = reputation.compute(msgs, NOW)
    assert all(r.score == 0 for r in reps.values())


def test_wash_pair_loses_to_honest_diverse_worker(keys):
    # wash: A and B trade 30 jobs back and forth, attested by their own sybil C
    w = World()
    for i in range(30):
        w.job(keys["a"], keys["b"], attesters=[keys["c"]], rail_ref=f"w{i}")
    wash = score(w.scores(), keys["b"])
    # honest: worker H does 10 jobs for 10 distinct payers, same per-job value
    h = World()
    payers = [SigningKey.generate() for _ in range(10)]
    for i, p in enumerate(payers):
        h.job(p, keys["h"], attesters=[keys["c"]], rail_ref=f"h{i}")
    honest = score(h.scores(), keys["h"])
    assert honest > 5 * wash
    # pair decay + 10% cap bound the wash score: <= 0.1 * sum(sqrt(1)/k) / 30
    assert wash <= 0.1 * sum(1 / k for k in range(1, 31)) / 30 + 1e-9


def test_trusted_attesters_excludes_sybil_attester(keys):
    w = World(); w.job(keys["a"], keys["b"], attesters=[keys["c"]])
    assert score(w.scores(trusted_attesters=frozenset({keys["d"].did})), keys["b"]) == 0
    assert score(w.scores(trusted_attesters=frozenset({keys["c"].did})), keys["b"]) > 0


def test_min_attester_score_two_pass(keys):
    w = World()
    w.job(keys["a"], keys["b"], attesters=[keys["c"]])    # b has a score after pass 1
    w.job(keys["d"], keys["e"], attesters=[keys["b"]])    # attested by b (reputable)
    w.job(keys["d"], keys["f"], attesters=[keys["g"]])    # attested by g (no score)
    reps = w.scores(min_attester_score=1e-9)
    assert score(reps, keys["e"]) > 0 and score(reps, keys["f"]) == 0 and score(reps, keys["b"]) == 0


def test_flags_only_from_reputable(keys):
    w = World()
    payers = [SigningKey.generate() for _ in range(3)]
    for i, p in enumerate(payers):
        rc = w.job(p, keys["b"], attesters=[keys["c"]], rail_ref=f"x{i}")
    base = score(w.scores(), keys["b"])
    note = codec.build(keys["b"], "note", R, {"text": "hi"})
    w.msgs.append(note)
    for _ in range(5):
        w.msgs.append(codec.build(SigningKey.generate(), "flag", R, {"reason": "scam"}, ref=note["cid"]))
    assert math.isclose(score(w.scores(), keys["b"]), base)
    w.job(keys["a"], keys["r"], attesters=[keys["c"]], rail_ref="rr")      # r becomes reputable
    w.msgs.append(codec.build(keys["r"], "flag", R, {"reason": "scam"}, ref=note["cid"]))
    reps = w.scores()
    assert math.isclose(score(reps, keys["b"]), base * 0.9) and reps[keys["b"].did].flags == 1


def test_window_and_decay(keys):
    w = World(); w.job(keys["a"], keys["b"], attesters=[keys["c"]], ts=NOW - 200 * DAY)
    assert score(w.scores(), keys["b"]) == 0
    w1 = World(); w1.job(keys["a"], keys["b"], attesters=[keys["c"]], ts=NOW - 90 * DAY)
    w2 = World(); w2.job(keys["a"], keys["b"], attesters=[keys["c"]], ts=NOW)
    assert math.isclose(score(w1.scores(), keys["b"]) * 2, score(w2.scores(), keys["b"]), rel_tol=1e-9)


def test_forged_messages_ignored(keys):
    w = World(); rc = w.job(keys["a"], keys["b"], attesters=[keys["c"]])
    for m in w.msgs:
        if m["t"] == "attest":
            m["sig"] = keys["p"].sign(b"x")
    assert score(w.scores(), keys["b"]) == 0


def test_pure_and_non_transferable(keys):
    w = World(); w.job(keys["a"], keys["b"], attesters=[keys["c"]])
    r1, r2 = w.scores(), w.scores()
    assert r1[keys["b"].did].to_json() == r2[keys["b"].did].to_json()
    assert r1[keys["b"].did].to_json()["transferable"] is False
    assert "transfer" not in codec.INTENTS


def _correct(w, payer, payee, rc, outcome, dt=1000, supersedes="tip"):
    b = {"outcome": outcome, "payee": payee.did, "amount": rc["b"]["amount"], "rail": "flop-htlc", "railRef": rc["b"]["railRef"]}
    if supersedes:
        b["supersedes"] = rc["cid"] if supersedes == "tip" else supersedes
    nr = codec.build(payer, "receipt", R, b,
                     ref=rc["ref"], ts=rc["ts"] + dt)
    w.msgs.append(nr)
    return nr


def test_later_refund_voids_settled(keys):
    w = World(); rc = w.job(keys["a"], keys["b"], attesters=[keys["c"]])
    assert score(w.scores(), keys["b"]) > 0
    _correct(w, keys["a"], keys["b"], rc, "refunded")
    assert score(w.scores(), keys["b"]) == 0
    assert rc in w.msgs  # original stays in the log


def test_later_dispute_suspends_and_settle_restores(keys):
    w = World(); rc = w.job(keys["a"], keys["b"], attesters=[keys["c"]])
    before = score(w.scores(), keys["b"])
    _correct(w, keys["a"], keys["b"], rc, "disputed")
    assert score(w.scores(), keys["b"]) == 0
    disp = w.msgs[-1]
    again = _correct(w, keys["a"], keys["b"], disp, "settled")
    w.msgs.append(codec.build(keys["c"], "attest", R, {"verdict": "valid", "method": "rerun"}, ref=again["cid"], ts=again["ts"] + 1))
    assert math.isclose(score(w.scores(), keys["b"]), before, rel_tol=1e-3)


def test_correction_from_non_payer_is_ignored(keys):
    w = World(); rc = w.job(keys["a"], keys["b"], attesters=[keys["c"]])
    before = score(w.scores(), keys["b"])
    _correct(w, keys["d"], keys["b"], rc, "refunded")
    assert score(w.scores(), keys["b"]) == before


def test_future_correction_not_applied_yet(keys):
    w = World(); rc = w.job(keys["a"], keys["b"], attesters=[keys["c"]])
    before = score(w.scores(), keys["b"])
    _correct(w, keys["a"], keys["b"], rc, "refunded", dt=NOW - rc["ts"] + DAY)
    assert score(w.scores(), keys["b"]) == before


def test_correction_without_supersedes_is_ignored(keys):
    w = World(); rc = w.job(keys["a"], keys["b"], attesters=[keys["c"]])
    before = score(w.scores(), keys["b"])
    _correct(w, keys["a"], keys["b"], rc, "refunded", supersedes=None)
    assert score(w.scores(), keys["b"]) == before


def test_correction_naming_stale_receipt_is_ignored(keys):
    w = World(); rc = w.job(keys["a"], keys["b"], attesters=[keys["c"]])
    disp = _correct(w, keys["a"], keys["b"], rc, "disputed")
    resettle = _correct(w, keys["a"], keys["b"], disp, "settled")
    w.msgs.append(codec.build(keys["c"], "attest", R, {"verdict": "valid", "method": "rerun"}, ref=resettle["cid"], ts=resettle["ts"] + 1))
    before = score(w.scores(), keys["b"])
    _correct(w, keys["a"], keys["b"], rc, "refunded", dt=5000)  # names the first receipt, not the tip
    assert score(w.scores(), keys["b"]) == before > 0
