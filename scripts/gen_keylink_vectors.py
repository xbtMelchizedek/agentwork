#!/usr/bin/env python3
"""Regenerate vectors/keylink-v1.json from PUBLIC deterministic test keys (agentwork.testkeys).

Every key here is derived from a published label, so anyone can recompute it. They are worthless
on purpose. Never fund them and never reuse them.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eth_account import Account  # noqa: E402

from agentwork import codec, keylink, lineage  # noqa: E402
from agentwork.imd import ERC8004_IDENTITY, SEAT_COLLECTION  # noqa: E402
from agentwork.testkeys import test_key  # noqa: E402

T = 1795000000000
agent, flopk, device, agent2 = (test_key(x) for x in ("keylink-agent", "keylink-flop", "keylink-device", "keylink-agent-2"))
evm = Account.from_key(hashlib.sha256(b"agentwork public test key: keylink-evm").digest())
IMD = {"chainId": 1, "collection": SEAT_COLLECTION, "tokenId": "42", "agentId": "1337", "identityRegistry": ERC8004_IDENTITY}


def path_a_derivation(key, label):
    pub = keylink.didkey.public_key(key.did)
    mc = keylink.didkey.b58decode(key.did[len("did:key:z"):])
    return {"key_label": label, "did": key.did, "multicodec_hex": mc[:2].hex(), "ed25519_pub_hex": pub.hex(),
            "account_id32": keylink.account_from_did(key.did), "ss58_prefix": keylink.SS58_PREFIX,
            "ss58": keylink.ss58_from_did(key.did)}


def rec(**kw):
    r = {"v": keylink.FORMAT, "did": agent.did, "issued": T, "nonce": "00000000000000a1",
         "flop": {"account": keylink.account_from_did(agent.did), "scheme": "ed25519", "net": "flop-testnet"}}
    r.update(kw)
    return r


def entry(name, doc, expect, why=""):
    v = keylink.verify(doc, now_ms=T + 1000)
    assert v.ok == expect, (name, v.errors)
    return {"name": name, "valid": expect, "why": why, "doc": doc, "cid": keylink.record_cid(doc["record"]),
            "path": v.path, "errors": v.errors, "evm_text": keylink.evm_text(doc["record"], keylink.record_cid(doc["record"])) if "evm" in doc["record"] else None}


a_plain = keylink.make_doc(rec(), did_key=agent)
b_sep = keylink.make_doc(rec(flop={"account": keylink.account_from_did(flopk.did), "scheme": "ed25519", "net": "flop-testnet"},
                             nonce="00000000000000b1"), did_key=agent, flop_key=flopk)
a_imd = keylink.make_doc(rec(imd={**IMD, "device": keylink.didkey.public_key(device.did).hex()}, evm={"address": evm.address.lower()},
                             nonce="00000000000000c1", expires=T + 365 * 86400000), did_key=agent, device_key=device, evm_account=evm)

b_missing = keylink.make_doc(rec(flop={"account": keylink.account_from_did(flopk.did), "scheme": "ed25519", "net": "flop-testnet"},
                                 nonce="00000000000000b2"), did_key=agent)
swapped = json.loads(json.dumps(b_sep))
swapped["sigs"]["flop"] = keylink.sign_ed25519_hex(flopk, keylink.sig_message("did", keylink.record_cid(b_sep["record"])))
tampered = json.loads(json.dumps(a_imd))
tampered["record"]["imd"]["tokenId"] = "43"
wrong_evm = keylink.make_doc(rec(imd=IMD, evm={"address": "0x" + "11" * 20}, nonce="00000000000000c2"), did_key=agent, evm_account=evm)

valid = [entry("path-a-ed25519-did-is-account", a_plain, True, "one key: did:key pubkey == AccountId32; did signature alone"),
         entry("path-b-separate-ed25519-flop-key", b_sep, True, "dual-signed: did + flop"),
         entry("path-a-imd-seat-device-evm", a_imd, True, "did + IMD device + seat-holder EIP-191")]
invalid = [entry("path-b-missing-flop-sig", b_missing, False, "flop account not proven"),
           entry("role-swap-replay", swapped, False, "flop signed the did-role message"),
           entry("tampered-after-signing", tampered, False, "cid changes, every signature fails"),
           entry("evm-address-mismatch", wrong_evm, False, "recovered signer is not the named owner")]

rev = keylink.make_revocation(a_imd, by="evm", key=evm, issued=T + 5000, nonce="00000000000000d1", reason="seat sold")
assert keylink.verify_revocation(rev, a_imd).ok

# lineage tie-in: inception -> link A -> rotation -> link for new key
import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    log = lineage.LineageLog(Path(td) / "lin.jsonl")
    log.incept(agent, "keylink demo agent", next_key=agent2.did, ts=T)
    log.append(agent, "entry", what="decision", ref=keylink.lineage_ref(keylink.record_cid(a_plain["record"])), ts=T + 1)
    log.append(agent, "rotation", new_key=agent2.did, ts=T + 2)
    a2 = keylink.make_doc({"v": keylink.FORMAT, "did": agent2.did, "issued": T + 3, "nonce": "00000000000000e1",
                           "prev": keylink.record_cid(a_plain["record"]),
                           "flop": {"account": keylink.account_from_did(agent2.did), "scheme": "ed25519", "net": "flop-testnet"}}, did_key=agent2)
    log.append(agent2, "entry", what="decision", ref=keylink.lineage_ref(keylink.record_cid(a2["record"])), ts=T + 4)
    lin_lines = log.lines()
resolved = keylink.active_link(lin_lines, [a_plain, a2], [], now_ms=T + 1000)
assert resolved["active"] == keylink.record_cid(a2["record"]), resolved

out = {
    "spec": "docs/KEYLINK.md (agentwork key-link v1)",
    "warning": "PUBLIC test keys derived from published labels (seed = sha256('agentwork public test key: ' + label)). Worthless. Never fund.",
    "path_a": [path_a_derivation(agent, "keylink-agent"), path_a_derivation(agent2, "keylink-agent-2")],
    "ss58_sanity": {"pub_hex": "d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d", "ss58": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY", "note": "Substrate well-known dev key, prefix 42"},
    "evm_test_address": evm.address.lower(),
    "valid": valid, "invalid": invalid,
    "revocation": {"link": keylink.record_cid(a_imd["record"]), "doc": rev, "cid": keylink.revocation_cid(rev["revoke"])},
    "lineage": {"lines": lin_lines, "links": [a_plain, a2], "active": resolved},
}
(ROOT / "vectors" / "keylink-v1.json").write_text(json.dumps(out, indent=1, ensure_ascii=True) + "\n")
print("wrote vectors/keylink-v1.json:", len(valid), "valid,", len(invalid), "invalid")
print(json.dumps(out["path_a"][0], indent=1))
