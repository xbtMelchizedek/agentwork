import json
import subprocess
import sys

from agentwork import codec, reputation
from agentwork.indexer import Index
from conftest import ROOT

VEC = ROOT / "vectors" / "supersede-v1.json"


def test_vectors_are_current():
    gen = subprocess.run([sys.executable, "-c",
                          "import sys; sys.path.insert(0, 'scripts'); import gen_supersede_vectors as g; sys.stdout.write(g.render())"],
                         cwd=ROOT, capture_output=True, text=True)
    assert gen.returncode == 0, gen.stderr
    assert gen.stdout == VEC.read_text(), "run scripts/gen_supersede_vectors.py"


def test_vectors_replay_from_wire_lines():
    doc = json.loads(VEC.read_text())
    assert len(doc["scenarios"]) >= 7
    for sc in doc["scenarios"]:
        msgs = [codec.decode(line) for line in sc["lines"]]
        idx = Index()
        for line in sc["lines"]:
            idx.add_line(line)
        job = idx.jobs(sc["eval_ts"])[0]
        assert (job["tip"]["cid"] if job["tip"] else None) == sc["expect"]["tip"], sc["name"]
        assert job["status"] == sc["expect"]["status"], sc["name"]
        worker = job["claims"][0]["from"]
        reps = reputation.compute(msgs, sc["eval_ts"], reputation.Params(window_ms=10**12))
        counted = reps.get(worker, reputation.Rep(worker)).receipts == 1
        assert counted == sc["expect"]["worker_counted"], sc["name"]
