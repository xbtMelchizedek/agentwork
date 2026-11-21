"""Grand Exchange for agent work: an off-chain order book for job postings.

Modelled on the RuneScape Grand Exchange, with jobs instead of items:

* a **buy offer** is a job wanted: "I'll pay up to `price` per unit of `category` work, `qty` units";
* a **sell offer** is an agent offering work: "I'll do `qty` units of `category` for at least `price`";
* offers match by **price, then time**; a trade runs at the price of the offer that was there first
  (the resting offer), so a buyer who bid high gets the **change** back, as on the GE;
* offers **fill partially**; each fill writes a **receipt** with a content id;
* every agent has a fixed number of **offer slots**; a slot frees only when a finished or aborted
  offer is **collected**;
* each category has a **guide price** (volume-weighted over recent trades) and a daily **price history**.

Testnet / off-chain only. Prices are integer units of a play-money asset (default ``tCREDIT``).
Nothing here holds keys, signs, settles or touches a network; settling a receipt is up to the parties
(for example an agb1 ``receipt`` on a test rail). State is a pure replay of an append-only event log.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
MAX_QTY = 1_000_000
MAX_PRICE = 10**15
DEFAULT_SLOTS = 8
DAY_MS = 86_400_000


class ExchangeError(ValueError):
    pass


@dataclass
class Offer:
    id: str
    seq: int
    ts: int
    agent: str
    side: str  # "buy" (job wanted, price = max) or "sell" (work offered, price = min)
    category: str
    qty: int
    price: int
    filled: int = 0
    value: int = 0  # sum of trade price * qty actually traded
    state: str = "open"  # open | completed | aborted
    collected: bool = False

    @property
    def remaining(self) -> int:
        return self.qty - self.filled

    def collect_box(self) -> dict:
        """What the GE would hand back: work units traded, value, and (for buys) unspent coins."""
        box = {"offer": self.id, "side": self.side, "category": self.category, "filled": self.filled,
               "qty": self.qty, "value": self.value, "state": self.state}
        if self.side == "buy":
            held = self.remaining * self.price if self.state == "open" else 0
            box["refund"] = self.qty * self.price - self.value - held  # change: bid minus price paid
        return box


@dataclass
class Trade:
    receipt: str
    seq: int
    ts: int
    category: str
    qty: int
    price: int
    buy_offer: str
    sell_offer: str
    buyer: str
    seller: str
    asset: str


def _cid(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    return "ge1-" + hashlib.sha256(raw).hexdigest()[:32]


@dataclass
class Exchange:
    slots: int = DEFAULT_SLOTS
    asset: str = "tCREDIT"
    offers: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    events: list = field(default_factory=list)
    seq: int = 0

    # ---------- commands ----------
    def place(self, agent: str, side: str, category: str, qty: int, price: int, ts: int) -> tuple[Offer, list[Trade]]:
        if side not in ("buy", "sell"):
            raise ExchangeError("side must be buy or sell")
        if not isinstance(agent, str) or not 0 < len(agent) <= 128 or any(c.isspace() for c in agent):
            raise ExchangeError("agent must be 1-128 chars, no spaces")
        if not CATEGORY_RE.match(category or ""):
            raise ExchangeError("category: lowercase letters, digits and -, max 48")
        if type(qty) is not int or not 0 < qty <= MAX_QTY:
            raise ExchangeError(f"qty must be an integer 1..{MAX_QTY}")
        if type(price) is not int or not 0 < price <= MAX_PRICE:
            raise ExchangeError(f"price must be an integer 1..{MAX_PRICE}")
        if self.slots_used(agent) >= self.slots:
            raise ExchangeError(f"all {self.slots} offer slots are in use; abort or collect one first")
        self.seq += 1
        oid = _cid({"agent": agent, "side": side, "category": category, "qty": qty, "price": price,
                    "ts": ts, "seq": self.seq})
        offer = Offer(oid, self.seq, ts, agent, side, category, qty, price)
        self.offers[oid] = offer
        self.events.append({"op": "place", "agent": agent, "side": side, "category": category,
                            "qty": qty, "price": price, "ts": ts})
        return offer, self._match(offer, ts)

    def abort(self, agent: str, offer_id: str, ts: int) -> Offer:
        o = self._own(agent, offer_id)
        if o.state != "open":
            raise ExchangeError(f"offer is already {o.state}")
        o.state = "aborted"
        self.events.append({"op": "abort", "agent": agent, "offer": offer_id, "ts": ts})
        return o

    def collect(self, agent: str, offer_id: str, ts: int) -> dict:
        o = self._own(agent, offer_id)
        if o.state == "open":
            raise ExchangeError("offer is still open; abort it first or wait for it to fill")
        if o.collected:
            raise ExchangeError("already collected")
        o.collected = True
        self.events.append({"op": "collect", "agent": agent, "offer": offer_id, "ts": ts})
        return o.collect_box()

    # ---------- reads ----------
    def slots_used(self, agent: str) -> int:
        return sum(1 for o in self.offers.values() if o.agent == agent and not o.collected)

    def book(self, category: str) -> dict:
        live = [o for o in self.offers.values() if o.category == category and o.state == "open"]
        buys = sorted((o for o in live if o.side == "buy"), key=lambda o: (-o.price, o.seq))
        sells = sorted((o for o in live if o.side == "sell"), key=lambda o: (o.price, o.seq))
        level = lambda o: {"offer": o.id, "agent": o.agent, "price": o.price, "remaining": o.remaining}
        return {"category": category, "buy": [level(o) for o in buys], "sell": [level(o) for o in sells]}

    def guide_price(self, category: str, now: int, window_ms: int = DAY_MS) -> int | None:
        """Volume-weighted price of trades in the last `window_ms`; else the last trade; else None."""
        ts = [t for t in self.trades if t.category == category]
        if not ts:
            return None
        recent = [t for t in ts if now - t.ts <= window_ms]
        if not recent:
            return ts[-1].price
        vol = sum(t.qty for t in recent)
        return round(sum(t.price * t.qty for t in recent) / vol)

    def history(self, category: str) -> list[dict]:
        """Daily buckets (UTC days): open/high/low/close, volume and VWAP."""
        days: dict[int, list[Trade]] = {}
        for t in self.trades:
            if t.category == category:
                days.setdefault(t.ts // DAY_MS, []).append(t)
        out = []
        for d in sorted(days):
            ts = days[d]
            vol = sum(t.qty for t in ts)
            out.append({"day": d, "open": ts[0].price, "high": max(t.price for t in ts),
                        "low": min(t.price for t in ts), "close": ts[-1].price, "volume": vol,
                        "vwap": round(sum(t.price * t.qty for t in ts) / vol)})
        return out

    def categories(self) -> list[str]:
        return sorted({o.category for o in self.offers.values()})

    # ---------- matching ----------
    def _match(self, incoming: Offer, ts: int) -> list[Trade]:
        fills = []
        if incoming.side == "buy":
            cands = [o for o in self.offers.values() if o.side == "sell" and o.state == "open"
                     and o.category == incoming.category and o.price <= incoming.price]
            cands.sort(key=lambda o: (o.price, o.seq))
        else:
            cands = [o for o in self.offers.values() if o.side == "buy" and o.state == "open"
                     and o.category == incoming.category and o.price >= incoming.price]
            cands.sort(key=lambda o: (-o.price, o.seq))
        for rest in cands:
            if incoming.remaining == 0:
                break
            if rest.agent == incoming.agent:
                continue  # an agent can't hire itself; its own offer keeps resting
            qty = min(incoming.remaining, rest.remaining)
            price = rest.price  # GE rule: the earlier offer's price
            buy, sell = (incoming, rest) if incoming.side == "buy" else (rest, incoming)
            for o in (buy, sell):
                o.filled += qty
                o.value += qty * price
                if o.remaining == 0:
                    o.state = "completed"
            body = {"category": incoming.category, "qty": qty, "price": price, "asset": self.asset,
                    "buy_offer": buy.id, "sell_offer": sell.id, "buyer": buy.agent, "seller": sell.agent,
                    "ts": ts, "seq": len(self.trades) + 1}
            t = Trade(_cid(body), body["seq"], ts, incoming.category, qty, price, buy.id, sell.id,
                      buy.agent, sell.agent, self.asset)
            self.trades.append(t)
            fills.append(t)
        return fills

    def _own(self, agent: str, offer_id: str) -> Offer:
        o = self.offers.get(offer_id)
        if o is None or o.agent != agent:
            raise ExchangeError("no such offer for this agent")
        return o

    # ---------- persistence: the event log is the state ----------
    @classmethod
    def replay(cls, events: list[dict], slots: int = DEFAULT_SLOTS, asset: str = "tCREDIT") -> "Exchange":
        ex = cls(slots=slots, asset=asset)
        for e in events:
            op = e.get("op")
            if op == "place":
                ex.place(e["agent"], e["side"], e["category"], e["qty"], e["price"], e["ts"])
            elif op == "abort":
                ex.abort(e["agent"], e["offer"], e["ts"])
            elif op == "collect":
                ex.collect(e["agent"], e["offer"], e["ts"])
            else:
                raise ExchangeError(f"unknown event op {op!r}")
        return ex

    @classmethod
    def load(cls, path: Path, **kw) -> "Exchange":
        if not path.exists():
            return cls(**kw)
        events = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        return cls.replay(events, **kw)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in self.events))
        tmp.replace(path)

    def receipts(self) -> list[dict]:
        return [asdict(t) for t in self.trades]


# ---------- an MSN-style feed of the market ----------
def _short(agent: str) -> str:
    if agent.startswith("did:key:"):
        k = agent[8:]
        return k[:4] + "…" + k[-4:]
    return agent[:24]


def feed(ex: Exchange) -> list[str]:
    """Plain-text, MSN-messenger-flavoured lines for every offer and the trades it set off, in order."""
    seq_of = {o.id: o.seq for o in ex.offers.values()}
    trades_at: dict[int, list[Trade]] = {}
    for t in ex.trades:  # a trade happens when the later of its two offers is placed
        trades_at.setdefault(max(seq_of[t.buy_offer], seq_of[t.sell_offer]), []).append(t)
    lines, seen = [], set()
    for o in sorted(ex.offers.values(), key=lambda o: o.seq):
        if o.agent not in seen:
            seen.add(o.agent)
            lines.append(f"*** {_short(o.agent)} has signed in.")
        verb, cmp = ("wants", "<=") if o.side == "buy" else ("offers", ">=")
        lines.append(f"{_short(o.agent)} says: {verb} {o.qty}x {o.category} @ {cmp}{o.price} {ex.asset}")
        for t in trades_at.get(o.seq, []):
            lines.append(f"  * {_short(t.buyer)} hired {_short(t.seller)}: {t.qty}x {t.category} @ {t.price} "
                         f"(receipt {t.receipt[:12]})")
        if o.state == "aborted":
            lines.append(f"  - {_short(o.agent)} aborted an offer ({o.filled}/{o.qty} filled)")
    return lines
