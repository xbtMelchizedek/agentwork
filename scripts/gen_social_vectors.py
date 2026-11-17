#!/usr/bin/env python3
"""Regenerate vectors/social-v1.json (SPEC §10) from PUBLIC deterministic test keys.

Contents: valid signed social messages, lines that must be rejected, confusable skeletons,
guild-reputation cap cases, and an ordered scenario with the expected folded state.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agentwork import codec, reserved as reserved_mod, social  # noqa: E402
from agentwork.testkeys import test_key  # noqa: E402

TS = 1795000000000
MIN, DAY = 60_000, 86_400_000
K = {n: test_key(n) for n in ("alice", "bob", "carol", "dave", "erin", "mallory")}
_n = [0]


def nonce():
    _n[0] += 1
    return f"{0x5000 + _n[0]:016x}"


def env(label, t, b, dt=0, **kw):
    return codec.build(K[label], t, "core/social", b, ts=TS + dt, n=nonce(), **kw)


def vec(name, label, e):
    core = {k: v for k, v in e.items() if k not in ("cid", "sig")}
    return {"name": name, "key_label": label, "did": K[label].did, "core": core,
            "canonical_core": codec.canonical(core), "cid": e["cid"], "sig": e["sig"], "wire": codec.encode(e)}


D = {n: k.did for n, k in K.items()}

# ── scenario (indexer order = list order) ──
steps: list[tuple[str, str, dict]] = []      # (step name, label, envelope)


def step(name, label, t, b, dt, **kw):
    e = env(label, t, b, dt, **kw)
    steps.append((name, label, e))
    return e


step("alice claims alice", "alice", "handle", {"op": "claim", "name": "alice"}, 0)
step("mallory lookalike a1ice", "mallory", "handle", {"op": "claim", "name": "a1ice"}, 1 * MIN)
step("mallory reserved admin", "mallory", "handle", {"op": "claim", "name": "adm1n"}, 2 * MIN)
step("bob claims bob_the_builder", "bob", "handle", {"op": "claim", "name": "bob_the_builder"}, 3 * MIN)
r1 = step("alice requests bob", "alice", "friend_request", {"to": D["bob"], "note": "worked together on PR 16"}, 4 * MIN)
step("bob accepts alice", "bob", "friend_accept", {"to": D["alice"]}, 5 * MIN, ref=r1["cid"])
step("carol requests alice", "carol", "friend_request", {"to": D["alice"]}, 6 * MIN)
step("alice requests carol (crossed)", "alice", "friend_request", {"to": D["carol"]}, 7 * MIN)
step("mallory forged accept", "mallory", "friend_accept", {"to": D["alice"]}, 8 * MIN, ref=r1["cid"])
g = step("alice founds fliptide", "alice", "guild_create", {"name": "fliptide", "tag": "FLIP", "about": "testnet work crew"}, 10 * MIN)
gid = social.gid_of(g["cid"])
step("mallory lookalike guild", "mallory", "guild_create", {"name": "f1iptide"}, 11 * MIN)
ib = step("alice invites bob", "alice", "guild_invite", {"guild": gid, "to": D["bob"]}, 12 * MIN)
step("bob joins", "bob", "guild_join", {"guild": gid}, 13 * MIN, ref=ib["cid"])
step("carol joins uninvited", "carol", "guild_join", {"guild": gid}, 14 * MIN, ref=ib["cid"])
ic = step("alice invites carol", "alice", "guild_invite", {"guild": gid, "to": D["carol"]}, 15 * MIN)
step("carol joins", "carol", "guild_join", {"guild": gid}, 16 * MIN, ref=ic["cid"])
step("alice makes bob officer", "alice", "guild_role", {"guild": gid, "to": D["bob"], "role": "officer"}, 17 * MIN)
idv = step("bob invites dave", "bob", "guild_invite", {"guild": gid, "to": D["dave"]}, 18 * MIN)
step("dave joins", "dave", "guild_join", {"guild": gid}, 19 * MIN, ref=idv["cid"])
step("carol (member) invites erin", "carol", "guild_invite", {"guild": gid, "to": D["erin"]}, 20 * MIN)
step("bob asks for the guild", "bob", "ask", {"task": "Index 3 rooms, CSV out", "budget": {"asset": "FLOP", "max": "1000"}, "guild": gid}, 21 * MIN)
step("mallory names the guild", "mallory", "ask", {"task": "Pay me first", "budget": {"asset": "FLOP", "max": "1"}, "guild": gid}, 22 * MIN)
step("bob unfriends alice", "bob", "unfriend", {"to": D["alice"]}, 23 * MIN)
step("alice re-requests bob too soon", "alice", "friend_request", {"to": D["bob"]}, 24 * MIN)
step("alice founder leaves with members", "alice", "guild_leave", {"guild": gid}, 25 * MIN)
step("alice renames to alicia", "alice", "handle", {"op": "claim", "name": "alicia"}, 2 * DAY)
step("mallory snipes released alice", "mallory", "handle", {"op": "claim", "name": "alice"}, 2 * DAY + MIN)
step("bob removes dave", "bob", "guild_leave", {"guild": gid, "to": D["dave"], "reason": "inactive"}, 2 * DAY + 2 * MIN)

pos = {e["cid"]: i for i, (_, _, e) in enumerate(steps)}
soc = social.compute([e for _, _, e in steps], TS + 3 * DAY, order_key=lambda m: pos[m["cid"]])
gg = soc.guilds[gid]
expected = {
    "handles": {n: h["did"] for n, h in sorted(soc.handles.items())},
    "friends": sorted(sorted([a, b]) for a, fs in soc.friends.items() for b in fs if a < b),
    "pending": sorted([a, b] for (a, b) in soc.pending),
    "guild": {"gid": gid, "name": gg.name, "members": dict(sorted(gg.members.items()))},
    "party": soc.party,
    "ignored": {name: soc.ignored[e["cid"]] for name, _, e in steps if e["cid"] in soc.ignored},
}
scenario = {"now_ms": TS + 3 * DAY, "order": "list order is indexer order",
            "steps": [{"name": n, "key_label": lab, "wire": codec.encode(e)} for n, lab, e in steps], "expected": expected}

valid = [vec(n, lab, e) for n, lab, e in steps if e["t"] in codec.SOCIAL_INTENTS][:12]
w = valid[0]["wire"]


def mutate(f, base=None):
    e = json.loads((base or w)[5:]); f(e); return "agb1 " + codec.canonical(e)


def forged(label, t, b, ref=None):
    """Correctly signed envelope with an invalid body (built by hand, bypassing build())."""
    e = {"t": t, "s": f"agb.{t}/1", "from": D[label], "ts": TS, "n": nonce(), "room": "core/social", "b": b}
    if ref:
        e["ref"] = ref
    e["cid"] = codec.compute_cid(e)
    e["sig"] = K[label].sign(codec.SIG_DOMAIN + e["cid"].encode("ascii"))
    return "agb1 " + codec.canonical(e)


invalid = [
    {"name": "handle-non-ascii-cyrillic-a", "line": forged("mallory", "handle", {"op": "claim", "name": "\u0430lice"})},
    {"name": "handle-uppercase", "line": forged("mallory", "handle", {"op": "claim", "name": "Alice"})},
    {"name": "handle-too-short", "line": forged("mallory", "handle", {"op": "claim", "name": "al"})},
    {"name": "handle-too-long", "line": forged("mallory", "handle", {"op": "claim", "name": "a" * 25})},
    {"name": "handle-double-separator", "line": forged("mallory", "handle", {"op": "claim", "name": "al__ice"})},
    {"name": "handle-leading-digit", "line": forged("mallory", "handle", {"op": "claim", "name": "0xalice"})},
    {"name": "handle-trailing-separator", "line": forged("mallory", "handle", {"op": "claim", "name": "alice-"})},
    {"name": "friend-request-self", "line": forged("alice", "friend_request", {"to": D["alice"]})},
    {"name": "friend-accept-missing-ref", "line": forged("bob", "friend_accept", {"to": D["alice"]})},
    {"name": "guild-join-missing-ref", "line": forged("bob", "guild_join", {"guild": gid})},
    {"name": "guild-bad-id", "line": forged("bob", "guild_invite", {"guild": "guild:abc", "to": D["carol"]})},
    {"name": "guild-bad-role", "line": forged("alice", "guild_role", {"guild": gid, "to": D["bob"], "role": "admin"})},
    {"name": "guild-tag-lowercase", "line": forged("alice", "guild_create", {"name": "crew", "tag": "flip"})},
    {"name": "handle-tampered", "line": mutate(lambda e: e["b"].update(name="alicf"))},
    {"name": "dm-not-in-v0", "line": forged("alice", "dm", {"to": D["bob"], "box": "AAAA"})},
]
for v in invalid:
    try:
        codec.decode(v["line"])
        raise SystemExit(f"vector {v['name']} unexpectedly valid")
    except codec.AgbError:
        pass

skeletons = [[n, codec.handle_skeleton(n)] for n in
             ("alice", "a1ice", "al1ce", "a-l-i-c-e", "bob_the_builder", "b0b_the_bui1der", "modern", "modem", "vvave", "wave", "s4tosh1", "satoshl")]
RESERVED_CASES = ("admin", "adm1n", "a-d-m-i-n", "fl0p", "technocore", "moderator", "mod3rat0r",
                  "god", "g0d", "gawd", "jesus", "j3sus", "jesus777", "realjesus", "jesusofficial", "the-lord", "christ",
                  "chr1st", "yahweh", "jehovah", "allah", "a11ah", "holyspirit", "holy-spirit", "messiah", "m3ss1ah", "lord",
                  "god_sucks", "godsucks", "g0d-sux", "jesus-is-fake", "fuckgod", "goddamn", "antichrist", "lordkiller",
                  "bobshit", "coon", "rapist",
                  # allowed: our own claim and ordinary words that merely contain a listed string
                  "melchizedek", "alice", "a1ice", "godfrey", "christina", "christian", "goodgame", "warlord", "lord_of_lag",
                  "jesusislord", "therapist", "torpedo", "grape", "pomegranate", "nigeria", "classy", "assassin",
                  "dickens", "cocktail", "raccoon", "hello", "modern", "imdworker", "bob_the_builder")
reserved = [[n, reserved_mod.reason(n)] for n in RESERVED_CASES]
guild_rep = [[s, round(social.guild_reputation(s), 9)] for s in ([10], [10, 10], [1, 1, 1], [10, 1, 1], [10, 10, 1], [5, 4, 3, 2], [3, 0, 0, 2, 2], [])]

out = {"spec": "agb1 social", "version": 1, "section": "SPEC.md §10",
       "keys": "seed = sha256(utf8('agentwork public test key: ' + key_label)); PUBLIC TEST KEYS",
       "valid": valid, "invalid": invalid, "skeletons": skeletons, "reserved": reserved,
       "guild_rep_cap": 0.4, "guild_rep": guild_rep, "scenario": scenario}
(ROOT / "vectors" / "social-v1.json").write_text(json.dumps(out, indent=1, ensure_ascii=True) + "\n")
print("wrote", len(valid), "valid,", len(invalid), "invalid,", len(steps), "scenario steps")
print(json.dumps(expected["ignored"], indent=1))
print(expected["guild"]["members"].values(), expected["handles"].keys(), len(expected["friends"]), expected["party"])
