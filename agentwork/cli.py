"""agentwork CLI. Nothing here posts to a network unless `post` is explicitly enabled."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from . import codec, didkey, drafts, lineage, post, reputation, render
from .indexer import Index, RoomPolicy, fetch_room
from .outbox import Outbox


class CliError(Exception):
    """Bad user input: main() prints one line to stderr and exits 2 instead of a traceback."""


def _json_obj(text: str | None, flag: str) -> dict:
    try:
        v = json.loads(text if text is not None else "{}")
    except json.JSONDecodeError as e:
        raise CliError(f"{flag} is not valid JSON ({e.msg} at char {e.pos})") from None
    if not isinstance(v, dict):
        raise CliError(f"{flag} must be a JSON object, got {type(v).__name__}")
    return v


def _key(path: str | None, cmd: str) -> didkey.SigningKey:
    if not path:
        raise CliError(f"{cmd} needs --key (a seed file from `agentwork keygen`)")
    try:
        return didkey.load_key(Path(path))
    except FileNotFoundError:
        raise CliError(f"key file not found: {path}") from None
    except ValueError as e:
        raise CliError(f"bad key file {path}: {e}") from None


def _now(a) -> int:
    return int(a.now) if getattr(a, "now", None) else int(time.time() * 1000)


def cmd_keygen(a):
    k = didkey.SigningKey.generate()
    didkey.save_key(k, Path(a.out))
    print(k.did)  # the public DID only; the seed stays in the mode-600 file


def cmd_draft(a):
    key = _key(a.key, "draft")
    args = _json_obj(a.args, "--args")
    try:
        env = drafts.make(f"post_{a.intent}", key, args)
    except codec.AgbError as e:
        raise CliError(str(e)) from None
    p = Outbox(Path(a.outbox)).put(env)
    print(json.dumps({"cid": env["cid"], "outbox_file": str(p), "posted": False}))


def cmd_verify(a):
    lines = []
    for t in a.target:
        src = Path(t)
        lines += src.read_text().splitlines() if src.exists() else [t]
    bad = 0
    for line in (l for l in lines if l.strip()):
        v = codec.verify_line(line)
        print(("OK   " + v.cid) if v.ok else ("FAIL " + v.error))
        bad += not v.ok
    sys.exit(1 if bad else 0)


def _load(a) -> Index:
    idx = Index()
    for p in a.src or []:
        idx.add_file(Path(p))
    for room in a.technocore_room or []:
        idx.add_dump(fetch_room(room, a.limit))
    return idx


def cmd_index(a):
    now = _now(a)
    idx = _load(a)
    reps = idx.apply_policy(RoomPolicy(), now)
    for p in render.write_all(idx, Path(a.out), now, reps, a.tz):
        print(p)
    print(json.dumps(dict(sorted(idx.stats.items()))))


def job_summary(j: dict) -> dict:
    """One job as a flat JSON-safe dict (board text stays data; json.dumps escapes it)."""
    ask, tip = j["ask"], j["tip"]
    b = ask.get("b") or {}
    payees = sorted({a["b"].get("payee") for a in j["accepts"] if a["b"].get("payee")})
    return {"cid": ask["cid"], "status": j["status"], "room": ask.get("room"), "from": ask["from"],
            "ts": ask["ts"], "task": b.get("task"), "budget": b.get("budget"), "due": b.get("due"),
            "offers": len(j["offers"]), "payees": payees, "receipt": tip["cid"] if tip else None,
            "outcome": tip["b"].get("outcome") if tip else None, "attests": len(j["attests"])}


def cmd_jobs(a):
    now = _now(a)
    idx = _load(a)
    reps = idx.apply_policy(RoomPolicy(), now)
    rows = [job_summary(j) for j in idx.jobs(now, reps)]
    if a.status:
        rows = [r for r in rows if r["status"] in a.status]
    if a.did:
        if not a.did.startswith("did:"):
            raise CliError(f"--did must be a DID (did:key:...), got {a.did[:40]!r}")
        rows = [r for r in rows if r["from"] == a.did or a.did in r["payees"]]
    if a.task:
        needle = " ".join(a.task.split()).casefold()
        if not needle:
            raise CliError("--task needs non-blank text")
        rows = [r for r in rows if needle in " ".join(str(r["task"] or "").split()).casefold()]
    if a.room is not None:
        room = a.room.strip().strip("/")
        if not room:
            raise CliError("--room needs a room name, e.g. core/builders")
        rows = [r for r in rows if str(r.get("room") or "").strip("/") == room]
    if a.asset is not None:
        asset = a.asset.strip().casefold()
        if not asset:
            raise CliError("--asset needs an asset symbol, e.g. FLOP")
        rows = [r for r in rows if isinstance(r.get("budget"), dict)
                and str(r["budget"].get("asset") or "").strip().casefold() == asset]
    if a.max_offers is not None:
        if a.max_offers < 0:
            raise CliError(f"--max-offers must be 0 or more, got {a.max_offers}")
        rows = [r for r in rows if r["offers"] <= a.max_offers]
    if a.newest is not None:
        if a.newest < 1:
            raise CliError(f"--newest must be a positive integer, got {a.newest}")
        rows = sorted(rows, key=lambda r: -(r["ts"] or 0))[:a.newest]
    if a.count:
        by = {}
        for r in rows:
            by[r["status"]] = by.get(r["status"], 0) + 1
        print(json.dumps({"total": len(rows), "by_status": dict(sorted(by.items()))}, sort_keys=True))
        return
    if a.md:
        print(jobs_markdown(rows))
        return
    print(json.dumps(rows, indent=1, sort_keys=True))


def _md_cell(v, n: int = 80) -> str:
    """Board text is data: flatten whitespace, escape pipes/backticks/brackets, cap length."""
    t = " ".join(str("" if v is None else v).split())
    t = t[:n] + ("..." if len(t) > n else "")
    return re.sub(r"([|\\\`\[\]<>])", r"\\\1", t)


def _budget(b) -> str:
    if isinstance(b, dict):
        return " ".join(str(b[k]) for k in ("max", "asset") if b.get(k) is not None) or ""
    return "" if b is None else str(b)


def jobs_markdown(rows: list) -> str:
    """Job board as a markdown table (newest first); short cids and DIDs, task text escaped."""
    head = "| status | task | budget | due | offers | from | cid |\n|---|---|---|---|---|---|---|"
    body = [f"| {_md_cell(r['status'])} | {_md_cell(r['task'])} | {_md_cell(_budget(r['budget']), 24)} | {_md_cell(r['due'], 24)} "
            f"| {r['offers']} | {_md_cell(r['from'][-8:])} | {_md_cell(str(r['cid'])[:12])} |"
            for r in sorted(rows, key=lambda r: -(r['ts'] or 0))]
    return "\n".join([head, *body]) if body else "_no jobs match_"


def cmd_rep(a):
    now = _now(a)
    idx = _load(a)
    reps = reputation.compute(idx.msgs.values(), now)
    if a.did:
        print(json.dumps(reputation.get(reps, a.did), indent=1))
    else:
        print(json.dumps([r.to_json() for r in sorted(reps.values(), key=lambda r: -r.score)], indent=1))


def cmd_lineage(a):
    log = lineage.LineageLog(Path(a.log))
    if a.action == "verify":
        rep = lineage.verify_chain(log.lines())
        print(json.dumps(rep.to_json(), indent=1))
        sys.exit(0 if rep.ok else 1)
    key = _key(a.key, f"lineage {a.action}")
    fields = _json_obj(a.fields, "--fields")
    if a.action == "init" and "purpose" not in fields:
        raise CliError('lineage init needs --fields with "purpose", e.g. {"purpose":"…"}')
    try:
        if a.action == "init":
            env = log.incept(key, **fields)
        else:
            env = log.append(key, a.action, **fields)
    except ValueError as e:
        raise CliError(f"{a.action}: {e}") from None
    except TypeError as e:  # unknown field name for incept
        raise CliError(f"{a.action}: bad --fields: {e}") from None
    print(env["cid"])


def cmd_post(a):
    line = Path(a.line_file).read_text().strip()
    key = didkey.load_key(Path(a.key))
    try:
        status = post.post(line, a.tc_room, key, confirm=a.yes)
    except post.PostingDisabled as e:
        print(f"not posted: {e}", file=sys.stderr)
        sys.exit(2)
    print(f"posted, HTTP {status}")


def cmd_mcp(a):
    from . import mcp_server
    mcp_server.main()


def cmd_imd(a):
    from . import imd
    if a.action == "proof" and not a.receipt:
        raise CliError("imd proof needs --receipt <cid>")
    if a.action == "device-did" and not a.device_key:
        raise CliError("imd device-did needs --device-key <64 hex>")
    try:
        _imd(a, imd)
    except imd.ImdError as e:
        raise CliError(f"{a.action}: {e}") from None


def _imd(a, imd):
    if a.action == "observe":
        recs = imd.load_observed(a.src)
        text = imd.observed_md(recs, a.tz)
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
            print(f"wrote {a.out} ({len(recs)} observed IMD job(s); unsigned, weight 0)")
        else:
            print(text)
    elif a.action == "proof":
        idx = Index()
        for p in a.src:
            idx.add_file(Path(p))
        ids = json.loads(Path(a.agent_ids).read_text()) if a.agent_ids else None
        print(json.dumps(imd.receipt_proof(a.receipt, idx.msgs.values(), agent_ids=ids), indent=1, sort_keys=True))
    elif a.action == "device-did":
        print(imd.did_from_device_key(a.device_key))


def cmd_ge(a):
    from . import exchange
    path = Path(a.state)
    ex = exchange.Exchange.load(path, slots=a.slots)
    now = _now(a)
    try:
        if a.action in ("buy", "sell"):
            offer, fills = ex.place(a.agent, a.action, a.category, a.qty, a.price, now)
            ex.save(path)
            out = {"offer": offer.id, "state": offer.state, "filled": offer.filled,
                   "receipts": [t.receipt for t in fills], "slots_used": ex.slots_used(a.agent)}
        elif a.action in ("abort", "collect"):
            r = getattr(ex, a.action)(a.agent, a.offer, now)
            ex.save(path)
            out = r.collect_box() if a.action == "abort" else r
        elif a.action == "book":
            out = ex.book(a.category)
        elif a.action == "guide":
            cats = [a.category] if a.category else ex.categories()
            out = {c: ex.guide_price(c, now) for c in cats}
        elif a.action == "history":
            out = ex.history(a.category)
        elif a.action == "receipts":
            out = ex.receipts()
        else:  # feed
            print("\n".join(exchange.feed(ex)))
            return
    except exchange.ExchangeError as e:
        sys.exit(f"ge: {e}")
    print(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentwork", description="agb1 open work layer for agents")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("keygen", help="create an Ed25519 did:key (seed file mode 600); prints the DID")
    s.add_argument("--out", required=True); s.set_defaults(f=cmd_keygen)
    s = sub.add_parser("draft", help="sign a message into the local outbox (never posts)")
    s.add_argument("intent", choices=[t.removeprefix("post_") for t in drafts.SPECS])
    s.add_argument("--key", required=True); s.add_argument("--outbox", default="outbox")
    s.add_argument("--args", required=True, help='JSON, e.g. {"room":"core/builders","task":"…","budget_asset":"FLOP","budget_max":"1"}')
    s.set_defaults(f=cmd_draft)
    s = sub.add_parser("verify", help="verify agb1 lines (a file or one line)")
    s.add_argument("target", nargs="+"); s.set_defaults(f=cmd_verify)
    helps = {"index": "index + render", "rep": "reputation scores", "jobs": "job board as JSON (status per ask)"}
    for name, fn in (("index", cmd_index), ("rep", cmd_rep), ("jobs", cmd_jobs)):
        s = sub.add_parser(name, help=helps[name])
        s.add_argument("--src", nargs="*", help="local files (agb1 lines, JSONL records, room dumps)")
        s.add_argument("--technocore-room", nargs="*", help="read-only fetch of these Technocore rooms")
        s.add_argument("--limit", type=int, default=200); s.add_argument("--now", type=int)
        s.add_argument("--tz", default="UTC")
        if name == "index":
            s.add_argument("--out", default="site")
        elif name == "jobs":
            s.add_argument("--status", nargs="*", choices=["open", "accepted", "claimed", "expired", "settled",
                                                         "settled+attested", "refunded", "cancelled", "disputed"],
                           help="keep only these statuses")
            s.add_argument("--did", help="keep only jobs this DID posted or was accepted as payee on")
            s.add_argument("--task", help="keep only jobs whose task text contains this (case-insensitive, plain text, not a regex)")
            s.add_argument("--room", help="keep only jobs asked in this room (exact name, e.g. core/builders)")
            s.add_argument("--asset", help="keep only jobs whose budget asset is this (case-insensitive, e.g. FLOP)")
            s.add_argument("--max-offers", type=int,
                           help="keep only jobs with at most N offers (0 = nobody has bid yet)")
            s.add_argument("--newest", type=int, help="keep only the N newest jobs (by ask ts), after the other filters")
            s.add_argument("--md", action="store_true", help="print a markdown table instead of JSON")
            s.add_argument("--count", action="store_true",
                           help='print only {"total", "by_status"} counts (after --status/--did/--task/--newest filters)')
        else:
            s.add_argument("--did")
        s.set_defaults(f=fn)
    s = sub.add_parser("lineage", help="init/append/verify a lineage log")
    s.add_argument("action", choices=["init", "verify", "entry", "checkpoint", "handoff", "rotation", "tombstone"])
    s.add_argument("--log", required=True); s.add_argument("--key"); s.add_argument("--fields", help="JSON body fields")
    s.set_defaults(f=cmd_lineage)
    s = sub.add_parser("post", help=f"publish one draft to Technocore (disabled unless {post.ENV_FLAG}=1 and --yes)")
    s.add_argument("--line-file", required=True); s.add_argument("--tc-room", required=True)
    s.add_argument("--key", required=True); s.add_argument("--yes", action="store_true")
    s.set_defaults(f=cmd_post)
    s = sub.add_parser("mcp", help="run the MCP server on stdio"); s.set_defaults(f=cmd_mcp)
    s = sub.add_parser("imd", help="IMD adapter (read-only): observe saved IMD jobs, export a receipt proof, device key -> did")
    s.add_argument("action", choices=["observe", "proof", "device-did"])
    s.add_argument("--src", nargs="*", default=[], help="saved IMD job JSON (observe) or agb1 sources (proof)")
    s.add_argument("--out"); s.add_argument("--tz", default="UTC")
    s.add_argument("--receipt", help="receipt cid (proof)"); s.add_argument("--agent-ids", help="JSON file did -> agentId (proof)")
    s.add_argument("--device-key", help="64-hex IMD device key (device-did)")
    s.set_defaults(f=cmd_imd)
    s = sub.add_parser("ge", help="Grand Exchange: off-chain order book for agent jobs (testnet play money)")
    s.add_argument("action", choices=["buy", "sell", "abort", "collect", "book", "guide", "history", "receipts", "feed"])
    s.add_argument("--state", default=str(Path.home() / ".agentwork" / "ge.jsonl"), help="event log (JSONL)")
    s.add_argument("--agent"); s.add_argument("--category"); s.add_argument("--offer")
    s.add_argument("--qty", type=int, default=1); s.add_argument("--price", type=int)
    s.add_argument("--slots", type=int, default=8); s.add_argument("--now", type=int)
    s.set_defaults(f=cmd_ge)
    a = ap.parse_args(argv)
    try:
        a.f(a)
    except CliError as e:
        print(f"agentwork {a.cmd}: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
