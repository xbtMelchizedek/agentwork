"""Issue #24: malformed revocation entries must be skipped by active_link, never raise."""
import pytest

from agentwork import keylink, lineage

T = 1_790_000_000_000


@pytest.fixture
def linked(tmp_path, keys):
    k = keys["a"]
    log = lineage.LineageLog(tmp_path / "l.jsonl")
    log.incept(k, "demo", ts=T)
    from test_keylink import _rec as rec  # reuse the suite's record builder
    link = keylink.make_doc(rec(k), did_key=k)
    log.append(k, "entry", what="decision", ref=keylink.lineage_ref(keylink.record_cid(link["record"])), ts=T + 1)
    return k, log, link


MALFORMED = [
    None, 0, 1.5, "x", b"x", [], [None], {},
    {"revoke": None}, {"revoke": "x"}, {"revoke": []}, {"revoke": {}},
    {"revoke": {"link": None}}, {"revoke": {"link": 7}},
    {"revoke": {"link": ["a"]}}, {"revoke": {"link": {"a": 1}}},
    {"revoke": {"link": "not-a-known-cid"}, "by": "did", "sig": "x"},
]


@pytest.mark.parametrize("bad", MALFORMED, ids=[repr(b)[:30] for b in MALFORMED])
def test_malformed_revocation_is_skipped(linked, bad):
    k, log, link = linked
    cid = keylink.record_cid(link["record"])
    got = keylink.active_link(log.lines(), [link], [bad])
    assert got["ok"] and got["active"] == cid


def test_malformed_entries_do_not_hide_a_valid_revocation(linked):
    k, log, link = linked
    rv = keylink.make_revocation(link, by="did", key=k, issued=T + 2, nonce="00000000000000bb")
    got = keylink.active_link(log.lines(), [link], MALFORMED + [rv] + MALFORMED)
    assert got["active"] is None and got["reason"] == "revoked"


def test_wrong_shaped_signature_on_right_target_does_not_revoke(linked):
    k, log, link = linked
    cid = keylink.record_cid(link["record"])
    rv = keylink.make_revocation(link, by="did", key=k, issued=T + 2, nonce="00000000000000bc")
    for sig in (None, 5, ["x"], {"a": 1}):
        bad = dict(rv, sig=sig)
        assert keylink.active_link(log.lines(), [link], [bad])["active"] == cid


def test_revocations_none_is_treated_as_empty(linked):
    k, log, link = linked
    assert keylink.active_link(log.lines(), [link], None)["active"] == keylink.record_cid(link["record"])
