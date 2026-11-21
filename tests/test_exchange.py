import json

import pytest

from agentwork import cli
from agentwork.exchange import DAY_MS, Exchange, ExchangeError, feed

T0 = 1_763_700_000_000


def test_price_time_priority_and_resting_price():
    ex = Exchange()
    s1, _ = ex.place("alice", "sell", "code-review", 1, 120, T0)
    s2, _ = ex.place("bob", "sell", "code-review", 1, 100, T0 + 1)
    s3, _ = ex.place("carol", "sell", "code-review", 1, 100, T0 + 2)
    b, fills = ex.place("dave", "buy", "code-review", 2, 150, T0 + 3)
    # cheapest first, then oldest at the same price; trade runs at the resting (earlier) price
    assert [(t.seller, t.price) for t in fills] == [("bob", 100), ("carol", 100)]
    assert b.state == "completed" and s1.state == "open"
    box = ex.collect("dave", b.id, T0 + 4)
    assert box["value"] == 200 and box["refund"] == 2 * 150 - 200  # GE change


def test_partial_fill_then_abort_refunds_rest():
    ex = Exchange()
    b, _ = ex.place("dave", "buy", "summarize", 5, 40, T0)
    s, fills = ex.place("alice", "sell", "summarize", 2, 30, T0 + 1)
    assert fills[0].price == 40 and fills[0].qty == 2  # the buy was there first
    assert s.state == "completed" and b.filled == 2 and b.state == "open"
    assert b.collect_box()["refund"] == 0
    ex.abort("dave", b.id, T0 + 2)
    box = ex.collect("dave", b.id, T0 + 3)
    assert box["refund"] == 3 * 40 and box["state"] == "aborted"


def test_no_cross_no_trade_and_book_order():
    ex = Exchange()
    ex.place("a", "buy", "scrape", 1, 10, T0)
    ex.place("b", "buy", "scrape", 1, 12, T0 + 1)
    ex.place("c", "sell", "scrape", 1, 20, T0 + 2)
    book = ex.book("scrape")
    assert [l["price"] for l in book["buy"]] == [12, 10] and [l["price"] for l in book["sell"]] == [20]
    assert ex.trades == []


def test_self_trade_skipped():
    ex = Exchange()
    ex.place("alice", "sell", "qa", 1, 10, T0)
    ex.place("bob", "sell", "qa", 1, 11, T0 + 1)
    _, fills = ex.place("alice", "buy", "qa", 1, 50, T0 + 2)
    assert [t.seller for t in fills] == ["bob"]


def test_slots_free_only_on_collect():
    ex = Exchange(slots=2)
    o1, _ = ex.place("a", "buy", "x", 1, 5, T0)
    ex.place("a", "buy", "x", 1, 5, T0 + 1)
    with pytest.raises(ExchangeError, match="slots"):
        ex.place("a", "buy", "x", 1, 5, T0 + 2)
    ex.abort("a", o1.id, T0 + 3)
    with pytest.raises(ExchangeError, match="slots"):
        ex.place("a", "buy", "x", 1, 5, T0 + 4)
    ex.collect("a", o1.id, T0 + 5)
    ex.place("a", "buy", "x", 1, 5, T0 + 6)


def test_guide_price_and_history():
    ex = Exchange()
    ex.place("s", "sell", "translate", 3, 100, T0)
    ex.place("b", "buy", "translate", 3, 100, T0 + 1)
    ex.place("s", "sell", "translate", 1, 200, T0 + DAY_MS)
    ex.place("b", "buy", "translate", 1, 200, T0 + DAY_MS + 1)
    assert ex.guide_price("translate", T0 + DAY_MS + 1) == round((300 + 200) / 4)
    assert ex.guide_price("translate", T0 + 5 * DAY_MS) == 200  # stale: last trade
    assert ex.guide_price("nope", T0) is None
    h = ex.history("translate")
    assert [d["volume"] for d in h] == [3, 1] and h[1]["close"] == 200


def test_validation_and_ownership():
    ex = Exchange()
    for bad in [dict(side="hold"), dict(category="Bad Cat"), dict(qty=0), dict(price=-1), dict(qty=1.5), dict(agent="a b")]:
        kw = dict(agent="a", side="buy", category="ok", qty=1, price=1, ts=T0) | bad
        with pytest.raises(ExchangeError):
            ex.place(**kw)
    o, _ = ex.place("a", "buy", "ok", 1, 1, T0)
    with pytest.raises(ExchangeError):
        ex.abort("mallory", o.id, T0)
    with pytest.raises(ExchangeError, match="still open"):
        ex.collect("a", o.id, T0)


def test_replay_is_deterministic(tmp_path):
    ex = Exchange()
    ex.place("s", "sell", "qa", 4, 7, T0)
    b, _ = ex.place("b", "buy", "qa", 2, 9, T0 + 1)
    ex.collect("b", b.id, T0 + 2)
    p = tmp_path / "ge.jsonl"
    ex.save(p)
    again = Exchange.load(p)
    assert again.receipts() == ex.receipts() and again.book("qa") == ex.book("qa")
    assert len({t["receipt"] for t in ex.receipts()}) == len(ex.receipts())


def test_feed_lines():
    ex = Exchange()
    ex.place("did:key:z6MkAAAAqqqqBBBB", "sell", "qa", 1, 7, T0)
    ex.place("buyer", "buy", "qa", 1, 9, T0 + 1)
    lines = feed(ex)
    assert lines[0] == "*** z6Mk…BBBB has signed in."
    assert any(l.startswith("  * buyer hired z6Mk…BBBB: 1x qa @ 7") for l in lines)


def test_cli_roundtrip(tmp_path, capsys):
    st = str(tmp_path / "ge.jsonl")
    cli.main(["ge", "sell", "--state", st, "--agent", "w1", "--category", "qa", "--qty", "2", "--price", "5", "--now", str(T0)])
    cli.main(["ge", "buy", "--state", st, "--agent", "j1", "--category", "qa", "--qty", "1", "--price", "8", "--now", str(T0 + 1)])
    capsys.readouterr()
    cli.main(["ge", "guide", "--state", st, "--now", str(T0 + 2)])
    assert json.loads(capsys.readouterr().out) == {"qa": 5}
