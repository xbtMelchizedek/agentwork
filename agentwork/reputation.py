"""Reputation v0: a pure function from signed agb1 messages to a per-DID work score.

Only *weight-bearing* evidence counts:

1. a `receipt` with outcome `settled`, a settlement reference (`rail` + `railRef`),
   a known asset, payer != payee, and (by default) `ref` pointing at the payee's `claim`;
2. at least one independent `attest` (verdict `valid`) on that receipt from a DID that
   is neither payer nor payee, and no more `invalid` than `valid` attestations;
3. it is the payer's *latest* receipt for that claim (see "Corrections" below);
4. optionally, the (rail, railRef) pair is present in a caller-supplied set of
   settlements the caller verified on the rail itself (recommended for production).

Score for the payee (worker) of each counted receipt:

    base_r   = sqrt(amount in whole units) * pair_decay_r * age_decay_r
    c_j      = sum of base_r over receipts paid by counterparty j
    capped   = sum_j min(c_j, cap_frac * sum_j c_j)       # no counterparty > cap_frac
    score    = capped * diversity * (1 - flag_penalty)
    diversity = distinct counterparties / counted receipts

pair_decay: the k-th counted receipt between the same payer and payee inside
`pair_window_ms` counts 1/k. age_decay halves every `half_life_ms`; receipts older
than `window_ms` are ignored. Scores are keyed by DID and are not transferable: no
message type moves score between DIDs, and reviews/notes/posts add nothing.

Corrections: the log is append-only, so nothing is edited or deleted. A payer corrects a
receipt by posting a newer receipt with the same `ref` and `payee` and `supersedes` set to
the cid it replaces (an explicit transition; see SPEC §3). Only the chain tip is current: a later `refunded` or `cancelled` voids an earlier `settled`,
a later `disputed` suspends it, and a later `settled` restores it. A later `invalid`
attestation majority drops a receipt the same way. Both the original and the correction
stay in the log, so the history shows what was claimed and what replaced it. A payer can
only ever move one counterparty's share, which `cap_frac` already bounds.

Everything is recomputable by anyone from the same log; parameters are explicit.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from . import codec

DAY_MS = 86_400_000


@dataclass(frozen=True)
class Params:
    decimals: dict = field(default_factory=lambda: {"FLOP": 18})
    window_ms: int = 180 * DAY_MS
    half_life_ms: int = 90 * DAY_MS
    pair_window_ms: int = 90 * DAY_MS
    cap_frac: float = 0.10
    require_claim_link: bool = True
    trusted_attesters: frozenset | None = None   # if set, only these DIDs' attests count
    min_attester_score: float = 0.0              # if > 0, attester needs this pass-1 score
    verified_settlements: frozenset | None = None  # set of (rail, railRef) seen on-rail
    flag_step: float = 0.10
    flag_max: float = 0.50


@dataclass
class Rep:
    did: str
    score: float = 0.0
    receipts: int = 0
    counterparties: int = 0
    settled_value: dict = field(default_factory=dict)   # asset -> base-unit int (string on export)
    flags: int = 0
    paid_receipts: int = 0

    def to_json(self) -> dict:
        return {
            "did": self.did, "score": round(self.score, 6), "receipts_counted": self.receipts,
            "distinct_counterparties": self.counterparties,
            "settled_value": {a: str(v) for a, v in sorted(self.settled_value.items())},
            "reputable_flags": self.flags, "receipts_paid": self.paid_receipts,
            "transferable": False,
        }


def _valid(messages: Iterable[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in messages:
        try:
            codec.validate(m)
        except codec.AgbError:
            continue
        out.setdefault(m["cid"], m)
    return out


def _receipt_key(m: dict) -> tuple:
    return (m["from"], m.get("ref"), m["b"]["payee"])


def _receipt_tips(idx: dict[str, dict], now_ms: int) -> dict[tuple, dict]:
    """Current receipt per (payer, ref, payee): walk the explicit `supersedes` chain."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for m in idx.values():
        if m["t"] == "receipt" and m["ts"] <= now_ms:
            groups[_receipt_key(m)].append(m)
    tips: dict[tuple, dict] = {}
    for key, rs in groups.items():
        rs.sort(key=lambda r: (r["ts"], r["cid"]))
        roots = [r for r in rs if "supersedes" not in r["b"]]
        if not roots:
            continue
        tip = roots[0]
        while True:
            nxt = next((r for r in rs if r["b"].get("supersedes") == tip["cid"]
                        and (r["ts"], r["cid"]) > (tip["ts"], tip["cid"])), None)
            if nxt is None:
                break
            tip = nxt
        tips[key] = tip
    return tips


receipt_tips = _receipt_tips  # public: the indexer uses the same chain rule for job status


def _qualifying_receipts(idx: dict[str, dict], now_ms: int, p: Params, attester_ok) -> list[dict]:
    attests: dict[str, list[dict]] = defaultdict(list)
    for m in idx.values():
        if m["t"] == "attest":
            attests[m["ref"]].append(m)
    current = _receipt_tips(idx, now_ms)
    out = []
    for m in idx.values():
        if m["t"] != "receipt":
            continue
        if current.get(_receipt_key(m)) is not m:
            continue  # superseded by a newer receipt from the same payer for the same claim
        b = m["b"]
        payer, payee = m["from"], b["payee"]
        if b["outcome"] != "settled" or payer == payee or not b["railRef"].strip():
            continue
        if b["amount"]["asset"] not in p.decimals or int(b["amount"]["amt"]) <= 0:
            continue
        if not (now_ms - p.window_ms <= m["ts"] <= now_ms):
            continue
        if p.verified_settlements is not None and (b["rail"], b["railRef"]) not in p.verified_settlements:
            continue
        if p.require_claim_link:
            claim = idx.get(m["ref"])
            if not claim or claim["t"] != "claim" or claim["from"] != payee:
                continue
        good, bad = set(), set()
        for a in attests.get(m["cid"], ()):
            who = a["from"]
            if who in (payer, payee) or not attester_ok(who):
                continue
            (good if a["b"]["verdict"] == "valid" else bad).add(who)
        if not good or len(bad) >= len(good):
            continue
        out.append(m)
    return sorted(out, key=lambda r: (r["ts"], r["cid"]))


def _score(receipts: list[dict], now_ms: int, p: Params) -> dict[str, Rep]:
    reps: dict[str, Rep] = {}
    contrib: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    pair_hist: dict[tuple, list[int]] = defaultdict(list)
    for r in receipts:
        payer, payee = r["from"], r["b"]["payee"]
        asset, amt = r["b"]["amount"]["asset"], int(r["b"]["amount"]["amt"])
        hist = [t for t in pair_hist[(payer, payee)] if r["ts"] - t <= p.pair_window_ms]
        k = len(hist) + 1
        pair_hist[(payer, payee)] = hist + [r["ts"]]
        units = amt / (10 ** p.decimals[asset])
        age = max(0, now_ms - r["ts"])
        base = math.sqrt(units) * (1.0 / k) * (0.5 ** (age / p.half_life_ms))
        contrib[payee][payer] += base
        rep = reps.setdefault(payee, Rep(payee))
        rep.receipts += 1
        rep.settled_value[asset] = rep.settled_value.get(asset, 0) + amt
        reps.setdefault(payer, Rep(payer)).paid_receipts += 1
    for did, by_cp in contrib.items():
        total = sum(by_cp.values())
        capped = sum(min(c, p.cap_frac * total) for c in by_cp.values())
        rep = reps[did]
        rep.counterparties = len(by_cp)
        diversity = rep.counterparties / rep.receipts if rep.receipts else 0.0
        rep.score = capped * diversity
    return reps


def compute(messages: Iterable[dict], now_ms: int, params: Params | None = None) -> dict[str, Rep]:
    """Pure: same messages + now + params -> same scores. Invalid/unsigned input is ignored."""
    p = params or Params()
    idx = _valid(messages)
    trusted = p.trusted_attesters
    pass1 = _score(_qualifying_receipts(idx, now_ms, p, lambda d: trusted is None or d in trusted), now_ms, p)
    if p.min_attester_score > 0:
        def ok(d):
            return (trusted is None or d in trusted) and pass1.get(d, Rep(d)).score >= p.min_attester_score
        reps = _score(_qualifying_receipts(idx, now_ms, p, ok), now_ms, p)
    else:
        reps = pass1
    # flags: only distinct flaggers with pass-1 score > 0 count (empty sybil DIDs can't grief)
    flaggers: dict[str, set] = defaultdict(set)
    for m in idx.values():
        if m["t"] == "flag" and m["b"]["reason"] in ("spam", "scam", "abuse", "injection"):
            target = idx.get(m["ref"])
            if target and target["from"] != m["from"] and pass1.get(m["from"], Rep("")).score > 0:
                flaggers[target["from"]].add(m["from"])
    for did, who in flaggers.items():
        rep = reps.setdefault(did, Rep(did))
        rep.flags = len(who)
        rep.score *= 1.0 - min(p.flag_max, p.flag_step * len(who))
    return reps


def get(reps: dict[str, Rep], did: str) -> dict:
    return reps.get(did, Rep(did)).to_json()
