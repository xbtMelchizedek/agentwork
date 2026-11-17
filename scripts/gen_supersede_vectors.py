#!/usr/bin/env python3
"""Regenerate vectors/supersede-v1.json: receipt-correction scenarios for interop (SPEC §3).

Built from PUBLIC deterministic test keys, fixed timestamps and fixed nonces, so the
output is byte-stable. Each scenario lists agb1 wire lines, an evaluation time, and
the expected chain tip, job status and whether the worker's receipt counts toward
reputation. Another implementation passes if it gets the same three answers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agentwork import codec, reputation  # noqa: E402
from agentwork.indexer import Index  # noqa: E402
from agentwork.testkeys import test_key  # noqa: E402

TS = 1795000000000
R = "core/jobs"
F = 10**18
payer, worker, checker = test_key("alice"), test_key("bob"), test_key("carol")
OUT = ROOT / "vectors" / "supersede-v1.json"


class Seq:
    def __init__(self):
        self.i = 0

    def n(self):
        self.i += 1
        return f"{self.i:016x}"


def scenario(name, note, steps, eval_dt=100):
    s = Seq()
    m = []
    ask = codec.build(payer, "ask", R, {"task": "t", "budget": {"asset": "FLOP", "max": str(F)}}, ts=TS, n=s.n())
    off = codec.build(worker, "offer", R, {"price": {"asset": "FLOP", "amt": str(F)}}, ref=ask["cid"], ts=TS + 1, n=s.n())
    acc = codec.build(payer, "accept", R, {"payee": worker.did}, ref=off["cid"], ts=TS + 2, n=s.n())
    clm = codec.build(worker, "claim", R, {"deliverable": "d"}, ref=acc["cid"], ts=TS + 3, n=s.n())
    m += [ask, off, acc, clm]
    receipts = []
    for k, (outcome, sup, attest, dt) in enumerate(steps):
        b = {"outcome": outcome, "payee": worker.did, "amount": {"asset": "FLOP", "amt": str(F)},
             "rail": "flop-htlc", "railRef": "tx-1"}
        if sup is not None:
            b["supersedes"] = receipts[sup]["cid"]
        rc = codec.build(payer, "receipt", R, b, ref=clm["cid"], ts=TS + 10 + dt, n=s.n())
        receipts.append(rc); m.append(rc)
        if attest:
            m.append(codec.build(checker, "attest", R, {"verdict": "valid", "method": "rerun"},
                                 ref=rc["cid"], ts=rc["ts"] + 1, n=s.n()))
    now = TS + eval_dt
    idx = Index()
    for e in m:
        idx.add_line(codec.encode(e))
    job = idx.jobs(now)[0]
    reps = reputation.compute(m, now, reputation.Params(window_ms=10**12))
    return {"name": name, "note": note, "eval_ts": now, "lines": [codec.encode(e) for e in m],
            "receipts": [r["cid"] for r in receipts],
            "expect": {"tip": job["tip"]["cid"] if job["tip"] else None, "status": job["status"],
                       "worker_counted": reps.get(worker.did, reputation.Rep("")).receipts == 1}}


# steps: (outcome, supersedes index or None, add independent valid attest, ts offset)
SCENARIOS = [
    scenario("settled", "one settled receipt, independently attested", [("settled", None, True, 0)]),
    scenario("settled-then-refunded", "refund names the settled receipt: voids it", [("settled", None, True, 0), ("refunded", 0, False, 5)]),
    scenario("dispute-then-settle", "dispute suspends, a new settled receipt with a fresh attest restores",
             [("settled", None, True, 0), ("disputed", 0, False, 5), ("settled", 1, True, 10)]),
    scenario("unlinked-correction", "refund without `supersedes` is ignored", [("settled", None, True, 0), ("refunded", None, False, 5)]),
    scenario("stale-correction", "refund names the first receipt, not the tip (dispute): ignored",
             [("settled", None, True, 0), ("disputed", 0, False, 5), ("refunded", 0, False, 10)]),
    scenario("future-correction", "refund dated after the evaluation time is not yet current",
             [("settled", None, True, 0), ("refunded", 0, False, 1000)]),
    scenario("restore-needs-fresh-attest", "re-settle without its own attest is current but does not count",
             [("settled", None, True, 0), ("disputed", 0, False, 5), ("settled", 1, False, 10)]),
]


def render() -> str:
    doc = {"format": "agentwork.supersede-vectors/1", "spec": "SPEC.md §3 corrections",
           "keys": "agentwork.testkeys public test keys alice (payer), bob (worker), carol (checker)",
           "scenarios": SCENARIOS}
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUT.write_text(render())
    print(f"wrote {OUT.relative_to(ROOT)} ({len(SCENARIOS)} scenarios)")
