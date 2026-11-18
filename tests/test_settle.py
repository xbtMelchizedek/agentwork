import json

import pytest

from agentwork import codec, reputation, settle
from agentwork.didkey import SigningKey

from test_reputation import NOW, World, score

F = 10**18


def ledger(*rows, rail="flop-htlc"):
    return settle.PaperRail([dict(zip(("railRef", "payee", "asset", "amt"), r)) for r in rows], rail=rail)


def test_check_paid_unpaid_wrong_amount_wrong_payee():
    a, b = SigningKey.generate(), SigningKey.generate()
    v = ledger(("tx-1", a.did, "FLOP", "1000"), rail="paper")
    amt = {"asset": "FLOP", "amt": "1000"}
    assert v.check("paper", "tx-1", a.did, amt) is True
    assert v.check("paper", "tx-2", a.did, amt) is False                 # unpaid
    assert v.check("paper", "tx-1", a.did, {"asset": "FLOP", "amt": "999"}) is False
    assert v.check("paper", "tx-1", a.did, {"asset": "ETH", "amt": "1000"}) is False
    assert v.check("paper", "tx-1", b.did, amt) is False                 # wrong payee
    assert v.check("flop-htlc", "tx-1", a.did, amt) is None              # rail not handled


def test_ledger_rejects_floats_and_bad_amounts(tmp_path):
    p = tmp_path / "l.json"
    p.write_text('{"settlements":[{"railRef":"x","payee":"d","asset":"FLOP","amt":1.5}]}')
    with pytest.raises(ValueError):
        settle.PaperRail.load(p)
    for bad in ("01", "-1", "1e3", "", 5):
        with pytest.raises(ValueError):
            settle.PaperRail([{"railRef": "x", "payee": "d", "asset": "FLOP", "amt": bad}])


def test_duplicate_ledger_ref_proves_nothing():
    a = SigningKey.generate()
    v = ledger(("tx-1", a.did, "FLOP", "1"), ("tx-1", a.did, "FLOP", "1"))
    assert v.check("flop-htlc", "tx-1", a.did, {"asset": "FLOP", "amt": "1"}) is False


def test_reputation_counts_only_verified_receipts():
    w = World()
    payer, paid, unpaid, att = (SigningKey.generate() for _ in range(4))
    w.job(payer, paid, attesters=[att], rail_ref="tx-paid")
    w.job(payer, unpaid, attesters=[att], rail_ref="tx-fake")
    v = ledger(("tx-paid", paid.did, "FLOP", str(F)), ("tx-fake", paid.did, "FLOP", str(F)))
    vs = settle.verified_settlements(w.msgs, v)
    assert vs == frozenset({("flop-htlc", "tx-paid")})  # tx-fake names the wrong payee
    reps = w.scores(verified_settlements=vs)
    assert score(reps, paid) > 0 and score(reps, unpaid) == 0


def test_one_settlement_cannot_back_two_receipts():
    w = World()
    p1, p2, worker, att = (SigningKey.generate() for _ in range(4))
    w.job(p1, worker, attesters=[att], rail_ref="tx-1")
    w.job(p2, worker, attesters=[att], rail_ref="tx-1")  # second payer reuses the same settlement
    v = ledger(("tx-1", worker.did, "FLOP", str(F)))
    assert settle.verified_settlements(w.msgs, v) == frozenset()


def test_unknown_rail_is_not_verified():
    w = World()
    payer, worker, att = (SigningKey.generate() for _ in range(3))
    w.job(payer, worker, attesters=[att], rail_ref="tx-1")
    v = ledger(("tx-1", worker.did, "FLOP", str(F)), rail="paper")  # receipts use flop-htlc
    assert settle.verified_settlements(w.msgs, v) == frozenset()


def test_cli_rep_settlements_flag(tmp_path, capsys):
    from agentwork import cli
    w = World()
    payer, worker, att = (SigningKey.generate() for _ in range(3))
    w.job(payer, worker, attesters=[att], rail_ref="tx-1")
    src = tmp_path / "log.jsonl"
    src.write_text("\n".join(codec.encode(m) for m in w.msgs) + "\n")
    led = tmp_path / "ledger.json"
    led.write_text(json.dumps({"rail": "flop-htlc", "settlements": []}))
    cli.main(["rep", "--src", str(src), "--now", str(NOW), "--settlements", str(led), "--did", worker.did])
    assert json.loads(capsys.readouterr().out)["score"] == 0
    led.write_text(json.dumps({"rail": "flop-htlc", "settlements": [
        {"railRef": "tx-1", "payee": worker.did, "asset": "FLOP", "amt": str(F)}]}))
    cli.main(["rep", "--src", str(src), "--now", str(NOW), "--settlements", str(led), "--did", worker.did])
    assert json.loads(capsys.readouterr().out)["score"] > 0
