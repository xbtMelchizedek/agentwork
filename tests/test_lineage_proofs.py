"""Issue 13 (part): checkpoint export + Merkle inclusion proofs."""
import copy
import json
import subprocess
import sys

import pytest

from agentwork import lineage
from conftest import ROOT

T = 1794000000000


def build(tmp_path, keys, n_entries=6):
    log = lineage.LineageLog(tmp_path / "lin.jsonl")
    k = keys["a"]
    log.incept(k, "proof demo", name="demo", ts=T)
    for i in range(n_entries):
        log.append(k, "entry", what="commit", ref=f"c{i}", ts=T + 1 + i)
    log.append(k, "checkpoint", ts=T + 100)   # covers 0..n_entries (odd/even padding exercised)
    return log


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7])
def test_every_leaf_proves_for_any_size(n):
    cids = ["0x" + f"{i:064x}" for i in range(1, n + 1)]
    root = lineage.merkle_root(cids)
    for i, c in enumerate(cids):
        assert lineage.verify_proof(c, lineage.merkle_proof(cids, i), root)


def test_wrong_leaf_wrong_root_and_garbage_fail():
    cids = ["0x" + f"{i:064x}" for i in range(1, 6)]
    root = lineage.merkle_root(cids)
    pr = lineage.merkle_proof(cids, 2)
    assert not lineage.verify_proof(cids[3], pr, root)
    assert not lineage.verify_proof(cids[2], pr, "0x" + "00" * 32)
    bad = copy.deepcopy(pr); bad[0]["side"] = "L" if bad[0]["side"] == "R" else "R"
    assert not lineage.verify_proof(cids[2], bad, root)
    for junk in ([{"side": "X", "hash": pr[0]["hash"]}], [{"hash": "zz"}], "nope", None):
        assert lineage.verify_proof(cids[2], junk, root) is False
    assert lineage.verify_proof("0x12", pr, root) is False
    with pytest.raises(ValueError):
        lineage.merkle_proof(cids, 5)


def test_export_bundle_verifies_and_detects_tamper(tmp_path, keys):
    log = build(tmp_path, keys)
    lines = log.lines()
    cp = len(lines) - 1
    b = lineage.export_checkpoint(lines, cp, leaf=3)
    assert b["range"] == [0, cp - 1] and lineage.verify_export(b)
    assert lineage.verify_export(lineage.export_checkpoint(lines, cp))       # no leaf
    assert lineage.verify_export(lineage.export_checkpoint(lines, cp, leaf=0))  # inception leaf
    t = copy.deepcopy(b); t["leaf"]["line"] = lines[4]                         # wrong leaf for proof
    assert not lineage.verify_export(t)
    t = copy.deepcopy(b); t["root"] = "0x" + "11" * 32
    assert not lineage.verify_export(t)
    t = copy.deepcopy(b); t["checkpoint_line"] = t["checkpoint_line"].replace(b["root"][2:6], "ffff", 1)
    assert not lineage.verify_export(t)


def test_export_refuses_bad_requests(tmp_path, keys):
    lines = build(tmp_path, keys).lines()
    with pytest.raises(ValueError):
        lineage.export_checkpoint(lines, 2)                 # not a checkpoint
    with pytest.raises(ValueError):
        lineage.export_checkpoint(lines, len(lines) - 1, leaf=len(lines) - 1)  # outside range
    with pytest.raises(ValueError):
        lineage.export_checkpoint(lines[:3] + lines[4:], len(lines) - 2)       # broken chain


def test_cli_export(tmp_path, keys):
    log = build(tmp_path, keys, n_entries=2)
    cp = len(log.lines()) - 1
    r = subprocess.run([sys.executable, "-m", "agentwork", "lineage", "export", "--log", str(log.path),
                        "--checkpoint", str(cp), "--leaf", "1"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert lineage.verify_export(json.loads(r.stdout))
