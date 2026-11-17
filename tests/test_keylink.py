import json

import pytest
from eth_account import Account

from agentwork import keylink, lineage
from conftest import ROOT

VEC = json.loads((ROOT / "vectors" / "keylink-v1.json").read_text())
T = 1795000000000


def test_ss58_known_vector():
    s = VEC["ss58_sanity"]
    assert keylink.ss58_encode(bytes.fromhex(s["pub_hex"])) == s["ss58"]
    assert keylink.ss58_decode(s["ss58"]).hex() == s["pub_hex"]


def test_ss58_rejects_bad_checksum_and_prefix():
    s = VEC["ss58_sanity"]["ss58"]
    bad = s[:-1] + ("A" if s[-1] != "A" else "B")
    with pytest.raises(keylink.KeylinkError):
        keylink.ss58_decode(bad)
    with pytest.raises(keylink.KeylinkError):
        keylink.ss58_decode(keylink.ss58_encode(b"\x01" * 32, prefix=0))


@pytest.mark.parametrize("d", VEC["path_a"], ids=lambda d: d["key_label"])
def test_path_a_derivation_vectors(d):
    assert d["multicodec_hex"] == "ed01"
    assert keylink.account_from_did(d["did"]) == d["account_id32"] == "0x" + d["ed25519_pub_hex"]
    assert keylink.ss58_from_did(d["did"]) == d["ss58"]
    assert keylink.did_from_account(d["ss58"]) == keylink.did_from_account(d["account_id32"]) == d["did"]


def test_path_a_fresh_key_roundtrip(keys):
    k = keys["a"]
    acct = keylink.account_from_did(k.did)
    assert keylink.did_from_account(keylink.ss58_encode(bytes.fromhex(acct[2:]))) == k.did


@pytest.mark.parametrize("v", VEC["valid"] + VEC["invalid"], ids=lambda v: v["name"])
def test_vectors(v):
    res = keylink.verify(v["doc"], now_ms=T + 1000)
    assert res.ok == v["valid"], res.errors
    assert res.cid == v["cid"]
    if v["evm_text"]:
        assert keylink.evm_text(v["doc"]["record"], v["cid"]) == v["evm_text"]


def test_vector_paths():
    paths = {v["name"]: v["path"] for v in VEC["valid"]}
    assert paths == {"path-a-ed25519-did-is-account": "A", "path-b-separate-ed25519-flop-key": "B", "path-a-imd-seat-device-evm": "A"}


def _rec(k, **kw):
    r = {"v": keylink.FORMAT, "did": k.did, "issued": T, "nonce": "0123456789abcdef",
         "flop": {"account": keylink.account_from_did(k.did), "scheme": "ed25519", "net": "flop-testnet"}}
    r.update(kw)
    return r


def test_path_a_rejects_redundant_flop_sig(keys):
    k = keys["a"]
    doc = keylink.make_doc(_rec(k), did_key=k, flop_key=k)
    assert not keylink.verify(doc).ok


def test_sr25519_claim_on_ed25519_key_is_path_b_and_fails_closed(keys):
    k = keys["a"]
    doc = keylink.make_doc(_rec(k, flop={"account": keylink.account_from_did(k.did), "scheme": "sr25519", "net": "flop-testnet"}), did_key=k)
    res = keylink.verify(doc)
    assert res.path == "B" and not res.ok


def test_expiry_and_future(keys):
    k = keys["a"]
    doc = keylink.make_doc(_rec(k, expires=T + 10), did_key=k)
    assert keylink.verify(doc, now_ms=T + 5).ok
    assert "link has expired" in keylink.verify(doc, now_ms=T + 10).errors
    assert "issued is in the future" in keylink.verify(doc, now_ms=T - 10 * keylink.CLOCK_SKEW_MS).errors


def test_imd_requires_evm(keys):
    k = keys["a"]
    imd = {"chainId": 1, "collection": "0x" + "ab" * 20, "tokenId": "1", "agentId": "2", "identityRegistry": "0x" + "cd" * 20}
    with pytest.raises(keylink.KeylinkError):
        keylink.make_doc(_rec(k, imd=imd), did_key=k)


def test_imd_onchain_checks_listed():
    v = next(x for x in VEC["valid"] if x["name"] == "path-a-imd-seat-device-evm")
    res = keylink.verify(v["doc"])
    assert any("ownerOf(42)" in c for c in res.onchain_checks)


def test_unknown_fields_rejected(keys):
    k = keys["a"]
    with pytest.raises(keylink.KeylinkError):
        keylink.make_doc(_rec(k, extra="x"), did_key=k)


def test_revocation_vector_and_any_signer(keys):
    link = next(x for x in VEC["valid"] if x["name"] == "path-a-imd-seat-device-evm")["doc"]
    assert keylink.verify_revocation(VEC["revocation"]["doc"], link).ok
    other = Account.create()
    forged = keylink.make_revocation(link, by="evm", key=other, issued=T, nonce="00000000000000ff")
    assert not keylink.verify_revocation(forged, link).ok


def test_revocation_by_did_and_outsider(keys):
    k, x = keys["a"], keys["b"]
    link = keylink.make_doc(_rec(k), did_key=k)
    assert keylink.verify_revocation(keylink.make_revocation(link, by="did", key=k, issued=T, nonce="00000000000000aa"), link).ok
    assert not keylink.verify_revocation(keylink.make_revocation(link, by="did", key=x, issued=T, nonce="00000000000000ab"), link).ok
    assert not keylink.verify_revocation(keylink.make_revocation(link, by="flop", key=k, issued=T, nonce="00000000000000ac"), link).ok


def test_lineage_vector_resolves_latest():
    L = VEC["lineage"]
    got = keylink.active_link(L["lines"], L["links"], now_ms=T + 1000)
    assert got == L["active"] and got["path"] == "A"


def test_lineage_link_must_be_signed_by_entry_key(tmp_path, keys):
    k, other = keys["a"], keys["b"]
    log = lineage.LineageLog(tmp_path / "l.jsonl")
    log.incept(k, "demo", ts=T)
    foreign = keylink.make_doc(_rec(other), did_key=other)
    log.append(k, "entry", what="decision", ref=keylink.lineage_ref(keylink.record_cid(foreign["record"])), ts=T + 1)
    assert keylink.active_link(log.lines(), [foreign])["active"] is None


def test_lineage_revoked_link_inactive(tmp_path, keys):
    k = keys["a"]
    log = lineage.LineageLog(tmp_path / "l.jsonl")
    log.incept(k, "demo", ts=T)
    link = keylink.make_doc(_rec(k), did_key=k)
    log.append(k, "entry", what="decision", ref=keylink.lineage_ref(keylink.record_cid(link["record"])), ts=T + 1)
    assert keylink.active_link(log.lines(), [link])["active"] == keylink.record_cid(link["record"])
    rv = keylink.make_revocation(link, by="did", key=k, issued=T + 2, nonce="00000000000000bb")
    got = keylink.active_link(log.lines(), [link], [rv])
    assert got["active"] is None and got["reason"] == "revoked"


def test_vectors_contain_no_seeds():
    import hashlib
    text = (ROOT / "vectors" / "keylink-v1.json").read_text()
    for label in ("keylink-agent", "keylink-agent-2", "keylink-flop", "keylink-device", "keylink-evm"):
        seed = hashlib.sha256(b"agentwork public test key: " + label.encode()).hexdigest()
        assert seed not in text
    assert "mnemonic" not in text.lower()
