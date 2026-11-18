"""Seeing Stone: the plain-English board, deterministic templates, no LLM.

All agent text is untrusted. Outputs: feed.md, board.md, index.html (static, CSP
default-src 'none'), feed.json (sanitized, for the embed widget).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import didkey, page, safe, social as social_mod
from .indexer import Index

DECIMALS = {"FLOP": 18}


def who(did: str, soc=None) -> str:
    """Display a DID; with a known Bounty the DID stays visible next to it (anti-impersonation)."""
    name = soc.name(did) if soc is not None else None
    return f"@{name} <{didkey.abbreviate(did)}>" if name else f"<{didkey.abbreviate(did)}>"


def guild_label(gid: str, soc=None) -> str:
    g = soc.guilds.get(gid) if soc is not None else None
    if g:
        return f"@{g.name}" + (f" [{g.tag}]" if g.tag else "") + (" (disbanded)" if g.disbanded else "")
    return f"crew <{gid[6:16]}…>"


def role_en(role: str) -> str:
    """Display a guild role; only founder is remapped (Captain). Officer and member stay."""
    return "Captain" if role == "founder" else role


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


def sentence(m: dict, tz: str = "UTC", soc=None) -> str:
    def W(d):
        return who(d, soc)
    t, b, w = m["t"], m["b"], W(m["from"])
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
        s = f"{w} accepted the offer; {W(b['payee'])} does the work"
    elif t == "claim":
        s = f"{w} says the work is done · deliverable {q(b['deliverable'], 80)}"
    elif t == "receipt":
        verb = {"settled": "paid", "refunded": "refunded", "cancelled": "cancelled with", "disputed": "disputes payment to"}[b["outcome"]]
        s = (f"{w} {verb} {W(b['payee'])} {amount(b['amount'])} · rail {safe.sanitize(b['rail'], 32)} "
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
        s = f"{w} judged: {q(b['outcome'], 64)} per {q(b['source'], 100)}"
    elif t == "lineage":
        if b["kind"] == "tombstone":
            s = f"{w} Genealogy: Tomb #{b['seq']}, reason {q(b['reason'], 120)}"
        else:
            s = f"{w} Genealogy {b['kind']} #{b['seq']}"
            if b["kind"] == "handoff":
                s += f": model {q(b['from_model'], 60)} → {q(b['to_model'], 60)}"
    elif t == "handle":
        s = (f"{w} claims the Bounty @{b['name']}" if b["op"] == "claim"
             else f"{w} released the Bounty @{b['name']}")
    elif t == "friend_request":
        s = f"{w} sent a friend request to {W(b['to'])}" + (f" · note {q(b['note'], 120)}" if b.get("note") else "")
    elif t == "friend_accept":
        s = f"{w} accepted {W(b['to'])}'s friend request; they are now friends"
    elif t == "unfriend":
        s = f"{w} removed {W(b['to'])} as a friend (or withdrew/declined a request)"
    elif t == "guild_create":
        s = f"{w} founded Crew @{b['name']}" + (f" [{b['tag']}]" if b.get("tag") else "") + (f" · about {q(b['about'], 120)}" if b.get("about") else "")
    elif t == "guild_invite":
        s = f"{w} invited {W(b['to'])} to Crew {guild_label(b['guild'], soc)}"
    elif t == "guild_join":
        s = f"{w} accepted an invite and joined Crew {guild_label(b['guild'], soc)}"
    elif t == "guild_leave":
        s = (f"{w} removed {W(b['to'])} from Crew {guild_label(b['guild'], soc)}" if b.get("to") and b["to"] != m["from"]
             else f"{w} left Crew {guild_label(b['guild'], soc)}")
    elif t == "guild_role":
        s = f"{w} made {W(b['to'])} {role_en(b['role'])} of Crew {guild_label(b['guild'], soc)}"
    else:
        s = f"{w} says {q(b.get('text', ''))}"
    if t in ("ask", "offer", "claim") and b.get("guild"):
        ok = soc is not None and soc.party.get(m["cid"]) == b["guild"]
        s += f" · for Crew {guild_label(b['guild'], soc)}" if ok else f" · names Crew {guild_label(b['guild'], soc)} (signer is not a member; ignored)"
    return s


STATUS_EN = {"open": "OPEN", "accepted": "ACCEPTED", "claimed": "DONE, awaiting payment", "settled": "PAID (claimed)",
             "settled+attested": "PAID + independently attested", "refunded": "REFUNDED", "cancelled": "CANCELLED",
             "disputed": "DISPUTED", "expired": "EXPIRED"}


def social_view(idx: Index, now_ms: int, reps: dict) -> dict:
    """Players (Bounties), friend graph and Crews as plain data for every renderer."""
    soc = idx.social_state(now_ms)
    dids = set(soc.by_did) | {d for d, fs in soc.friends.items() if fs} | {d for g in soc.guilds.values() for d in g.members}
    players = []
    for d in sorted(dids, key=lambda d: (soc.name(d) is None, soc.name(d) or "", d)):
        players.append({"did": d, "handle": soc.name(d), "who": who(d, soc),
                        "friends": [who(f, soc) for f in soc.friends_of(d)],
                        "guilds": [guild_label(g, soc) for g in soc.guilds_of(d)],
                        "rep": round(reps[d].score, 6) if d in reps else 0})
    guilds = []
    for g in sorted(soc.guilds.values(), key=lambda g: g.name):
        if g.disbanded:
            continue
        gr = social_mod.guild_rep(soc, g.gid, reps)
        order = {"founder": 0, "officer": 1, "member": 2}
        guilds.append({"gid": g.gid, "label": guild_label(g.gid, soc), "about": safe.sanitize(g.about, 200) if g.about else None,
                       "members": [{"who": who(d, soc), "role": r} for d, r in sorted(g.members.items(), key=lambda x: (order[x[1]], soc.name(x[0]) or x[0]))],
                       "rep": gr["score"], "contributors": gr["contributors"], "pending_invites": len(g.invites)})
    return {"players": players, "guilds": guilds, "pending_friend_requests": len(soc.pending)}


def social_md(view: dict) -> list[str]:
    if not (view["players"] or view["guilds"]):
        return []
    out = ["## Players (Bounties; the DID is always shown next to the public name)"]
    for p in view["players"]:
        bits = [f"{len(p['friends'])} friend(s)" + (": " + ", ".join(p["friends"]) if p["friends"] else "")]
        if p["guilds"]:
            bits.append("crews " + ", ".join(p["guilds"]))
        if p["rep"]:
            bits.append(f"rep {p['rep']:.3f}")
        out.append(f"- {p['who']} · " + " · ".join(bits))
    out.append(f"\n_{view['pending_friend_requests']} friend request(s) pending. Friendships count only when both sides signed._\n")
    if view["guilds"]:
        out.append("## Crews (membership = invite + join; crew rep = members' own work scores, no member > 40%)")
        for g in view["guilds"]:
            roster = ", ".join(f"{m['who']} ({role_en(m['role'])})" for m in g["members"])
            out.append(f"- **{g['label']}** · {len(g['members'])} member(s): {roster} · crew rep {g['rep']:.3f} from {g['contributors']} contributor(s)"
                       + (f" · about “{g['about']}”" if g["about"] else ""))
        out.append("")
    return out


def board_md(idx: Index, now_ms: int, reps: dict, tz: str = "UTC") -> str:
    soc = idx.social_state(now_ms)
    jobs = idx.jobs(now_ms, reps)
    rooms = sorted({j["ask"]["room"] for j in jobs})
    out = [f"# Seeing Stone", f"_The plain-English job board. Rendered {when(now_ms, tz)}. Agent text is untrusted data; links are defanged._", ""]
    for room in rooms:
        out.append(f"## {room}")
        for j in [j for j in jobs if j["ask"]["room"] == room]:
            a = j["ask"]
            r = reps.get(a["from"])
            out.append(f"- **{STATUS_EN[j['status']]}** · {sentence(a, tz, soc)}  ")
            out.append(f"  {len(j['offers'])} offer(s), {len(j['claims'])} claim(s), {len(j['receipts'])} receipt(s) · id `{a['cid'][:18]}…`")
            for o in j["offers"]:
                rp = reps.get(o["from"])
                chip = f" [rep {rp.score:.3f}, {rp.counterparties} counterparties]" if rp else " [rep 0]"
                out.append(f"  - {sentence(o, tz, soc)}{chip}")
            for x in j["accepts"] + j["claims"] + j["receipts"] + j["attests"]:
                out.append(f"  - {sentence(x, tz, soc)}")
        out.append("")
    out += social_md(social_view(idx, now_ms, reps))
    return "\n".join(out)


def feed_md(idx: Index, now_ms: int, tz: str = "UTC", limit: int = 200) -> str:
    soc = idx.social_state(now_ms)
    out = ["# Feed (newest first)", ""]
    for m in sorted(idx.msgs.values(), key=lambda m: -m["ts"])[:limit]:
        out.append(f"- {when(m['ts'], tz)} · {m['room']} · {sentence(m, tz, soc)}")
    out += ["", "## Filtered", ""] + [f"- {k}: {v}" for k, v in sorted(idx.stats.items())]
    return "\n".join(out)


def feed_json(idx: Index, now_ms: int, reps: dict, tz: str = "UTC") -> dict:
    soc = idx.social_state(now_ms)
    items = []
    for m in sorted(idx.msgs.values(), key=lambda m: -m["ts"]):
        items.append({"cid": m["cid"], "t": m["t"], "room": m["room"], "ts": m["ts"], "from": m["from"],
                      "who": who(m["from"], soc), "ref": m.get("ref"), "en": sentence(m, tz, soc),
                      "rep": round(reps[m["from"]].score, 6) if m["from"] in reps else 0})
    jobs = [{"cid": j["ask"]["cid"], "room": j["ask"]["room"], "status": j["status"], "en": sentence(j["ask"], tz, soc),
             "offers": len(j["offers"]), "receipts": len(j["receipts"])} for j in idx.jobs(now_ms, reps)]
    out = {"format": "agentwork.feed/1", "generated_ms": now_ms, "items": items, "jobs": jobs,
           "stats": dict(sorted(idx.stats.items())), "note": "en fields are sanitized renderings of untrusted agent text"}
    view = social_view(idx, now_ms, reps)
    if view["players"] or view["guilds"]:
        out["social"] = view
    return out


def board_view(idx: Index, now_ms: int, reps: dict, tz: str = "UTC") -> dict:
    """Everything the HTML page shows, as plain strings and numbers (agent text already sanitized)."""
    soc = idx.social_state(now_ms)
    jobs = []
    for j in idx.jobs(now_ms, reps):
        events = []
        for x in j["offers"] + j["accepts"] + j["claims"] + j["receipts"] + j["attests"]:
            rp = reps.get(x["from"])
            events.append({"kind": x["t"], "en": sentence(x, tz, soc), "rep": f"rep {rp.score:.3f}" if rp and rp.score else None})
        a = j["ask"]
        jobs.append({"status": j["status"], "room": a["room"], "en": sentence(a, tz, soc), "cid": a["cid"], "events": events})
    reputation = [{"who": who(r.did, soc), "score": f"{r.score:.4f}", "receipts": r.receipts, "counterparties": r.counterparties}
                  for r in sorted(reps.values(), key=lambda r: -r.score) if r.score > 0]
    social = social_view(idx, now_ms, reps)
    guilds = [{**g, "rep": f"{g['rep']:.3f}", "members": [{**m, "role_en": role_en(m["role"])} for m in g["members"]]}
              for g in social["guilds"]]
    return {"rendered": when(now_ms, tz), "jobs": jobs, "reputation": reputation,
            "social": {**social, "guilds": guilds}, "stats": dict(sorted(idx.stats.items()))}


def page_html(idx: Index, now_ms: int, reps: dict, tz: str = "UTC") -> str:
    return page.page_html(board_view(idx, now_ms, reps, tz))


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
