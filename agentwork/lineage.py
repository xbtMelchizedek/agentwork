"""Lineage log: a hash-linked, signed record of one agent across model and key changes.

Each entry is an agb1 envelope with t="lineage". The lineage id is
"lin:" + cid(inception). Every later entry names the lineage, the next seq and the
cid of the previous entry, and is signed by the lineage's *current* key. Editing,
reordering or deleting any entry breaks verification.

Kinds: inception, entry (activity pointer), checkpoint (Merkle root over a seq
range), handoff (model/runtime change), rotation (new key; must match the committed
next_key_hash when one was set), tombstone (final record; nothing may follow).

This is continuity of *record*, not of mind: a successor reads and verifies the log.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import codec, didkey


def next_key_hash(did: str) -> str:
    return "0x" + hashlib.sha256(b"agb1-next-key|" + did.encode("ascii")).hexdigest()


def merkle_root(cids: list[str]) -> str:
    if not cids:
        raise ValueError("empty range")
    layer = [bytes.fromhex(c[2:]) for c in cids]
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [hashlib.sha256(b"\x01" + layer[i] + layer[i + 1]).digest() for i in range(0, len(layer), 2)]
    return "0x" + layer[0].hex()


@dataclass
class ChainReport:
    ok: bool
    lin: str | None = None
    entries: int = 0
    head: str | None = None
    current_key: str | None = None
    tombstoned: bool = False
    handoffs: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def to_json(self) -> dict:
        return self.__dict__.copy()


def verify_chain(lines: list[str]) -> ChainReport:
    rep = ChainReport(ok=False)
    envs: list[dict] = []
    commit: str | None = None
    for i, line in enumerate(l for l in lines if l.strip()):
        try:
            env = codec.decode(line)
        except codec.AgbError as e:
            rep.errors.append(f"entry {i}: {e}")
            return rep
        if env["t"] != "lineage":
            rep.errors.append(f"entry {i}: not a lineage message")
            return rep
        b = env["b"]
        if i == 0:
            if b["kind"] != "inception":
                rep.errors.append("entry 0: first entry must be inception")
                return rep
            rep.lin = "lin:" + env["cid"]
            rep.current_key = env["from"]
            commit = b.get("next_key_hash")
        else:
            if rep.tombstoned:
                rep.errors.append(f"entry {i}: entry after tombstone")
                return rep
            if b.get("lin") != rep.lin:
                rep.errors.append(f"entry {i}: wrong lineage id")
                return rep
            if b["seq"] != i:
                rep.errors.append(f"entry {i}: seq {b['seq']} != {i} (gap or reorder)")
                return rep
            if b.get("prev") != envs[-1]["cid"]:
                rep.errors.append(f"entry {i}: prev does not match previous entry (edit or deletion)")
                return rep
            if env["from"] != rep.current_key:
                rep.errors.append(f"entry {i}: signed by a key that is not the lineage's current key")
                return rep
            if env["ts"] < envs[-1]["ts"]:
                rep.errors.append(f"entry {i}: timestamp goes backwards")
                return rep
            kind = b["kind"]
            if kind == "rotation":
                if commit and next_key_hash(b["new_key"]) != commit:
                    rep.errors.append(f"entry {i}: new key does not match committed next_key_hash")
                    return rep
                rep.current_key = b["new_key"]
                commit = b.get("next_key_hash")
            elif kind == "checkpoint":
                lo, hi = b["from_seq"], b["to_seq"]
                if not (0 <= lo <= hi < i) or merkle_root([e["cid"] for e in envs[lo:hi + 1]]) != b["root"]:
                    rep.errors.append(f"entry {i}: checkpoint root does not match entries {lo}..{hi}")
                    return rep
            elif kind == "handoff":
                rep.handoffs.append({"seq": i, "from_model": b["from_model"], "to_model": b["to_model"], "cid": env["cid"]})
            elif kind == "tombstone":
                rep.tombstoned = True
        envs.append(env)
    if not envs:
        rep.errors.append("empty log")
        return rep
    rep.ok, rep.entries, rep.head = True, len(envs), envs[-1]["cid"]
    return rep


class LineageLog:
    """Append-only local lineage log (JSONL of agb1 wire lines). Writes only to `path`."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def lines(self) -> list[str]:
        return self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []

    def state(self) -> ChainReport:
        rep = verify_chain(self.lines())
        if not rep.ok:
            raise ValueError("lineage log does not verify: " + "; ".join(rep.errors))
        return rep

    def _write(self, env: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(codec.encode(env) + "\n")
        return env

    def incept(self, key: didkey.SigningKey, purpose: str, *, name: str | None = None, owner: str | None = None,
               model: str | None = None, next_key: str | None = None, ts: int | None = None) -> dict:
        if self.lines():
            raise ValueError("log already has an inception")
        b: dict = {"kind": "inception", "seq": 0, "purpose": purpose}
        for k, v in (("name", name), ("owner", owner), ("model", model)):
            if v is not None:
                b[k] = v
        if next_key:
            b["next_key_hash"] = next_key_hash(next_key)
        return self._write(codec.build(key, "lineage", "lineage", b, ts=ts))

    def append(self, key: didkey.SigningKey, kind: str, /, ts: int | None = None, **fields) -> dict:
        st = self.state()
        if st.tombstoned:
            raise ValueError("lineage is tombstoned")
        if key.did != st.current_key:
            raise ValueError("key is not the lineage's current key")
        reserved = sorted({"kind", "seq", "lin", "prev"} & fields.keys())
        if reserved:  # chain fields are set here; letting --fields override them would write an unverifiable entry
            raise ValueError(f"field(s) {', '.join(reserved)} are set by the log, not by the caller")
        lines = self.lines()
        prev = json.loads(lines[-1][len(codec.PREFIX):])["cid"]
        b = {"kind": kind, "seq": st.entries, "lin": st.lin, "prev": prev, **fields}
        if kind == "checkpoint" and "root" not in b:
            cids = [json.loads(l[len(codec.PREFIX):])["cid"] for l in lines]
            lo, hi = b.setdefault("from_seq", 0), b.setdefault("to_seq", len(cids) - 1)
            b["root"] = merkle_root(cids[lo:hi + 1])
        room = "lineage/" + st.lin
        env = codec.build(key, "lineage", room, b, ts=ts)
        check = verify_chain(lines + [codec.encode(env)])  # never append an entry that breaks the chain
        if not check.ok:
            raise ValueError("entry would not verify: " + "; ".join(check.errors))
        return self._write(env)
