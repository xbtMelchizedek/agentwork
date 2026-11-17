import json
import subprocess
import sys

import pytest

from agentwork import codec, lineage
from agentwork.didkey import SigningKey, save_key
from conftest import ROOT

T = 1794000000000


def make(tmp_path, keys, tomb=True):
    log = lineage.LineageLog(tmp_path / "lin.jsonl")
    k1, k2 = keys["a"], keys["b"]
    log.incept(k1, "demo agent", name="demo", model="model-1", next_key=k2.did, ts=T)
    log.append(k1, "entry", what="commit", ref="abc123", ts=T + 1)
    log.append(k1, "checkpoint", ts=T + 2)
    log.append(k1, "handoff", from_model="model-1", to_model="model-2", summary="open threads: none", ts=T + 3)
    log.append(k1, "rotation", new_key=k2.did, ts=T + 4)
    log.append(k2, "entry", what="note", ref="continuing", ts=T + 5)
    if tomb:
        log.append(k2, "tombstone", reason="retired", epitaph="built the demo", ts=T + 6)
    return log


def test_chain_verifies(tmp_path, keys):
    log = make(tmp_path, keys)
    rep = lineage.verify_chain(log.lines())
    assert rep.ok and rep.entries == 7 and rep.tombstoned and rep.current_key == keys["b"].did
    assert rep.lin.startswith("lin:0x") and rep.handoffs[0]["to_model"] == "model-2"


def test_append_after_tombstone_refused(tmp_path, keys):
    log = make(tmp_path, keys)
    with pytest.raises(ValueError):
        log.append(keys["b"], "entry", what="note", ref="x")


def mutated(lines, i, f):
    env = json.loads(lines[i][5:]); f(env)
    return lines[:i] + ["agb1 " + codec.canonical(env)] + lines[i + 1:]


@pytest.mark.parametrize("case", ["delete", "reorder", "edit", "resign-edit", "old-key-after-rotation", "extra-after-tomb"])
def test_tamper_detected(tmp_path, keys, case):
    lines = make(tmp_path, keys).lines()
    if case == "delete":
        lines = lines[:2] + lines[3:]
    elif case == "reorder":
        lines[1], lines[2] = lines[2], lines[1]
    elif case == "edit":
        lines = mutated(lines, 1, lambda e: e["b"].update(ref="other"))
    elif case == "resign-edit":
        env = json.loads(lines[1][5:]); env["b"]["ref"] = "other"
        e2 = codec.build(keys["a"], "lineage", env["room"], env["b"], ts=env["ts"])
        lines[1] = codec.encode(e2)  # validly signed, but breaks the next entry's prev
    elif case == "old-key-after-rotation":
        st = lineage.verify_chain(lines[:6])
        prev = json.loads(lines[5][5:])["cid"]
        lines = lines[:6] + [codec.encode(codec.build(keys["a"], "lineage", "lineage/" + st.lin,
                                                      {"kind": "entry", "seq": 6, "lin": st.lin, "prev": prev, "what": "note", "ref": "x"}, ts=T + 9))]
    elif case == "extra-after-tomb":
        st = lineage.verify_chain(lines)
        lines.append(codec.encode(codec.build(keys["b"], "lineage", "lineage/" + st.lin,
                                              {"kind": "entry", "seq": 7, "lin": st.lin, "prev": st.head, "what": "note", "ref": "x"}, ts=T + 9)))
    assert not lineage.verify_chain(lines).ok


def test_rotation_must_match_commitment(tmp_path, keys):
    log = lineage.LineageLog(tmp_path / "l.jsonl")
    log.incept(keys["a"], "x", next_key=keys["b"].did, ts=T)
    log.append(keys["a"], "rotation", new_key=keys["c"].did, ts=T + 1)  # writer doesn't check; verifier does
    assert "committed" in " ".join(lineage.verify_chain(log.lines()).errors)


def test_cli_verify(tmp_path, keys):
    log = make(tmp_path, keys, tomb=False)
    kf = tmp_path / "k.seed"
    save_key(keys["b"], kf)
    assert oct(kf.stat().st_mode & 0o777) == "0o600"
    run = lambda *a: subprocess.run([sys.executable, "-m", "agentwork", *a], cwd=ROOT, capture_output=True, text=True)
    r = run("lineage", "verify", "--log", str(log.path))
    assert r.returncode == 0 and json.loads(r.stdout)["ok"]
    r = run("lineage", "tombstone", "--log", str(log.path), "--key", str(kf), "--fields", '{"reason":"done"}')
    assert r.returncode == 0, r.stderr
    assert lineage.verify_chain(log.lines()).tombstoned
    log.path.write_text("\n".join(log.lines()[:-2] + log.lines()[-1:]) + "\n")
    assert run("lineage", "verify", "--log", str(log.path)).returncode == 1
