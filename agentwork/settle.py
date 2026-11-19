"""Settlement verification: check a receipt's (rail, railRef) against the rail itself.

Reputation trusts nothing a payer says about money. A receipt claims "I paid `payee`
`amount` on `rail`, reference `railRef`". A `SettlementVerifier` answers whether the rail
agrees:

    check(rail, railRef, payee, amount) -> True   # found, same payee, same asset and amt
                                        -> False  # rail handled, but missing or mismatched
                                        -> None   # this verifier does not know that rail

`verified_settlements(msgs, verifier)` turns verifier answers into the
`Params.verified_settlements` set that `reputation.compute` already accepts.

Rules (fail closed):
- amounts are base-unit decimal strings, compared exactly; floats are rejected;
- one settlement pays one receipt: if two different `settled` receipts (payer, payee or
  amount differ) name the same (rail, railRef), none of them is verified. Byte-identical
  claims collapse to one, so a rebroadcast is harmless;
- a receipt missing `from`, `payee` or `amount` makes its (rail, railRef) unverifiable;
- `None` (unknown rail) is never treated as verified.

PaperRail reads a local JSON ledger and never touches the network:

    {"rail": "paper",
     "settlements": [{"railRef": "p-1", "payee": "did:key:z...", "asset": "FLOP", "amt": "1000"}]}
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Protocol

_AMT = re.compile(r"^(0|[1-9][0-9]*)$")


class SettlementVerifier(Protocol):
    def check(self, rail: str, rail_ref: str, payee: str, amount: dict) -> bool | None: ...


def _amt_ok(v) -> bool:
    return isinstance(v, str) and bool(_AMT.match(v))


class PaperRail:
    """Verifier for the `paper` rail (or any rail id given) backed by a local JSON ledger."""

    def __init__(self, entries: Iterable[dict], rail: str = "paper"):
        self.rail = rail
        self._by_ref: dict[str, dict] = {}
        dupes: set[str] = set()
        for e in entries:
            ref, payee, asset, amt = e.get("railRef"), e.get("payee"), e.get("asset"), e.get("amt")
            if not (isinstance(ref, str) and ref.strip() and isinstance(payee, str) and isinstance(asset, str)):
                raise ValueError(f"bad ledger entry: {e!r}")
            if not _amt_ok(amt):
                raise ValueError(f"amt must be a base-unit decimal string: {amt!r}")
            if ref in self._by_ref:
                dupes.add(ref)
            self._by_ref[ref] = {"payee": payee, "asset": asset, "amt": amt}
        for ref in dupes:  # an ambiguous ledger line proves nothing
            del self._by_ref[ref]

    @classmethod
    def load(cls, path: str | Path) -> "PaperRail":
        doc = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=_no_float)
        return cls(doc.get("settlements", []), rail=doc.get("rail", "paper"))

    def check(self, rail: str, rail_ref: str, payee: str, amount: dict) -> bool | None:
        if rail != self.rail:
            return None
        e = self._by_ref.get(rail_ref)
        if e is None or not isinstance(amount, dict) or not _amt_ok(amount.get("amt")):
            return False
        return e["payee"] == payee and e["asset"] == amount.get("asset") and e["amt"] == amount["amt"]


def _no_float(s: str):
    raise ValueError(f"floats are forbidden in a settlement ledger: {s}")


def _claim(m: dict) -> tuple | None:
    b = m["b"]
    frm, payee, amount = m.get("from"), b.get("payee"), b.get("amount")
    if not (isinstance(frm, str) and isinstance(payee, str) and isinstance(amount, dict)):
        return None
    return frm, payee, json.dumps(amount, sort_keys=True)


def verified_settlements(msgs: Iterable[dict], verifier: SettlementVerifier) -> frozenset:
    """Set of (rail, railRef) whose single settled receipt the verifier confirms."""
    claims: dict[tuple, list[dict]] = {}
    for m in msgs:
        if m.get("t") != "receipt" or m.get("b", {}).get("outcome") != "settled":
            continue
        b = m["b"]
        claims.setdefault((b.get("rail"), b.get("railRef")), []).append(m)
    out = set()
    for (rail, ref), ms in sorted(claims.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        if not isinstance(rail, str) or not isinstance(ref, str):
            continue
        claimants = {_claim(m) for m in ms}
        if None in claimants or len(claimants) != 1:
            continue  # malformed, or two different receipts claim one settlement
        b = ms[0]["b"]
        if verifier.check(rail, ref, b["payee"], b["amount"]) is True:
            out.add((rail, ref))
    return frozenset(out)
