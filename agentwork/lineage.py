"""Lineage log: a hash-linked, signed record of one agent across model and key changes.

Each entry is an agb1 envelope with t="lineage". The lineage id is
"lin:" + cid(inception). Every later entry names the lineage, the next seq and the
cid of the previous entry, and is signed by the lineage's *current* key. Editing,
reordering or deleting any entry breaks verification.

Kinds: inception, entry (activity pointer), checkpoint (Merkle root over a seq
range), handoff (model/runtime change), rotation (new key; must match the committed
next_key_hash when one was set), tombstone (final record; nothing may follow).

Successor countersign: the first entry after a handoff MAY carry `ack` = cid of that handoff
(if that first entry is a rotation, the ack may instead ride on the entry after the rotation, so
the successor's new key signs it). An `ack` anywhere else, or naming another cid, fails
verification; handoffs never acknowledged are reported in `ChainReport.unacked_handoffs`.

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



def _parent(a: bytes, b: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + a + b).digest()


def merkle_proof(cids: list[str], index: int) -> list[dict]:
    """Inclusion proof for cids[index] under `merkle_root(cids)` (same odd-layer padding).

    Each step is {"side": "L"|"R", "hash": "0x.."}: the sibling's side relative to the path.
    """
    if not 0 <= index < len(cids):
        raise ValueError("index out of range")
    layer = [bytes.fromhex(c[2:]) for c in cids]
    i, proof = index, []
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        sib = i ^ 1
        proof.append({"side": "L" if sib < i else "R", "hash": "0x" + layer[sib].hex()})
        layer = [_parent(layer[j], layer[j + 1]) for j in range(0, len(layer), 2)]
        i //= 2
    return proof


def verify_proof(leaf_cid: str, proof: list[dict], root: str) -> bool:
    """True if `leaf_cid` with `proof` hashes to `root`. Malformed input is False, never an exception."""
    try:
        h = bytes.fromhex(leaf_cid[2:])
        if not leaf_cid.startswith("0x") or len(h) != 32 or len(proof) > 64:
            return False
        for step in proof:
            sib = bytes.fromhex(step["hash"][2:])
            if not step["hash"].startswith("0x") or len(sib) != 32 or step["side"] not in ("L", "R"):
                return False
            h = _parent(sib, h) if step["side"] == "L" else _parent(h, sib)
        return "0x" + h.hex() == root
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def export_checkpoint(lines: list[str], checkpoint: int, leaf: int | None = None) -> dict:
    """Shareable bundle: the signed checkpoint line, its range, and (optionally) a proof for one entry.

    The chain must verify first; the bundle is self-checking with `verify_export`.
    """
    rep = verify_chain(lines)
    if not rep.ok:
        raise ValueError("chain does not verify: " + "; ".join(rep.errors[:3]))
    envs = [codec.decode(x) for x in lines]
    if not 0 <= checkpoint < len(envs) or envs[checkpoint]["t"] != "lineage" or envs[checkpoint]["b"].get("kind") != "checkpoint":
        raise ValueError(f"entry {checkpoint} is not a checkpoint")
    b = envs[checkpoint]["b"]
    lo, hi = b["from_seq"], b["to_seq"]
    out = {"format": "agentwork.lineage-checkpoint/1", "lin": rep.lin, "checkpoint_line": lines[checkpoint],
           "range": [lo, hi], "root": b["root"]}
    if leaf is not None:
        if not lo <= leaf <= hi:
            raise ValueError(f"entry {leaf} is outside checkpoint range {lo}..{hi}")
        out["leaf"] = {"seq": leaf, "line": lines[leaf],
                       "proof": merkle_proof([e["cid"] for e in envs[lo:hi + 1]], leaf - lo)}
    return out


def verify_export(bundle: dict) -> bool:
    """Check signatures on the checkpoint (and leaf) lines and the leaf's inclusion proof."""
    try:
        cp = codec.verify_line(bundle["checkpoint_line"])
        if not cp.ok:
            return False
        env = codec.decode(bundle["checkpoint_line"])
        if env["b"].get("kind") != "checkpoint" or env["b"].get("root") != bundle["root"]:
            return False
        if "leaf" not in bundle:
            return True
        lv = codec.verify_line(bundle["leaf"]["line"])
        lenv = codec.decode(bundle["leaf"]["line"])
        lin = env["b"].get("lin")
        if not lv.ok or not (lenv.get("b", {}).get("lin") == lin or "lin:" + lenv["cid"] == lin):
            return False  # leaf must belong to the same lineage (inception cid = lin)
        return verify_proof(lenv["cid"], bundle["leaf"]["proof"], env["b"]["root"])
    except (KeyError, TypeError, ValueError):
        return False

@dataclass
class ChainReport:
    ok: bool
    lin: str | None = None
    entries: int = 0
    head: str | None = None
    current_key: str | None = None
    tombstoned: bool = False
    handoffs: list = field(default_factory=list)
    unacked_handoffs: list = field(default_factory=list)  # [{"seq", "cid"}] never countersigned
    awaiting_ack: str | None = None                       # handoff cid the next entry may ack
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
            if "ack" in b and b["ack"] != rep.awaiting_ack:
                rep.errors.append(f"entry {i}: ack does not name the handoff awaiting acknowledgement")
                return rep
            if rep.awaiting_ack is not None:
                if "ack" in b:
                    rep.handoffs[-1]["acked_by"] = i
                    rep.awaiting_ack = None
                elif not (kind == "rotation" and envs[-1]["b"]["kind"] == "handoff"):
                    rep.awaiting_ack = None  # window closed without a countersign
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
                rep.handoffs.append({"seq": i, "from_model": b["from_model"], "to_model": b["to_model"], "cid": env["cid"],
                                     "acked_by": None})
                rep.awaiting_ack = env["cid"]
            elif kind == "tombstone":
                rep.tombstoned = True
        envs.append(env)
    if not envs:
        rep.errors.append("empty log")
        return rep
    rep.unacked_handoffs = [{"seq": h["seq"], "cid": h["cid"]} for h in rep.handoffs if h["acked_by"] is None]
    rep.ok, rep.entries, rep.head = True, len(envs), envs[-1]["cid"]
    return rep


class LineageLog:
    """Append-only local lineage log (JSONL of agb1 wire lines). Writes only to `path`."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def lines(self) -> list[str]:
        return self.path.read_text().splitlines() if self.path.exists() else []

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

    def append(self, key: didkey.SigningKey, kind: str, ts: int | None = None, **fields) -> dict:
        st = self.state()
        if st.tombstoned:
            raise ValueError("lineage is tombstoned")
        if key.did != st.current_key:
            raise ValueError("key is not the lineage's current key")
        lines = self.lines()
        prev = json.loads(lines[-1][len(codec.PREFIX):])["cid"]
        if fields.get("ack") is True:  # countersign the handoff awaiting acknowledgement
            if st.awaiting_ack is None:
                raise ValueError("no handoff is awaiting acknowledgement")
            fields["ack"] = st.awaiting_ack
        b = {"kind": kind, "seq": st.entries, "lin": st.lin, "prev": prev, **fields}
        if kind == "checkpoint" and "root" not in b:
            cids = [json.loads(l[len(codec.PREFIX):])["cid"] for l in lines]
            lo, hi = b.setdefault("from_seq", 0), b.setdefault("to_seq", len(cids) - 1)
            b["root"] = merkle_root(cids[lo:hi + 1])
        room = "lineage/" + st.lin
        return self._write(codec.build(key, "lineage", room, b, ts=ts))
