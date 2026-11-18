"""ERC-8004 export: a file whose evidence cids reproduce the score exactly (issue 05)."""
import json
import subprocess
import sys

from agentwork import codec, erc8004, reputation
from agentwork.didkey import SigningKey
from tests.test_reputation import NOW, R, World, score


def noisy_world(keys):
    """b earns from 4 payers; one receipt is later refunded; b is flagged by a reputable r;
    unrelated DIDs trade too. The export must pick exactly what b's score depends on."""
    w = World()
    payers = [SigningKey.generate() for _ in range(4)]
    rcs = [w.job(p, keys["b"], amt=(i + 1) * 10**18, attesters=[keys["c"]], rail_ref=f"x{i}")
           for i, p in enumerate(payers)]
    w.job(payers[0], keys["b"], attesters=[keys["c"]], rail_ref="x0b")          # pair decay
    r0 = rcs[1]
    w.msgs.append(codec.build(payers[1], "receipt", R, {**r0["b"], "outcome": "refunded", "supersedes": r0["cid"]},
                              ref=r0["ref"], ts=w.tick()))
    for i in range(3):
        w.job(SigningKey.generate(), keys["e"], attesters=[keys["d"]], rail_ref=f"e{i}")
    w.job(keys["a"], keys["r"], attesters=[keys["c"]], rail_ref="rr")          # r reputable
    note = codec.build(keys["b"], "note", R, {"text": "hi"}, ts=w.tick())
    w.msgs += [note, codec.build(keys["r"], "flag", R, {"reason": "scam"}, ref=note["cid"], ts=w.tick())]
    return w


def test_export_shape_and_value(keys):
    w = noisy_world(keys)
    doc = erc8004.export(w.msgs, keys["b"].did, NOW)
    s = score(reputation.compute(w.msgs, NOW), keys["b"])
    assert s > 0 and doc["value"] == round(s * 10**6) and doc["valueDecimals"] == 6
    assert (doc["tag1"], doc["tag2"]) == ("agb1Rep", "v0") and doc["createdAt"].endswith("Z")
    assert doc["agentId"] is None and doc["agentwork"]["onchain"] is False
    assert len(doc["agentwork"]["receipts"]) == 4      # refunded one is not counted
    assert doc["agentwork"]["summary"]["reputable_flags"] == 1
    json.dumps(doc)                                     # plain JSON


def test_recompute_from_evidence_is_exact(keys):
    w = noisy_world(keys)
    doc = erc8004.export(w.msgs, keys["b"].did, NOW)
    assert erc8004.recompute(doc, w.msgs) == score(reputation.compute(w.msgs, NOW), keys["b"])
    assert erc8004.verify(doc, w.msgs)
    ev = set(doc["agentwork"]["evidence"])
    assert len(ev) < len(w.msgs)                        # e's trades are not evidence
    assert erc8004.verify(doc, [m for m in w.msgs if m["cid"] in ev])


def test_missing_evidence_or_tamper_fails_closed(keys):
    w = noisy_world(keys)
    doc = erc8004.export(w.msgs, keys["b"].did, NOW)
    refund = next(m for m in w.msgs if m["t"] == "receipt" and m["b"]["outcome"] == "refunded")
    assert refund["cid"] in doc["agentwork"]["evidence"]   # whole supersedes group is carried
    assert not erc8004.verify(doc, [m for m in w.msgs if m["cid"] != refund["cid"]])
    bad = json.loads(json.dumps(doc)); bad["value"] += 1
    assert not erc8004.verify(bad, w.msgs)


def test_min_attester_score_params_roundtrip(keys):
    w = World()
    w.job(keys["a"], keys["c"], attesters=[keys["d"]], rail_ref="c1")          # c gets score
    w.job(keys["a"], keys["b"], attesters=[keys["c"]], rail_ref="b1")
    w.job(keys["f"], keys["b"], attesters=[keys["g"]], rail_ref="b2")          # g has no score
    p = reputation.Params(min_attester_score=1e-9, verified_settlements=frozenset({("flop-htlc", "b1"), ("flop-htlc", "c1")}))
    doc = erc8004.export(w.msgs, keys["b"].did, NOW, p)
    assert doc["value"] > 0 and len(doc["agentwork"]["receipts"]) == 1
    assert erc8004.params_from_json(json.loads(json.dumps(doc))["agentwork"]["params"]) == p
    ev = set(doc["agentwork"]["evidence"])
    assert erc8004.verify(doc, [m for m in w.msgs if m["cid"] in ev])


def test_unknown_did_exports_zero(keys):
    doc = erc8004.export([], keys["z"].did, NOW)
    assert doc["value"] == 0 and doc["agentwork"]["evidence"] == [] and erc8004.verify(doc, [])


def test_cli_format_erc8004(keys, tmp_path):
    w = noisy_world(keys)
    src = tmp_path / "log.agb1"
    src.write_text("\n".join(codec.encode(m) for m in w.msgs) + "\n")
    out = subprocess.run([sys.executable, "-m", "agentwork.cli", "rep", "--src", str(src), "--now", str(NOW),
                          "--did", keys["b"].did, "--format", "erc8004"], capture_output=True, text=True, check=True)
    doc = json.loads(out.stdout)
    assert erc8004.verify(doc, w.msgs)
