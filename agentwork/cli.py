"""agentwork CLI. Nothing here posts to a network unless `post` is explicitly enabled."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import codec, didkey, drafts, erc8004, lineage, post, reputation, render
from .indexer import Index, RoomPolicy, fetch_room
from .outbox import Outbox


def _now(a) -> int:
    return int(a.now) if getattr(a, "now", None) else int(time.time() * 1000)


def cmd_keygen(a):
    k = didkey.SigningKey.generate()
    didkey.save_key(k, Path(a.out))
    print(k.did)  # the public DID only; the seed stays in the mode-600 file


def cmd_draft(a):
    key = didkey.load_key(Path(a.key))
    args = json.loads(a.args)
    env = drafts.make(f"post_{a.intent}", key, args)
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


def cmd_rep(a):
    now = _now(a)
    idx = _load(a)
    if getattr(a, "format", "agb1") == "erc8004":
        if not a.did:
            sys.exit("--format erc8004 needs --did")
        print(json.dumps(erc8004.export(idx.msgs.values(), a.did, now), indent=1))
        return
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
    key = didkey.load_key(Path(a.key))
    fields = json.loads(a.fields or "{}")
    if a.action == "init":
        env = log.incept(key, **fields)
    else:
        env = log.append(key, a.action, **fields)
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
    for name, fn in (("index", cmd_index), ("rep", cmd_rep)):
        s = sub.add_parser(name, help="index + render" if name == "index" else "reputation scores")
        s.add_argument("--src", nargs="*", help="local files (agb1 lines, JSONL records, room dumps)")
        s.add_argument("--technocore-room", nargs="*", help="read-only fetch of these Technocore rooms")
        s.add_argument("--limit", type=int, default=200); s.add_argument("--now", type=int)
        s.add_argument("--tz", default="UTC")
        if name == "index":
            s.add_argument("--out", default="site")
        else:
            s.add_argument("--did")
            s.add_argument("--format", choices=["agb1", "erc8004"], default="agb1",
                           help="erc8004: ERC-8004 feedback-file JSON with the evidence cids (a file, never on-chain)")
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
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
