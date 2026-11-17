"""Indexer: ingest agb1 messages, verify, drop spam/invalid, build threads and job boards.

Sources:
* local files: JSONL of agb1 wire lines, JSONL of transport records
  ({"from","text",...}), or a Technocore room dump ({"room","messages":[...]});
* Technocore read endpoints: GET https://technocore.chat/r/<room>?format=json&limit=N.
  Room names come from the caller (CLI/config), never from message text. Reads only.

All message text is untrusted data: it is validated as a typed envelope and rendered
through `safe.sanitize`; nothing in it can choose a URL, a room to read, or an action.
"""
from __future__ import annotations

import json
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import codec, didkey, reputation, safe

TECHNOCORE = "https://technocore.chat"


@dataclass(frozen=True)
class RoomPolicy:
    """A view filter (moderation hides; it never deletes the signed log)."""
    intents: frozenset = frozenset(codec.INTENTS)
    max_per_min_per_did: int = 6
    template_dids: int = 3          # identical body from >= this many DIDs = template spam
    min_score_to_show: float = 0.0  # reputation gate for asks/offers (0 = open room)
    hide_flagged: bool = True       # hide targets flagged by DIDs with score > 0


@dataclass
class Index:
    msgs: dict = field(default_factory=dict)          # cid -> envelope (shown)
    hidden: dict = field(default_factory=dict)        # cid -> (envelope, reason)
    transport: dict = field(default_factory=dict)     # cid -> {"room","seq","signed"}
    stats: Counter = field(default_factory=Counter)
    rejects: list = field(default_factory=list)       # (reason, sanitized preview)
    seen: set = field(default_factory=set)

    # ── ingest ────────────────────────────────────────────────────────────
    def add_line(self, line: str, transport_from: str | None = None, room: str | None = None, seq=None) -> str | None:
        self.stats["records"] += 1
        if not codec.looks_like_agb1(line):
            self.stats["chatter"] += 1
            return None
        try:
            env = codec.decode(line)
        except codec.AgbError as e:
            self.stats["invalid"] += 1
            self.rejects.append((str(e)[:120], safe.sanitize(line, 120)))
            return None
        if transport_from and didkey.is_did(transport_from) and transport_from != env["from"]:
            self.stats["signer_mismatch"] += 1
            self.rejects.append(("transport signer != envelope from", env["cid"]))
            return None
        if env["cid"] in self.seen:
            self.stats["duplicate"] += 1
            return env["cid"]
        self.seen.add(env["cid"])
        self.msgs[env["cid"]] = env
        self.transport[env["cid"]] = {"room": room, "seq": seq, "signed": bool(transport_from and didkey.is_did(transport_from))}
        self.stats["valid"] += 1
        return env["cid"]

    def add_record(self, rec: dict, room: str | None = None) -> str | None:
        if not isinstance(rec, dict):
            self.stats["records"] += 1
            self.stats["chatter"] += 1
            return None
        return self.add_line(rec.get("text", ""), rec.get("from") if isinstance(rec.get("from"), str) else None,
                             room or rec.get("room"), rec.get("seq"))

    def add_dump(self, dump: dict) -> None:
        room = dump.get("room") if isinstance(dump.get("room"), str) else None
        for rec in dump.get("messages") or []:
            self.add_record(rec, room)

    def add_file(self, path: Path) -> None:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        try:
            whole = json.loads(text)
        except ValueError:
            whole = None
        if isinstance(whole, dict) and isinstance(whole.get("messages"), list):
            self.add_dump(whole)
            return
        if isinstance(whole, list):
            for rec in whole:
                self.add_record(rec)
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith(codec.PREFIX):
                self.add_line(line)
            elif line.startswith("{"):
                try:
                    self.add_record(json.loads(line))
                except ValueError:
                    self.stats["records"] += 1
                    self.stats["chatter"] += 1
            else:
                self.add_line(line)

    # ── policy ────────────────────────────────────────────────────────────
    def apply_policy(self, policy: RoomPolicy, now_ms: int, rep_params: reputation.Params | None = None) -> dict:
        reps = reputation.compute(self.msgs.values(), now_ms, rep_params)
        hide: dict[str, str] = {}
        ordered = sorted(self.msgs.values(), key=lambda m: (m["ts"], m["cid"]))
        # intent allowlist
        for m in ordered:
            if m["t"] not in policy.intents:
                hide[m["cid"]] = "intent not allowed in room"
        # per-DID rate (per room, sliding 60 s)
        recent: dict[tuple, list[int]] = defaultdict(list)
        for m in ordered:
            key = (m["room"], m["from"])
            win = [t for t in recent[key] if m["ts"] - t < 60_000]
            if len(win) >= policy.max_per_min_per_did:
                hide.setdefault(m["cid"], "rate limit")
            win.append(m["ts"])
            recent[key] = win
        # template spam: identical (room, intent, body) from many DIDs
        groups: dict[str, set] = defaultdict(set)
        for m in ordered:
            groups[codec.canonical([m["room"], m["t"], m["b"]])].add(m["from"])
        for m in ordered:
            if m["t"] in ("ask", "offer", "note", "claim") and len(groups[codec.canonical([m["room"], m["t"], m["b"]])]) >= policy.template_dids:
                hide.setdefault(m["cid"], "template spam")
        # reputation gate for asks/offers
        if policy.min_score_to_show > 0:
            for m in ordered:
                if m["t"] in ("ask", "offer") and reps.get(m["from"], reputation.Rep(m["from"])).score < policy.min_score_to_show:
                    hide.setdefault(m["cid"], "below room reputation gate")
        # flags from reputable DIDs
        if policy.hide_flagged:
            for m in ordered:
                if m["t"] == "flag" and m["ref"] in self.msgs and reps.get(m["from"], reputation.Rep("")).score > 0:
                    hide.setdefault(m["ref"], f"flagged {m['b']['reason']} by a reputable DID")
        for cid, why in hide.items():
            self.hidden[cid] = (self.msgs.pop(cid), why)
            self.stats["hidden:" + why.split(" by ")[0]] += 1
        return reps

    # ── threads / job board ───────────────────────────────────────────────
    def children(self) -> dict[str, list[dict]]:
        kids: dict[str, list[dict]] = defaultdict(list)
        for m in self.msgs.values():
            if "ref" in m:
                kids[m["ref"]].append(m)
        for v in kids.values():
            v.sort(key=lambda m: (m["ts"], m["cid"]))
        return kids

    def jobs(self, now_ms: int, reps: dict | None = None) -> list[dict]:
        kids = self.children()
        reps = reps or {}
        jobs = []
        for ask in sorted((m for m in self.msgs.values() if m["t"] == "ask"), key=lambda m: -m["ts"]):
            offers = [o for o in kids[ask["cid"]] if o["t"] == "offer"]
            accepts = [a for o in offers for a in kids[o["cid"]] if a["t"] == "accept" and a["from"] == ask["from"]]
            claims = [c for a in accepts for c in kids[a["cid"]] if c["t"] == "claim" and c["from"] == a["b"]["payee"]]
            receipts = [r for c in claims for r in kids[c["cid"]] if r["t"] == "receipt" and r["from"] == ask["from"]]
            attests = [x for r in receipts for x in kids[r["cid"]] if x["t"] == "attest"]
            if receipts:
                status = receipts[-1]["b"]["outcome"]
                if status == "settled" and any(x["b"]["verdict"] == "valid" and x["from"] not in (ask["from"], receipts[-1]["b"]["payee"]) for x in attests):
                    status = "settled+attested"
            elif claims:
                status = "claimed"
            elif accepts:
                status = "accepted"
            elif ask["b"].get("due") and ask["b"]["due"] < now_ms:
                status = "expired"
            else:
                status = "open"
            jobs.append({"ask": ask, "offers": offers, "accepts": accepts, "claims": claims,
                         "receipts": receipts, "attests": attests, "status": status})
        return jobs


def fetch_room(room: str, limit: int = 200, origin: str = TECHNOCORE, timeout: float = 15.0) -> dict:
    """Read-only GET of one Technocore room as JSON. Redirects are refused."""
    url = safe.assert_read_url(f"{origin}/r/{room}?format=json&limit={int(limit)}")

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "agentwork-indexer/0.1 (read-only)"})
    with opener.open(req, timeout=timeout) as r:
        data = r.read(4_000_000)
    obj = json.loads(data)
    if not isinstance(obj, dict):
        raise ValueError("unexpected response shape")
    obj["room"] = room
    return obj


def build_index(sources: Iterable[Path] = (), dumps: Iterable[dict] = ()) -> Index:
    idx = Index()
    for p in sources:
        idx.add_file(Path(p))
    for d in dumps:
        idx.add_dump(d)
    return idx
