import json

import pytest

from agentwork import codec, didkey
from conftest import ROOT

V = json.loads((ROOT / "vectors" / "agb1-v1.json").read_text())


@pytest.mark.parametrize("v", V["valid"], ids=lambda v: v["name"])
def test_valid_vectors(v):
    from agentwork.testkeys import test_key
    key = test_key(v["key_label"])
    assert key.did == v["did"]
    assert codec.canonical(v["core"]) == v["canonical_core"]
    assert codec.compute_cid(v["core"]) == v["cid"]
    env = codec.decode(v["wire"])
    assert codec.encode(env) == v["wire"]
    c = v["core"]
    again = codec.build(key, c["t"], c["room"], c["b"], ts=c["ts"], n=c["n"], ref=c.get("ref"), prev=c.get("prev"),
                        k=c.get("k"), lang=c.get("lang"), att=c.get("att"))
    assert codec.encode(again) == v["wire"]  # Ed25519 is deterministic


@pytest.mark.parametrize("v", V["invalid"], ids=lambda v: v["name"])
def test_invalid_vectors(v):
    with pytest.raises(codec.AgbError):
        codec.decode(v["line"])


def test_fresh_key_round_trip(keys):
    k = keys["a"]
    env = codec.build(k, "ask", "app/demo/jobs", {"task": "naïve ✓ 🚀", "budget": {"asset": "FLOP", "max": "5"}}, k=["x"])
    line = codec.encode(env)
    assert line.isascii()
    assert codec.decode(line)["cid"] == env["cid"]
    assert repr(k).startswith("SigningKey(did=")


def test_other_key_cannot_sign_for_from(keys):
    env = codec.build(keys["a"], "note", "core/builders", {"text": "hi"})
    env["sig"] = keys["b"].sign(codec.SIG_DOMAIN + env["cid"].encode())
    with pytest.raises(codec.AgbError, match="signature"):
        codec.validate(env)


def test_swap_from_breaks_cid(keys):
    env = codec.build(keys["a"], "note", "core/builders", {"text": "hi"})
    env["from"] = keys["b"].did
    with pytest.raises(codec.AgbError):
        codec.validate(env)


@pytest.mark.parametrize("body", [
    {"task": "", "budget": {"asset": "FLOP", "max": "1"}},
    {"task": "x", "budget": {"asset": "flop", "max": "1"}},
    {"task": "x", "budget": {"asset": "FLOP", "max": "01"}},
    {"task": "x", "budget": {"asset": "FLOP", "max": 1}},
    {"task": "x", "budget": {"asset": "FLOP", "max": "1"}, "due": True},
    {"task": "x", "budget": {"asset": "FLOP", "max": "1"}, "due": 2**60},
    {"task": "x" * 1001, "budget": {"asset": "FLOP", "max": "1"}},
])
def test_bad_ask_bodies(keys, body):
    with pytest.raises(codec.AgbError):
        codec.build(keys["a"], "ask", "core/builders", body)


def test_refs_required_and_rooms(keys):
    with pytest.raises(codec.AgbError):
        codec.build(keys["a"], "claim", "core/builders", {"deliverable": "x"})
    with pytest.raises(codec.AgbError):
        codec.build(keys["a"], "note", "Core/Builders", {"text": "x"})
    with pytest.raises(codec.AgbError):
        codec.build(keys["a"], "note", "a/b/c/d/e", {"text": "x"})


def test_duplicate_keys_rejected(keys):
    line = codec.encode(codec.build(keys["a"], "note", "core/builders", {"text": "x"}))
    dup = line.replace('{"b":{"text":"x"}', '{"b":{"text":"x","text":"x"}')
    with pytest.raises(codec.AgbError):
        codec.decode(dup)


def test_wire_cap(keys):
    with pytest.raises(codec.AgbError):
        codec.build(keys["a"], "note", "core/builders", {"text": "\u00e9" * 1000})  # 6 chars each escaped


def test_did_roundtrip(keys):
    did = keys["c"].did
    assert didkey.did_from_public(didkey.public_key(did)) == did
    assert not didkey.is_did("did:key:z6Mkbad")
