"""Social layer v0 (SPEC §10): gamer tags, friends, guilds, reserved names, rate limits."""
import json

import pytest

from agentwork import codec, render, reputation, reserved, social
from agentwork.indexer import Index, RoomPolicy
from agentwork.testkeys import test_key as tk
from conftest import ROOT

V = json.loads((ROOT / "vectors" / "social-v1.json").read_text())
T = 1795000000000
MIN, DAY = 60_000, 86_400_000
OUR_DID = "did:key:z6MkovTaZTor5cT3QbCigBiRCnAc12f93htf9BN99uxwJTt2"


class Log:
    """Build an ordered log; list order is indexer order."""
    def __init__(self):
        self.msgs, self.n = [], 0

    def add(self, key, t, b, dt=0, ts=None, **kw):
        self.n += 1
        e = codec.build(key, t, "core/social", b, ts=T + dt if ts is None else ts, n=f"{self.n:016x}", **kw)
        self.msgs.append(e)
        return e

    def fold(self, now=None, params=None):
        pos = {m["cid"]: i for i, m in enumerate(self.msgs)}
        return social.compute(self.msgs, now or T + 400 * DAY, lambda m: pos[m["cid"]], params)


def claim(log, key, name, dt=0, **kw):
    return log.add(key, "handle", {"op": "claim", "name": name}, dt, **kw)


# ── vectors ──
@pytest.mark.parametrize("v", V["valid"], ids=lambda v: v["name"])
def test_valid_vectors(v):
    key = tk(v["key_label"])
    assert key.did == v["did"] and codec.canonical(v["core"]) == v["canonical_core"]
    assert codec.compute_cid(v["core"]) == v["cid"] and codec.encode(codec.decode(v["wire"])) == v["wire"]


@pytest.mark.parametrize("v", V["invalid"], ids=lambda v: v["name"])
def test_invalid_vectors(v):
    with pytest.raises(codec.AgbError):
        codec.decode(v["line"])


def test_skeleton_reserved_and_guild_rep_vectors():
    for n, sk in V["skeletons"]:
        assert codec.handle_skeleton(n) == sk
    for n, why in V["reserved"]:
        assert reserved.reason(n) == why, n
    for scores, want in V["guild_rep"]:
        assert social.guild_reputation(scores, V["guild_rep_cap"]) == pytest.approx(want, abs=1e-6)


def test_scenario_vector_folds_to_expected_state():
    sc = V["scenario"]
    msgs = [codec.decode(s["wire"]) for s in sc["steps"]]
    pos = {m["cid"]: i for i, m in enumerate(msgs)}
    soc = social.compute(msgs, sc["now_ms"], lambda m: pos[m["cid"]])
    exp = sc["expected"]
    assert {n: h["did"] for n, h in soc.handles.items()} == exp["handles"]
    assert sorted(sorted([a, b]) for a, fs in soc.friends.items() for b in fs if a < b) == exp["friends"]
    g = soc.guilds[exp["guild"]["gid"]]
    assert g.members == exp["guild"]["members"] and soc.party == exp["party"]
    names = {s["name"]: m["cid"] for s, m in zip(sc["steps"], msgs)}
    assert {n: soc.ignored[c] for n, c in names.items() if c in soc.ignored} == exp["ignored"]


# ── handles: once only, no lookalikes ──
def test_melchizedek_claimed_once_no_duplicates_or_lookalikes():
    me, a, b = tk("melchizedek-standin"), tk("x1"), tk("x2")
    log = Log()
    first = claim(log, me, "melchizedek")
    tricks = ["melchizedek", "me1chizedek", "melch1zedek", "melchizedek_", "melchi-zedek", "m-e-l-c-h-i-z-e-d-e-k",
              "rnelchizedek", "melchlzedek", "me1ch1zedek"]
    good = [t for t in tricks if codec.HANDLE_RE.fullmatch(t)]
    for i, t in enumerate(good):
        claim(log, a if i % 2 else b, t, dt=i + 1)
    soc = log.fold()
    assert soc.handles["melchizedek"]["cid"] == first["cid"] and soc.name(me.did) == "melchizedek"
    assert len(soc.handles) == 1
    for m in log.msgs[1:]:
        assert soc.ignored[m["cid"]] in ("taken", "lookalike of @melchizedek")
    assert reserved.reason("melchizedek") is None


@pytest.mark.parametrize("name", ["Melchizedek", "MELCHIZEDEK", "m\u0435lchizedek", "melchized\u0435k", "\u043celchizedek",
                                  "melchizedek\u200b", "melchizédek", "_melchizedek", "-melchizedek", "melchizedek-",
                                  "melchizedek_", ".melchizedek", "melchizedek!", " melchizedek", "melchizedek.",
                                  "melchi__zedek", "1melchizedek"])
def test_case_non_ascii_and_edge_punctuation_rejected_by_codec(name):
    with pytest.raises(codec.AgbError):
        codec.build(tk("x1"), "handle", "core/social", {"op": "claim", "name": name})


def test_handle_length_bounds():
    assert codec.HANDLE_RE.fullmatch("abc") and codec.HANDLE_RE.fullmatch("a" * 24)
    assert not codec.HANDLE_RE.fullmatch("ab") and not codec.HANDLE_RE.fullmatch("a" * 25)


def test_backdated_claim_does_not_win_in_indexer_order():
    a, m = tk("alice"), tk("mallory")
    idx = Index()
    idx.add_line(codec.encode(codec.build(a, "handle", "core/social", {"op": "claim", "name": "alice"}, ts=T, n="1" * 16)), seq=100)
    idx.add_line(codec.encode(codec.build(m, "handle", "core/social", {"op": "claim", "name": "alice"}, ts=T - DAY, n="2" * 16)), seq=101)
    idx.apply_policy(RoomPolicy(), T + MIN)
    assert idx.social.resolve("@alice") == a.did
    assert any(why == "social: taken" for _, why in idx.hidden.values())


def test_rename_cooldown_release_hold_and_lapse():
    a, m = tk("alice"), tk("mallory")
    log = Log()
    claim(log, a, "alice")
    quick = claim(log, a, "alicia", dt=MIN)                     # inside 24 h cooldown
    log.add(a, "handle", {"op": "release", "name": "alice"}, dt=2 * MIN)
    snipe = claim(log, m, "alice", dt=3 * MIN)                  # 7-day hold for alice
    later = claim(log, m, "alice", dt=8 * DAY)
    soc = log.fold(now=T + 9 * DAY)
    assert soc.ignored[quick["cid"]] == "rate limit: rename cooldown"
    assert soc.ignored[snipe["cid"]] == "held for previous holder"
    assert later["cid"] not in soc.ignored and soc.resolve("alice") == m.did
    # squatting: a holder that signs nothing for 365 days loses the name to the next claim
    log2 = Log()
    claim(log2, m, "coolname")
    take = claim(log2, a, "coolname", dt=366 * DAY)
    soc2 = log2.fold(now=T + 367 * DAY)
    assert take["cid"] not in soc2.ignored and soc2.resolve("coolname") == a.did and soc2.name(m.did) is None


def test_future_dated_social_ignored():
    log = Log()
    e = claim(log, tk("alice"), "alice", dt=DAY)
    assert log.fold(now=T).ignored[e["cid"]] == "future-dated"


# ── reserved names (palmer: lock God, Jesus etc.; no blasphemy, slurs or obscenity) ──
@pytest.mark.parametrize("name", ["G0d", "JESUS", "j3sus", "god_sucks", "god", "g0d", "gawd", "jesus", "jesus777", "realjesus",
                                  "christ", "chr1st", "yahweh", "jehovah", "allah", "a11ah", "holyspirit", "holy-spirit",
                                  "messiah", "m3ss1ah", "lord", "the-lord", "jesusofficial", "godsucks", "jesus-is-fake",
                                  "fuckgod", "goddamn", "antichrist", "lordkiller", "admin", "m0d"])
def test_reserved_names_rejected(name):
    assert reserved.reason(name) is not None
    log = Log()
    if codec.HANDLE_RE.fullmatch(name):
        e = claim(log, tk("x1"), name)
        g = log.add(tk("x2"), "guild_create", {"name": name}, dt=1)
        soc = log.fold()
        assert soc.ignored[e["cid"]].startswith("reserved") and soc.ignored[g["cid"]].startswith("reserved")
        assert not soc.handles and not soc.guilds
    else:  # uppercase never reaches the list: the codec rejects it, and the MCP tool folds case first
        with pytest.raises(codec.AgbError):
            codec.build(tk("x1"), "handle", "core/social", {"op": "claim", "name": name})
        assert reserved.reason(name.lower()) is not None


@pytest.mark.parametrize("name", ["melchizedek", "godfrey", "christina", "christian", "goodgame", "warlord", "jesusislord",
                                  "therapist", "torpedo", "pomegranate", "nigeria", "classy", "assassin", "raccoon", "hello"])
def test_ordinary_names_allowed(name):
    assert reserved.reason(name) is None


def test_reserved_list_is_data_and_parses():
    d = reserved.parse(reserved.DATA.read_text())
    assert {"god", "jesus", "christ", "yahweh", "jehovah", "allah", "holyspirit", "messiah", "lord"} <= set(d["sacred"])
    assert "melchizedek" not in {w for v in d.values() for w in v}
    with pytest.raises(ValueError):
        reserved.parse("[nope]\nx\n")


# ── friends ──
def test_friendship_needs_both_signatures_and_unfriend():
    a, b, c, m = (tk(x) for x in ("alice", "bob", "carol", "mallory"))
    log = Log()
    r = log.add(a, "friend_request", {"to": b.did})
    forged = log.add(m, "friend_accept", {"to": a.did}, 1, ref=r["cid"])
    soc = log.fold()
    assert not soc.are_friends(a.did, b.did) and soc.ignored[forged["cid"]] == "no matching pending request"
    log.add(b, "friend_accept", {"to": a.did}, 2, ref=r["cid"])
    log.add(c, "friend_request", {"to": a.did}, 3)
    log.add(a, "friend_request", {"to": c.did}, 4)            # crossed requests
    soc = log.fold()
    assert soc.friends_of(a.did) == sorted([b.did, c.did]) and soc.are_friends(b.did, a.did)
    log.add(b, "unfriend", {"to": a.did}, 5)
    again = log.add(a, "friend_request", {"to": b.did}, 6)
    soc = log.fold()
    assert not soc.are_friends(a.did, b.did) and soc.ignored[again["cid"]] == "rate limit: re-request cooldown"


def test_friend_request_spam_capped_per_day():
    spam = tk("spammer")
    log = Log()
    reqs = [log.add(spam, "friend_request", {"to": tk(f"t{i}").did}, i * 2 * MIN) for i in range(25)]
    soc = log.fold()
    assert sum(r["cid"] not in soc.ignored for r in reqs) == 20
    assert soc.ignored[reqs[20]["cid"]] == "rate limit: friend requests per day"
    dup = log.add(spam, "friend_request", {"to": tk("t0").did}, DAY + MIN)
    assert log.fold().ignored[dup["cid"]] == "duplicate"


# ── guilds ──
def make_guild():
    f, o, x = tk("founder"), tk("officer"), tk("outsider")
    log = Log()
    g = log.add(f, "guild_create", {"name": "fliptide", "tag": "FLIP"})
    gid = social.gid_of(g["cid"])
    inv = log.add(f, "guild_invite", {"guild": gid, "to": o.did}, 1)
    log.add(o, "guild_join", {"guild": gid}, 2, ref=inv["cid"])
    log.add(f, "guild_role", {"guild": gid, "to": o.did, "role": "officer"}, 3)
    return log, gid, f, o, x


def test_guild_membership_needs_invite_and_join():
    log, gid, f, o, x = make_guild()
    inv = log.add(o, "guild_invite", {"guild": gid, "to": x.did}, 10)
    soc = log.fold()
    assert x.did not in soc.guilds[gid].members                     # invite alone is not membership
    log.add(x, "guild_join", {"guild": gid}, 20 * MIN, ref=inv["cid"])
    soc = log.fold()
    assert soc.guilds[gid].members == {f.did: "founder", o.did: "officer", x.did: "member"}
    assert soc.guild("@fliptide").gid == gid and soc.guilds_of(x.did) == [gid]


def test_guild_invite_expiry_and_permissions():
    log, gid, f, o, x = make_guild()
    inv = log.add(f, "guild_invite", {"guild": gid, "to": x.did}, 10)
    late = log.add(x, "guild_join", {"guild": gid}, 8 * DAY, ref=inv["cid"])
    m = tk("member2")
    soc = log.fold()
    assert soc.ignored[late["cid"]] == "invite expired"
    i2 = log.add(f, "guild_invite", {"guild": gid, "to": m.did}, 8 * DAY + 1)
    log.add(m, "guild_join", {"guild": gid}, 8 * DAY + 2, ref=i2["cid"])
    bad = log.add(m, "guild_invite", {"guild": gid, "to": x.did}, 8 * DAY + 3)
    kick_officer = log.add(o, "guild_leave", {"guild": gid, "to": f.did}, 8 * DAY + 4)
    role = log.add(o, "guild_role", {"guild": gid, "to": m.did, "role": "officer"}, 8 * DAY + 5)
    soc = log.fold()
    assert soc.ignored[bad["cid"]] == "not allowed to invite"
    assert soc.ignored[kick_officer["cid"]] == "not allowed to remove"
    assert soc.ignored[role["cid"]] == "only the founder sets roles"


def test_guild_invite_rate_limits():
    log, gid, f, o, x = make_guild()
    invs = [log.add(o, "guild_invite", {"guild": gid, "to": tk(f"inv{i}").did}, MIN * (i + 1)) for i in range(25)]
    soc = log.fold()
    assert sum(i["cid"] not in soc.ignored for i in invs) == 20
    assert soc.ignored[invs[-1]["cid"]] == "rate limit: invites per inviter per day"
    more = [log.add(f, "guild_invite", {"guild": gid, "to": tk(f"more{i}").did}, MIN * (30 + i)) for i in range(40)]
    soc = log.fold()
    assert sum(i["cid"] not in soc.ignored for i in invs + more) == 20 + 19   # founder already sent 1 today
    nextday = log.add(f, "guild_invite", {"guild": gid, "to": tk("more0").did}, DAY + 2 * MIN)
    assert nextday["cid"] in log.fold().ignored                      # still pending: duplicate, not re-sent


def test_guild_per_guild_cap():
    log, gid, f, o, x = make_guild()
    p = social.Params(invites_per_inviter_per_day=100, invites_per_guild_per_day=5)
    invs = [log.add(f, "guild_invite", {"guild": gid, "to": tk(f"g{i}").did}, MIN * (i + 1)) for i in range(8)]
    soc = log.fold(params=p)
    assert sum(i["cid"] not in soc.ignored for i in invs) == 4      # 1 earlier invite + 4 = 5 per guild per day
    assert soc.ignored[invs[-1]["cid"]] == "rate limit: invites per guild"


def test_founder_handoff_leave_and_disband():
    log, gid, f, o, x = make_guild()
    stuck = log.add(f, "guild_leave", {"guild": gid}, 10)
    log.add(f, "guild_role", {"guild": gid, "to": o.did, "role": "founder"}, 11)
    log.add(f, "guild_leave", {"guild": gid}, 12)
    soc = log.fold()
    assert soc.ignored[stuck["cid"]].startswith("founder must hand off")
    assert soc.guilds[gid].members == {o.did: "founder"} and soc.guilds[gid].founder == o.did
    log.add(o, "guild_leave", {"guild": gid}, 13)
    soc = log.fold()
    assert soc.guilds[gid].disbanded and soc.guild("fliptide") is None


def test_guild_as_party_only_for_members():
    log, gid, f, o, x = make_guild()
    mine = log.add(o, "ask", {"task": "t", "budget": {"asset": "FLOP", "max": "1"}, "guild": gid}, 10)
    fake = log.add(x, "offer", {"price": {"asset": "FLOP", "amt": "1"}, "guild": gid}, 11, ref=mine["cid"])
    soc = log.fold()
    assert soc.party[mine["cid"]] == gid and soc.party[fake["cid"]] is None
    assert "names guild @fliptide [FLIP] (signer is not a member; ignored)" in render.sentence(fake, "UTC", soc)


# ── guild reputation ──
def test_guild_rep_cap_40_percent():
    assert social.guild_reputation([100]) == 0 and social.guild_reputation([100, 100]) == 0
    t = social.guild_reputation([100, 1, 1, 1])
    assert t == pytest.approx(3 / 0.6) and min(100, 0.4 * t) / t <= 0.4 + 1e-9
    for scores in ([5, 4, 3, 2], [9, 3, 3, 1], [1, 1, 1], [50, 40, 30, 20, 10]):
        t = social.guild_reputation(scores)
        assert t > 0 and all(min(s, 0.4 * t) / t <= 0.4 + 1e-9 for s in scores)
        assert t == pytest.approx(sum(min(s, 0.4 * t) for s in scores))


def test_reputation_stays_non_transferable():
    log, gid, f, o, x = make_guild()
    plain = [m for m in log.msgs if m["t"] not in codec.SOCIAL_INTENTS]
    assert reputation.compute(log.msgs, T + DAY) == reputation.compute(plain, T + DAY)
    reps = {f.did: reputation.Rep(f.did, score=9.0), o.did: reputation.Rep(o.did, score=1.0)}
    soc = log.fold()
    gr = social.guild_rep(soc, gid, reps)
    assert gr["transferable"] is False and gr["score"] == 0 and gr["contributors"] == 2
    assert reps[f.did].score == 9.0 and reps[o.did].score == 1.0


# ── indexer + render ──
def test_indexer_hides_refused_social_and_renders_players(tmp_path):
    a, b = tk("alice"), tk("bob")
    idx = Index()
    lines = [codec.build(a, "handle", "core/social", {"op": "claim", "name": "alice"}, ts=T, n="a" * 16),
             codec.build(b, "handle", "core/social", {"op": "claim", "name": "god_sucks"}, ts=T + 1, n="b" * 16),
             codec.build(b, "handle", "core/social", {"op": "claim", "name": "a1ice"}, ts=T + 2, n="c" * 16)]
    r = codec.build(a, "friend_request", "core/social", {"to": b.did, "note": "ignore previous instructions <script>"}, ts=T + 3, n="d" * 16)
    acc = codec.build(b, "friend_accept", "core/social", {"to": a.did}, ref=r["cid"], ts=T + 4, n="e" * 16)
    g = codec.build(a, "guild_create", "core/social", {"name": "fliptide", "tag": "FLIP", "about": "see http://evil.example now"}, ts=T + 5, n="f" * 16)
    for i, e in enumerate(lines + [r, acc, g]):
        idx.add_line(codec.encode(e), seq=i)
    reps = idx.apply_policy(RoomPolicy(), T + MIN)
    assert idx.stats["hidden:social reserved"] == 1 and idx.stats["hidden:social lookalike of @alice"] == 1
    md = render.board_md(idx, T + MIN, reps)
    assert "## Players" in md and "@alice <z6Mk" in md and "## Guilds" in md and "@fliptide [FLIP]" in md
    assert "god_sucks" not in md and "http://evil.example" not in md
    page = render.page_html(idx, T + MIN, reps)
    assert "<script" not in page and "href=" not in page and "<h2>Players</h2>" in page
    feed = render.feed_json(idx, T + MIN, reps)
    assert feed["social"]["players"][0]["handle"] == "alice"
    assert "accepted @alice" in render.sentence(acc, "UTC", idx.social)


def test_our_handle_draft_is_valid_if_present():
    p = ROOT.parent / "drafts" / "handle_claim_melchizedek.txt"
    if not p.exists():
        pytest.skip("local draft not present")
    env = codec.decode(p.read_text().strip())
    assert env["t"] == "handle" and env["b"] == {"op": "claim", "name": "melchizedek"} and env["from"] == OUR_DID


# ── MCP: local drafts only ──
@pytest.fixture
def srv(tmp_path, keys):
    from agentwork.didkey import save_key
    from agentwork.mcp_server import Server
    kf = tmp_path / "agent.seed"
    save_key(keys["a"], kf)
    return Server({"AGB_KEY_FILE": str(kf), "AGB_OUTBOX": str(tmp_path / "outbox"), "AGB_OUT": str(tmp_path / "site")})


def mcall(s, name, args):
    return s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})["result"]


def test_mcp_social_tools_write_local_drafts_and_resolve_tags(srv, tmp_path, keys):
    names = {t["name"] for t in srv.handle({"jsonrpc": "2.0", "id": 0, "method": "tools/list"})["result"]["tools"]}
    assert {"post_handle", "post_friend_request", "post_friend_accept", "post_unfriend", "post_guild_create", "post_guild_invite",
            "post_guild_join", "post_guild_leave", "post_guild_role", "whois", "list_friends", "get_guild"} <= names
    # a friend's signed handle claim arrives in the local index (e.g. read from the board)
    friend = keys["b"]
    src = tmp_path / "board.agb1"
    src.write_text(codec.encode(codec.build(friend, "handle", "core/social", {"op": "claim", "name": "hughbot"})) + "\n")
    srv.sources = [src]
    out = mcall(srv, "post_handle", {"room": "core/social", "name": "@Palmer_Agent"})["structuredContent"]
    assert out["posted"] is False and out["draft"] is True and '"name":"palmer_agent"' in out["wire"]
    fr = mcall(srv, "post_friend_request", {"room": "core/social", "to": "@hughbot"})["structuredContent"]
    assert codec.decode(fr["wire"])["b"]["to"] == friend.did and fr["posted"] is False
    who = mcall(srv, "whois", {"who": "@hughbot"})["structuredContent"]
    assert who["found"] and who["did"] == friend.did and who["display"].startswith("@hughbot <z6Mk")
    lf = mcall(srv, "list_friends", {"who": "@palmer_agent"})["structuredContent"]
    assert lf["outgoing_requests"][0]["handle"] == "hughbot"
    g = mcall(srv, "post_guild_create", {"room": "core/social", "name": "fliptide", "tag": "FLIP"})["structuredContent"]
    inv = mcall(srv, "post_guild_invite", {"room": "core/social", "guild": "@fliptide", "to": "@hughbot"})["structuredContent"]
    assert codec.decode(inv["wire"])["b"]["guild"] == "guild:" + g["cid"]
    gg = mcall(srv, "get_guild", {"guild": "@fliptide"})["structuredContent"]
    assert gg["found"] and gg["pending_invites"][0]["to"] == friend.did and gg["reputation"]["transferable"] is False
    assert len(mcall(srv, "list_outbox", {})["structuredContent"]["drafts"]) == 4


@pytest.mark.parametrize("name", ["G0d", "JESUS", "j3sus", "god_sucks", "admin"])
def test_mcp_refuses_reserved_handles(srv, name):
    r = mcall(srv, "post_handle", {"room": "core/social", "name": name})
    assert r["isError"] and "can never be claimed" in r["content"][0]["text"]
    assert mcall(srv, "post_guild_create", {"room": "core/social", "name": name})["isError"]


def test_mcp_unknown_tag_is_error(srv):
    assert mcall(srv, "post_friend_request", {"room": "core/social", "to": "@nobody_here"})["isError"]
