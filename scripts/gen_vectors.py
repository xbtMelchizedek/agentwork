#!/usr/bin/env python3
"""Regenerate vectors/agb1-v1.json from PUBLIC deterministic test keys."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agentwork import codec  # noqa: E402
from agentwork.testkeys import test_key  # noqa: E402

TS = 1795000000000
alice, bob = test_key("alice"), test_key("bob")


def vec(name, key, label, t, room, b, **kw):
    env = codec.build(key, t, room, b, ts=TS + kw.pop("dt", 0), n=kw.pop("n"), **kw)
    core = {k: v for k, v in env.items() if k not in ("cid", "sig")}
    return {"name": name, "key_label": label, "did": key.did, "core": core,
            "canonical_core": codec.canonical(core), "cid": env["cid"], "sig": env["sig"], "wire": codec.encode(env)}


valid = [
    vec("ask-basic", alice, "alice", "ask", "core/builders",
        {"task": "Review diff of tclk PR 160; 200 words; list risks", "budget": {"asset": "FLOP", "max": "2000000000000000000", "net": "flop-testnet"}, "due": TS + 1800000},
        k=["review", "tclk"], lang="en", n="0000000000000001"),
    vec("ask-unicode", alice, "alice", "ask", "app/demo/jobs",
        {"task": "Traduire en français: «bonjour» — ✓ 🚀", "budget": {"asset": "FLOP", "max": "1"}}, lang="fr", n="0000000000000002"),
]
valid.append(vec("offer", bob, "bob", "offer", "core/builders",
                 {"price": {"asset": "FLOP", "amt": "1500000000000000000"}, "eta": 900000, "rails": ["flop-htlc"]},
                 ref=valid[0]["cid"], dt=60000, n="0000000000000003"))
valid.append(vec("lineage-inception", alice, "alice", "lineage", "lineage",
                 {"kind": "inception", "seq": 0, "purpose": "test lineage"}, n="0000000000000004"))

w0 = valid[0]["wire"]
env0 = json.loads(w0[5:])


def mutate(f):
    e = json.loads(w0[5:]); f(e); return "agb1 " + codec.canonical(e)


invalid = [
    {"name": "tampered-body", "line": mutate(lambda e: e["b"].update(task="changed"))},
    {"name": "bad-sig", "line": mutate(lambda e: e.update(sig=valid[2]["sig"]))},
    {"name": "unknown-envelope-key", "line": mutate(lambda e: e.update(x=1))},
    {"name": "unknown-body-key", "line": mutate(lambda e: e["b"].update(extra="y"))},
    {"name": "float-value", "line": w0.replace('"due":' + str(TS + 1800000), '"due":1.5')},
    {"name": "non-canonical-spacing", "line": w0.replace('{"b":', '{ "b":', 1)},
    {"name": "missing-ref-on-offer", "line": "agb1 " + codec.canonical({k: v for k, v in json.loads(valid[2]["wire"][5:]).items() if k != "ref"})},
    {"name": "wrong-prefix", "line": "agb2 " + w0[5:]},
    {"name": "schema-mismatch", "line": mutate(lambda e: e.update(s="agb.offer/1"))},
]
out = {"spec": "agb1", "version": 1,
       "keys": "seed = sha256(utf8('agentwork public test key: ' + key_label)); PUBLIC TEST KEYS",
       "cid": "0x + sha256('FLOP::agb::v1|' + canonical_core)", "sig_message": "'agb1-sig|' + cid",
       "valid": valid, "invalid": invalid}
(ROOT / "vectors").mkdir(exist_ok=True)
(ROOT / "vectors" / "agb1-v1.json").write_text(json.dumps(out, indent=1, ensure_ascii=True) + "\n")
print("wrote", len(valid), "valid and", len(invalid), "invalid vectors")
