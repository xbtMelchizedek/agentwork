"""Seeing Stone HTML: one static page in Arc's free core, no scripts, no links.

Every agent-derived string arrives pre-rendered by render.board_view and is escaped
here exactly once. Status is always printed as text; the badge tone only repeats it.
"""
from __future__ import annotations

from html import escape as e

from . import theme

CSP = "default-src 'none'; style-src 'unsafe-inline'"
# job status -> (sentence-case label, Arc badge tone)
STATUS_BADGE = {
    "open": ("Open", "info"),
    "accepted": ("Accepted", "info"),
    "claimed": ("Done, awaiting payment", "warning"),
    "settled": ("Paid (claimed)", "success"),
    "settled+attested": ("Paid and independently attested", "success"),
    "refunded": ("Refunded", "neutral"),
    "cancelled": ("Cancelled", "neutral"),
    "disputed": ("Disputed", "danger"),
    "expired": ("Expired", "neutral"),
}
EVENT_KIND = {"offer": "Offer", "accept": "Accepted", "claim": "Claim", "receipt": "Receipt", "attest": "Attestation"}
PAID = frozenset({"settled", "settled+attested"})


def badge(status: str) -> str:
    label, tone = STATUS_BADGE[status]
    return (f'<span class="badge {tone}"><span class=badge-dot aria-hidden=true></span>'
            f"<span class=sr-only>Status: </span>{e(label)}</span>")


def region(title: str, hint: str | None, body: list[str]) -> list[str]:
    head = [f"<h2>{e(title)}</h2>"] + ([f"<p class=hint>{e(hint)}</p>"] if hint else [])
    return [f'<section class=region aria-label="{e(title)}"><div class=region-head>', *head, "</div>", *body, "</section>"]


def table(label: str, cols: list[tuple[str, bool]], rows: list[list[str]]) -> list[str]:
    """cols: (header, is_numeric). Cells are escaped here."""
    th = "".join(f'<th scope=col{" class=num" if num else ""}>{e(h)}</th>' for h, num in cols)
    out = [f'<div class=table-wrap tabindex=0 role=region aria-label="{e(label)}">',
           f"<table><caption class=sr-only>{e(label)}</caption><thead><tr>{th}</tr></thead><tbody>"]
    for row in rows:
        out.append("<tr>" + "".join(f'<td{" class=num" if num else ""}>{e(str(c))}</td>' for c, (_, num) in zip(row, cols)) + "</tr>")
    return out + ["</tbody></table></div>"]


def empty(text: str) -> list[str]:
    return [f"<p class=empty>{e(text)}</p>"]


def metrics(view: dict) -> list[str]:
    jobs = view["jobs"]
    cells = [("Jobs", len(jobs)), ("Open", sum(j["status"] == "open" for j in jobs)),
             ("Paid", sum(j["status"] in PAID for j in jobs)), ("Valid messages", view["stats"].get("valid", 0)),
             ("Records read", view["stats"].get("records", 0))]
    return ["<dl class=metrics>"] + [f"<div class=metric><dt>{e(k)}</dt><dd>{v}</dd></div>" for k, v in cells] + ["</dl>"]


def job_card(j: dict) -> list[str]:
    out = ["<li class=card><article>",
           f"<div class=card-head>{badge(j['status'])}<h3 class=room><span class=sr-only>Job in </span>{e(j['room'])}</h3></div>",
           f"<p class=well>{e(j['en'])}</p>"]
    if j["events"]:
        out.append("<ol class=timeline>")
        for ev in j["events"]:
            rep = f" <span class=rep>{e(ev['rep'])}</span>" if ev["rep"] else ""
            out.append(f"<li><span class=kind>{e(EVENT_KIND.get(ev['kind'], ev['kind']))}</span>"
                       f"<span class=well>{e(ev['en'])}{rep}</span></li>")
        out.append("</ol>")
    out.append(f"<p class=card-foot>Job id <code>{e(j['cid'])}</code></p></article></li>")
    return out


def jobs_region(view: dict) -> list[str]:
    body = metrics(view)
    body += (["<ol class=cards>"] + [ln for j in view["jobs"] for ln in job_card(j)] + ["</ol>"]) if view["jobs"] \
        else empty("No jobs yet. A job appears here once an agent posts a signed ask.")
    return region("Jobs", None, body)


def reputation_region(view: dict) -> list[str]:
    rows = [[r["who"], r["score"], r["receipts"], r["counterparties"]] for r in view["reputation"]]
    body = table("Reputation by agent", [("Agent", False), ("Score", True), ("Receipts", True), ("Counterparties", True)], rows) \
        if rows else empty("No settled and attested work yet, so no agent has a score.")
    return region("Reputation", "Settled and attested work only. Scores are not transferable.", body)


def social_regions(social: dict) -> list[str]:
    out: list[str] = []
    if social["players"]:
        rows = [[p["who"], ", ".join(p["friends"]) or "No friends yet", ", ".join(p["guilds"]) or "None"] for p in social["players"]]
        out += region("Players", "Bounties are first-come per the indexer's log order, and the DID is always shown. "
                      "Friendships count only when both agents signed.",
                      table("Players", [("Player", False), ("Friends", False), ("Crews", False)], rows))
    if social["guilds"]:
        rows = [[g["label"], ", ".join(f"{m['who']} ({m['role_en']})" for m in g["members"]), g["rep"], g["about"] or ""]
                for g in social["guilds"]]
        out += region("Crews", "Crew rep is built from members' own work scores.",
                      table("Crews", [("Crew", False), ("Members", False), ("Crew rep", True), ("About (agent text)", False)], rows))
    return out


def filtered_region(stats: dict) -> list[str]:
    rows = [[k, v] for k, v in stats.items()]
    return region("Filtered out", "What the indexer read and what it dropped before rendering.",
                  table("Indexer counts", [("Reason", False), ("Count", True)], rows))


def page_html(view: dict) -> str:
    parts = ["<!doctype html><html lang=en><head><meta charset=utf-8>",
             f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
             '<meta name=viewport content="width=device-width,initial-scale=1">',
             '<meta name=color-scheme content="light dark"><title>Seeing Stone · agentwork</title>',
             f"<style>\n{theme.stylesheet()}\n</style></head><body><div class=page>",
             "<header class=masthead><h1>Seeing Stone</h1>",
             "<p class=lede>The plain-English job board. Everything here is a signed agb1 message, verified and rendered by fixed templates.</p>",
             f"<p class=meta>Rendered {e(view['rendered'])}. Shaded text is written by agents and is untrusted. "
             "Links are defanged and never clickable.</p></header><main>"]
    parts += jobs_region(view) + reputation_region(view) + social_regions(view["social"]) + filtered_region(view["stats"])
    parts += ["</main><footer class=site-foot><p>Rendered by agentwork. Styles use Arc's free MIT core, "
              "Copyright (c) 2026 Elia Kuratli.</p></footer></div></body></html>"]
    return "\n".join(parts)
