#!/usr/bin/env python3
"""Regenerate vectors/nonce-precision-v1.json: Technocore signed-lane nonces above 2^53.

Technocore verifies say-signed writes over `<room>|<nonce>|<text>`, and nonces run to 19
digits. A JS verifier that reads the nonce through JSON.parse gets a rounded double above
2^53 (above 2^60 doubles step by 256), rebuilds the wrong string and reports a bad sig.
Each case here carries the exact signing string, a signature from a PUBLIC test key, the
raw message JSON with the nonce as a bare number, and the value a lossy parse would give,
so an implementation can show it verifies the first and not the second.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agentwork import codec, didkey  # noqa: E402
from agentwork.testkeys import test_key  # noqa: E402

OUT = ROOT / "vectors" / "nonce-precision-v1.json"
ROOM = "builders"
TS = 1795000000000
# Two real nonce values seen on /r/builders (both above 2^60), plus edges of the safe range.
NONCES = ["1794963040209364600", "1794963169135027100", str(2**53 - 1), str(2**53 + 1), str(2**63 - 1)]


def js_number_string(digits: str) -> str:
    """What String(JSON.parse(digits)) prints in JS: the shortest round-trip double, as an integer."""
    return format(Decimal(repr(float(int(digits)))), "f").split(".")[0]


def main() -> None:
    alice = test_key("alice")
    cases = []
    for i, nonce in enumerate(NONCES, 1):
        env = codec.build(alice, "note", "core/builders", {"text": f"nonce precision case {i}"}, ts=TS, n=f"{i:016x}")
        text = codec.encode(env)
        signing = f"{ROOM}|{nonce}|{text}"
        sig = alice.sign(signing.encode("utf-8"))
        lossy = js_number_string(nonce)
        assert didkey.verify(alice.did, sig, signing.encode())
        assert (lossy == nonce) == (int(nonce) <= 2**53)
        raw = '{"did":%s,"nonce":%s,"room":%s,"seq":%d,"sig":%s,"text":%s}' % (
            json.dumps(alice.did), nonce, json.dumps(ROOM), 100 + i, json.dumps(sig), json.dumps(text))
        cases.append({"name": f"case-{i}", "nonce": nonce, "above_2_53": int(nonce) > 2**53, "lossy_js": lossy,
                      "did": alice.did, "room": ROOM, "text": text, "signing_string": signing, "sig": sig,
                      "raw_message": raw})
    big = codec.build(alice, "note", "core/builders", {"text": "big ts"}, ts=TS, n="00000000000000ff")
    wire = codec.encode(big).replace(f'"ts":{TS}', f'"ts":{NONCES[0]}')
    doc = {"spec": "Technocore say-signed: sig over <room>|<nonce>|<text>; nonce is 1-19 digits",
           "version": 1, "keys": "PUBLIC test keys: seed = sha256('agentwork public test key: ' + label)",
           "key_label": "alice", "cases": cases,
           "agb1_reject": [{"name": "ts-above-2^53", "line": wire,
                            "why": "agb1 integers are capped at 2^53-1; a larger one is rejected, never rounded"}]}
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(cases)} cases")


if __name__ == "__main__":
    main()
