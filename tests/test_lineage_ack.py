"""Issue 13 (rest): successor countersign (`ack`) on the first entry after a handoff."""
import json
import subprocess
import sys

import pytest

from agentwork import codec, lineage
from agentwork.didkey import save_key
from conftest import ROOT

T = 1794000000000


def base(tmp_path, keys):
    log = lineage.LineageLog(tmp_path / "lin.jsonl")
    log.incept(keys["a"], "ack demo", next_key=keys["b"].did, ts=T)
    log.append(keys["a"], "entry", what="commit", ref="c0", ts=T + 1)
    log.append(keys["a"], "handoff", from_model="m1", to_model="m2", summary="s", ts=T + 2)
    return log


def test_ack_on_next_entry_countersigns(tmp_path, keys):
    log = base(tmp_path, keys)
    hcid = json.loads(log.lines()[-1][5:])["cid"]
    assert lineage.verify_chain(log.lines()).awaiting_ack == hcid
    env = log.append(keys["a"], "entry", what="note", ref="m2 online", ack=True, ts=T + 3)
    assert env["b"]["ack"] == hcid
    rep = lineage.verify_chain(log.lines())
    assert rep.ok and rep.handoffs[0]["acked_by"] == 3 and rep.unacked_handoffs == [] and rep.awaiting_ack is None


def test_ack_by_successor_key_after_rotation(tmp_path, keys):
    log = base(tmp_path, keys)
    log.append(keys["a"], "rotation", new_key=keys["b"].did, ts=T + 3)
    log.append(keys["b"], "entry", what="note", ref="b online", ack=True, ts=T + 4)
    rep = lineage.verify_chain(log.lines())
    assert rep.ok and rep.handoffs[0]["acked_by"] == 4 and not rep.unacked_handoffs


def test_unacked_handoffs_reported_not_fatal(tmp_path, keys):
    log = base(tmp_path, keys)
    rep = lineage.verify_chain(log.lines())             # pending at head
    assert rep.ok and [h["seq"] for h in rep.unacked_handoffs] == [2]
    log.append(keys["a"], "entry", what="note", ref="no ack", ts=T + 3)
    log.append(keys["a"], "handoff", from_model="m2", to_model="m3", summary="s", ts=T + 4)
    log.append(keys["a"], "entry", what="note", ref="m3", ack=True, ts=T + 5)
    rep = lineage.verify_chain(log.lines())
    assert rep.ok and [h["seq"] for h in rep.unacked_handoffs] == [2] and rep.handoffs[1]["acked_by"] == 5


@pytest.mark.parametrize("case", ["late", "wrong-cid", "no-handoff", "twice"])
def test_misplaced_or_wrong_ack_fails(tmp_path, keys, case):
    log = base(tmp_path, keys)
    hcid = json.loads(log.lines()[-1][5:])["cid"]
    other = json.loads(log.lines()[1][5:])["cid"]
    if case == "late":
        log.append(keys["a"], "entry", what="note", ref="x", ts=T + 3)
        log.append(keys["a"], "entry", what="note", ref="y", ack=hcid, ts=T + 4)
    elif case == "wrong-cid":
        log.append(keys["a"], "entry", what="note", ref="x", ack=other, ts=T + 3)
    elif case == "no-handoff":
        log = lineage.LineageLog(tmp_path / "l2.jsonl")
        log.incept(keys["a"], "x", ts=T)
        log.append(keys["a"], "entry", what="note", ref="x", ack=other, ts=T + 1)
    elif case == "twice":
        log.append(keys["a"], "entry", what="note", ref="x", ack=hcid, ts=T + 3)
        log.append(keys["a"], "entry", what="note", ref="y", ack=hcid, ts=T + 4)
    rep = lineage.verify_chain(log.lines())
    assert not rep.ok and "ack" in " ".join(rep.errors)


def test_writer_refuses_ack_true_without_handoff(tmp_path, keys):
    log = lineage.LineageLog(tmp_path / "l.jsonl")
    log.incept(keys["a"], "x", ts=T)
    with pytest.raises(ValueError):
        log.append(keys["a"], "entry", what="note", ref="x", ack=True)


def test_schema_ack_must_be_cid_and_not_on_inception(keys):
    with pytest.raises(codec.AgbError):
        codec.build(keys["a"], "lineage", "lineage", {"kind": "inception", "seq": 0, "purpose": "p", "ack": "0x" + "ab" * 32})
    with pytest.raises(codec.AgbError):
        codec.build(keys["a"], "lineage", "lineage/lin:0x" + "00" * 32,
                    {"kind": "entry", "seq": 1, "lin": "lin:0x" + "00" * 32, "prev": "0x" + "11" * 32,
                     "ack": "nope", "what": "note", "ref": "x"})


def test_ack_edit_detected_by_tamper_rules(tmp_path, keys):
    log = base(tmp_path, keys)
    log.append(keys["a"], "entry", what="note", ref="x", ack=True, ts=T + 3)
    log.append(keys["a"], "entry", what="note", ref="y", ts=T + 4)
    lines = log.lines()
    env = json.loads(lines[3][5:]); del env["b"]["ack"]          # strip the countersign, re-sign it
    lines[3] = codec.encode(codec.build(keys["a"], "lineage", env["room"], env["b"], ts=env["ts"]))
    assert not lineage.verify_chain(lines).ok                     # breaks entry 4's prev


def test_cli_ack_and_verify(tmp_path, keys):
    log = base(tmp_path, keys)
    kf = tmp_path / "k.seed"; save_key(keys["a"], kf)
    run = lambda *a: subprocess.run([sys.executable, "-m", "agentwork", *a], cwd=ROOT, capture_output=True, text=True)
    r = run("lineage", "entry", "--log", str(log.path), "--key", str(kf), "--fields", '{"what":"note","ref":"up","ack":true}')
    assert r.returncode == 0, r.stderr
    out = json.loads(run("lineage", "verify", "--log", str(log.path)).stdout)
    assert out["ok"] and out["unacked_handoffs"] == [] and out["handoffs"][0]["acked_by"] == 3


def test_render_shows_ack(tmp_path, keys):
    from agentwork import render
    log = base(tmp_path, keys)
    hcid = json.loads(log.lines()[-1][5:])["cid"]
    log.append(keys["a"], "entry", what="note", ref="m2 online", ack=True, ts=T + 3)
    acked = json.loads(log.lines()[-1][5:])
    assert f"countersigns handoff `{hcid[:18]}" in render.sentence(acked, "UTC")
    handoff = json.loads(log.lines()[-2][5:])
    assert "countersigns" not in render.sentence(handoff, "UTC")


def test_export_carries_ack_proof(tmp_path, keys):
    import copy
    log = base(tmp_path, keys)
    log.append(keys["a"], "entry", what="note", ref="m2 online", ack=True, ts=T + 3)
    log.append(keys["a"], "checkpoint", ts=T + 4)
    lines = log.lines()
    b = lineage.export_checkpoint(lines, 4, leaf=2)          # leaf 2 = handoff, acked by 3
    assert b["ack"]["seq"] == 3 and lineage.verify_export(b)
    assert "ack" not in lineage.export_checkpoint(lines, 4, leaf=1)
    t = copy.deepcopy(b); t["ack"]["line"] = lines[1]       # not an ack of this handoff
    assert not lineage.verify_export(t)
    t = copy.deepcopy(b); t["ack"]["proof"] = b["leaf"]["proof"]
    assert not lineage.verify_export(t)


def test_export_unacked_handoff_has_no_ack(tmp_path, keys):
    log = base(tmp_path, keys)
    log.append(keys["a"], "checkpoint", ts=T + 3)            # checkpoint ends the ack window
    b = lineage.export_checkpoint(log.lines(), 3, leaf=2)
    assert "ack" not in b and lineage.verify_export(b)
