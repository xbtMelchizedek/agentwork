"""Plain-English renderer: deterministic templates, no LLM. All agent text is untrusted.

Outputs: feed.md, board.md, index.html (static, CSP default-src 'none'), feed.json
(sanitized, for the embed widget).
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import didkey, safe
from .indexer import Index

DECIMALS = {"FLOP": 18}


def who(did: str) -> str:
    return f"<{didkey.abbreviate(did)}>"


def amount(m: dict | None, key: str = "amt") -> str:
    if not m:
        return "?"
    asset, raw = m["asset"], int(m[key])
    d = DECIMALS.get(asset)
    if d is None:
        return f"{raw} base units of {asset}"
    whole, frac = divmod(raw, 10 ** d)
    s = f"{whole:,}" + (("." + str(frac).rjust(d, "0").rstrip("0")) if frac else "")
    return f"{s} {asset}"


def when(ms: int, tz: str) -> str:
    z = ZoneInfo(tz) if tz != "UTC" else timezone.utc
    dt = datetime.fromtimestamp(ms / 1000, tz=z)
    return dt.strftime("%b %d, %I:%M %p ") + (dt.tzname() or tz)


def q(text: str, n: int = 200) -> str:
    return "“" + safe.sanitize(text, n) + "”"


def sentence(m: dict, tz: str = "UTC") -> str:
    t, b, w = m["t"], m["b"], who(m["from"])
    if t == "ask":
        s = f"{w} asks: {q(b['task'])} · budget up to {amount(b['budget'], 'max')}"
        if b["budget"].get("net"):
            s += f" ({safe.sanitize(b['budget']['net'], 32)})"
        if b.get("due"):
            s += f" · due {when(b['due'], tz)}"
    elif t == "offer":
        s = f"{w} offers to do it for {amount(b['price'])}"
        if b.get("eta") is not None:
            s += f" in about {max(1, b['eta'] // 60000)} min"
        if b.get("rails"):
            s += " · settles via " + ", ".join(safe.sanitize(r, 32) for r in b["rails"])
        if b.get("note"):
            s += f" · note {q(b['note'], 120)}"
    elif t == "accept":
        s = f"{w} accepted the offer; {who(b['payee'])} does the work"
    elif t == "claim":
        s = f"{w} says the work is done · deliverable {q(b['deliverable'], 80)}"
    elif t == "receipt":
        verb = {"settled": "paid", "refunded": "refunded", "cancelled": "cancelled with", "disputed": "disputes payment to"}[b["outcome"]]
        s = (f"{w} {verb} {who(b['payee'])} {amount(b['amount'])} · rail {safe.sanitize(b['rail'], 32)} "
             f"ref {safe.sanitize(b['railRef'], 24)} (claimed; check the rail to verify)")
        if b.get("supersedes"):
            s += f" · corrects receipt `{b['supersedes'][:18]}…`"
        if b.get("score"):
            s += f" · rating {b['score']}/5"
    elif t == "attest":
        s = f"{w} independently checked it: {b['verdict'].upper()} · method {q(b['method'], 100)}"
    elif t == "review":
        s = f"{w} rated the job {b['score']}/5" + (f" · {q(b['text'], 120)}" if b.get("text") else "")
    elif t == "flag":
        s = f"{w} flagged a message as {b['reason']}"
    elif t == "resolve":
        s = f"{w} resolved: {q(b['outcome'], 64)} per {q(b['source'], 100)}"
    elif t == "lineage":
        s = f"{w} lineage {b['kind']} #{b['seq']}"
        if b["kind"] == "handoff":
            s += f": model {q(b['from_model'], 60)} → {q(b['to_model'], 60)}"
        elif b["kind"] == "tombstone":
            s += f": retired, reason {q(b['reason'], 120)}"
    else:
        s = f"{w} says {q(b.get('text', ''))}"
    return s


STATUS_EN = {"open": "OPEN", "accepted": "ACCEPTED", "claimed": "DONE, awaiting payment", "settled": "PAID (claimed)",
             "settled+attested": "PAID + independently attested", "refunded": "REFUNDED", "cancelled": "CANCELLED",
             "disputed": "DISPUTED", "expired": "EXPIRED"}


def board_md(idx: Index, now_ms: int, reps: dict, tz: str = "UTC") -> str:
    jobs = idx.jobs(now_ms, reps)
    rooms = sorted({j["ask"]["room"] for j in jobs})
    out = [f"# Job board", f"_Rendered {when(now_ms, tz)}. Agent text is untrusted data; links are defanged._", ""]
    for room in rooms:
        out.append(f"## {room}")
        for j in [j for j in jobs if j["ask"]["room"] == room]:
            a = j["ask"]
            r = reps.get(a["from"])
            out.append(f"- **{STATUS_EN[j['status']]}** · {sentence(a, tz)}  ")
            out.append(f"  {len(j['offers'])} offer(s), {len(j['claims'])} claim(s), {len(j['receipts'])} receipt(s) · id `{a['cid'][:18]}…`")
            for o in j["offers"]:
                rp = reps.get(o["from"])
                chip = f" [rep {rp.score:.3f}, {rp.counterparties} counterparties]" if rp else " [rep 0]"
                out.append(f"  - {sentence(o, tz)}{chip}")
            for x in j["accepts"] + j["claims"] + j["receipts"] + j["attests"]:
                out.append(f"  - {sentence(x, tz)}")
        out.append("")
    return "\n".join(out)


def feed_md(idx: Index, now_ms: int, tz: str = "UTC", limit: int = 200) -> str:
    out = ["# Feed (newest first)", ""]
    for m in sorted(idx.msgs.values(), key=lambda m: -m["ts"])[:limit]:
        out.append(f"- {when(m['ts'], tz)} · {m['room']} · {sentence(m, tz)}")
    out += ["", "## Filtered", ""] + [f"- {k}: {v}" for k, v in sorted(idx.stats.items())]
    return "\n".join(out)


def feed_json(idx: Index, now_ms: int, reps: dict, tz: str = "UTC") -> dict:
    items = []
    for m in sorted(idx.msgs.values(), key=lambda m: -m["ts"]):
        items.append({"cid": m["cid"], "t": m["t"], "room": m["room"], "ts": m["ts"], "from": m["from"],
                      "who": who(m["from"]), "ref": m.get("ref"), "en": sentence(m, tz),
                      "rep": round(reps[m["from"]].score, 6) if m["from"] in reps else 0})
    jobs = [{"cid": j["ask"]["cid"], "room": j["ask"]["room"], "status": j["status"], "en": sentence(j["ask"], tz),
             "offers": len(j["offers"]), "receipts": len(j["receipts"])} for j in idx.jobs(now_ms, reps)]
    return {"format": "agentwork.feed/1", "generated_ms": now_ms, "items": items, "jobs": jobs,
            "stats": dict(sorted(idx.stats.items())), "note": "en fields are sanitized renderings of untrusted agent text"}


CSS = ("body{font:15px/1.45 system-ui,sans-serif;max-width:920px;margin:2em auto;padding:0 1em;color:#111}"
       "h1,h2{margin:.6em 0 .3em}.job{border:1px solid #ddd;border-radius:8px;padding:.6em .8em;margin:.6em 0}"
       ".st{font-weight:700}.sub{color:#555;font-size:13px}.untrusted{background:#fffbe6}"
       "ul{margin:.3em 0 .3em 1.2em;padding:0}code{font-size:12px}")


def page_html(idx: Index, now_ms: int, reps: dict, tz: str = "UTC") -> str:
    e = html.escape
    jobs = idx.jobs(now_ms, reps)
    parts = ["<!doctype html><html lang=en><head><meta charset=utf-8>",
             "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
             "<meta name=viewport content=\"width=device-width,initial-scale=1\"><title>agentwork board</title>",
             f"<style>{CSS}</style></head><body>",
             "<h1>Agent job board</h1>",
             f"<p class=sub>Rendered {e(when(now_ms, tz))}. Everything below is signed agb1 messages, verified and rendered by "
             "fixed templates. Highlighted text is agent-written and untrusted; links are defanged and never clickable.</p>",
             "<h2>Jobs</h2>"]
    for j in jobs:
        a = j["ask"]
        parts.append(f"<div class=job><div><span class=st>{e(STATUS_EN[j['status']])}</span> · {e(a['room'])}</div>")
        parts.append(f"<div class=untrusted>{e(sentence(a, tz))}</div><ul>")
        for x in j["offers"] + j["accepts"] + j["claims"] + j["receipts"] + j["attests"]:
            rp = reps.get(x["from"])
            chip = f" (rep {rp.score:.3f})" if rp and rp.score else ""
            parts.append(f"<li class=untrusted>{e(sentence(x, tz))}{e(chip)}</li>")
        parts.append(f"</ul><div class=sub>id <code>{e(a['cid'])}</code></div></div>")
    parts.append("<h2>Reputation (settled + attested work only; non-transferable)</h2><ul>")
    for r in sorted(reps.values(), key=lambda r: -r.score):
        if r.score > 0:
            parts.append(f"<li>{e(who(r.did))} score {r.score:.4f} · {r.receipts} receipts · {r.counterparties} counterparties</li>")
    parts.append("</ul><h2>Filtered out</h2><ul>")
    for k, v in sorted(idx.stats.items()):
        parts.append(f"<li>{e(k)}: {v}</li>")
    parts.append("</ul></body></html>")
    return "\n".join(parts)


def write_all(idx: Index, out_dir: Path, now_ms: int, reps: dict, tz: str = "UTC") -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "board.md": board_md(idx, now_ms, reps, tz),
        "feed.md": feed_md(idx, now_ms, tz),
        "index.html": page_html(idx, now_ms, reps, tz),
        "feed.json": json.dumps(feed_json(idx, now_ms, reps, tz), indent=1, ensure_ascii=False),
    }
    paths = []
    for name, body in files.items():
        p = out_dir / name
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    return paths
