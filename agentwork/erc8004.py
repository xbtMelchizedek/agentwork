"""ERC-8004 export of an agb1 reputation summary (a file; never an on-chain write).

ERC-8004 (Draft) feedback is `value` (int128) + `valueDecimals` (0..18), optional `tag1`/`tag2`,
and an optional off-chain feedback file. `export()` builds that file for one DID:

    value         = round(score * 10**6)    valueDecimals = 6
    tag1 = "agb1Rep"   tag2 = "v0"          createdAt = `now` as ISO-8601 UTC

plus an `agentwork` block with everything needed to recompute it: the DID, `now`, the
reputation params, the counted receipt cids, and `evidence`, the closed set of message cids
the score depends on. `recompute(doc, messages)` runs `reputation.compute` over only those
cids and returns the score; `verify(doc, messages)` checks it reproduces `value` exactly.

Why the evidence set is closed (for reputation v0 on main): a DID's score depends only on
(a) receipts naming it as payee, each judged locally from its supersedes group, its claim
and the attestations on it; (b) flags on its messages, which count only if the flagger has
pass-1 score > 0; (c) with `min_attester_score > 0`, the attesters' pass-1 scores. Pass-1
scores depend only on each DID's own payee receipts. So evidence = whole receipt groups
with payee DID + their claims + attests, flags on DID's messages + their targets, and the
same receipt closure for every flagger and (if needed) every attester. One hop, no recursion.

Not carried (lost in the mapping): agentRegistry/agentId/clientAddress are on-chain
identities agb1 does not have; they stay null unless the caller supplies them. The score is
an indexer's computation, not a client's rating, so whoever submits it is the "client".
feedbackHash (keccak256 of this file) is left to whoever submits; we never submit.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Iterable

from . import reputation

VALUE_DECIMALS = 6
TAG1, TAG2 = "agb1Rep", "v0"
SCHEMA = "agentwork/erc8004-export/v0"


def _params_json(p: reputation.Params) -> dict:
    out = {}
    for f in dataclasses.fields(p):
        v = getattr(p, f.name)
        out[f.name] = sorted(map(list, v) if f.name == "verified_settlements" else v) \
            if isinstance(v, frozenset) else v
    return out


def params_from_json(d: dict) -> reputation.Params:
    d = dict(d)
    if d.get("trusted_attesters") is not None:
        d["trusted_attesters"] = frozenset(d["trusted_attesters"])
    if d.get("verified_settlements") is not None:
        d["verified_settlements"] = frozenset(tuple(x) for x in d["verified_settlements"])
    return reputation.Params(**d)


def _receipt_closure(idx: dict[str, dict], payee: str, now_ms: int) -> set[str]:
    out: set[str] = set()
    keys = {reputation._receipt_key(m) for m in idx.values()
            if m["t"] == "receipt" and m["ts"] <= now_ms and m["b"]["payee"] == payee}
    for m in idx.values():
        if m["t"] == "receipt" and reputation._receipt_key(m) in keys:
            out.add(m["cid"])
            if m.get("ref") in idx:
                out.add(m["ref"])
    for m in idx.values():
        if m["t"] == "attest" and m.get("ref") in out:
            out.add(m["cid"])
    return out


def evidence(messages: Iterable[dict], did: str, now_ms: int, p: reputation.Params) -> set[str]:
    idx = reputation._valid(messages)
    ev = _receipt_closure(idx, did, now_ms)
    hops: set[str] = set()
    for m in idx.values():
        if m["t"] == "flag":
            t = idx.get(m.get("ref"))
            if t and t["from"] == did:
                ev |= {m["cid"], t["cid"]}
                hops.add(m["from"])
    if p.min_attester_score > 0:
        hops |= {idx[c]["from"] for c in ev if idx[c]["t"] == "attest"}
    for other in sorted(hops - {did}):
        ev |= _receipt_closure(idx, other, now_ms)
    return ev


def export(messages: Iterable[dict], did: str, now_ms: int, params: reputation.Params | None = None,
           agent_registry: str | None = None, agent_id: int | None = None,
           client_address: str | None = None) -> dict:
    p = params or reputation.Params()
    msgs = list(messages)
    reps = reputation.compute(msgs, now_ms, p)
    rep = reps.get(did, reputation.Rep(did))
    ev = evidence(msgs, did, now_ms, p)
    idx = reputation._valid(msgs)
    counted = [c for c in ev if idx[c]["t"] == "receipt" and idx[c]["b"]["payee"] == did
               and c in _counted(idx, did, now_ms, p)]
    return {
        "agentRegistry": agent_registry, "agentId": agent_id, "clientAddress": client_address,
        "createdAt": datetime.fromtimestamp(now_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "value": round(rep.score * 10**VALUE_DECIMALS), "valueDecimals": VALUE_DECIMALS,
        "tag1": TAG1, "tag2": TAG2,
        "agentwork": {
            "schema": SCHEMA, "did": did, "now_ms": now_ms, "params": _params_json(p),
            "summary": rep.to_json(), "receipts": sorted(counted), "evidence": sorted(ev),
            "onchain": False,
        },
    }


def _counted(idx: dict, did: str, now_ms: int, p: reputation.Params) -> set[str]:
    trusted = p.trusted_attesters
    pass1 = reputation._score(reputation._qualifying_receipts(
        idx, now_ms, p, lambda d: trusted is None or d in trusted), now_ms, p)
    if p.min_attester_score > 0:
        def ok(d):
            return (trusted is None or d in trusted) and pass1.get(d, reputation.Rep(d)).score >= p.min_attester_score
    else:
        def ok(d):
            return trusted is None or d in trusted
    return {r["cid"] for r in reputation._qualifying_receipts(idx, now_ms, p, ok) if r["b"]["payee"] == did}


def recompute(doc: dict, messages: Iterable[dict]) -> float:
    aw = doc["agentwork"]
    keep = set(aw["evidence"])
    sub = [m for m in messages if m.get("cid") in keep]
    reps = reputation.compute(sub, aw["now_ms"], params_from_json(aw["params"]))
    return reps.get(aw["did"], reputation.Rep(aw["did"])).score


def verify(doc: dict, messages: Iterable[dict]) -> bool:
    msgs = list(messages)
    have = {m.get("cid") for m in msgs}
    if not set(doc["agentwork"]["evidence"]) <= have:
        return False   # missing evidence: cannot reproduce, fail closed
    return round(recompute(doc, msgs) * 10**doc["valueDecimals"]) == doc["value"]
