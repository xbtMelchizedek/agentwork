"""Social layer v0 (SPEC §10): gamer tags, friends, guilds. A pure function of the signed log.

Everything here is recomputed from agb1 messages in *indexer order* (see `order_key`); no
message can move reputation between DIDs. Guild reputation is a derived view over members'
own non-transferable scores, capped so no single member makes up more than `cap_frac` of it.

Anti-abuse (all parameters explicit, apps may publish others):
* handles: one per DID, ASCII-only syntax (codec), reserved names, confusable-skeleton
  collisions, 24 h rename cooldown, 7 d hold for the previous holder after a rename/release,
  and lapse after 365 d of signer inactivity (squatting);
* friends: both sides sign (request + accept, or crossed requests); one pending request per
  pair; 20 requests per DID per day; 7 d cooldown after being declined or unfriended;
* guilds: membership needs an invite from a founder/officer *and* a join by the invitee;
  invites expire after 7 d; 20 invites per inviter per day, 50 per guild per day, 100 pending
  per guild; 3 guild_create per DID per 30 d.

Rate windows use each signer's clock forced non-decreasing in log order (backdating does not
reopen a window), and social messages dated more than `max_future_ms` past `now` are ignored.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import codec, reserved

DAY_MS = 86_400_000
SOCIAL = frozenset(codec.SOCIAL_INTENTS)


@dataclass(frozen=True)
class Params:
    rename_cooldown_ms: int = DAY_MS
    release_hold_ms: int = 7 * DAY_MS
    handle_lapse_ms: int = 365 * DAY_MS
    friend_requests_per_day: int = 20
    rerequest_cooldown_ms: int = 7 * DAY_MS
    invite_ttl_ms: int = 7 * DAY_MS
    invites_per_inviter_per_day: int = 20
    invites_per_guild_per_day: int = 50
    max_pending_invites_per_guild: int = 100
    guild_creates_per_30d: int = 3
    max_future_ms: int = 5 * 60_000
    guild_cap_frac: float = 0.40


def gid_of(create_cid: str) -> str:
    return "guild:" + create_cid


@dataclass
class Guild:
    gid: str
    name: str
    founder: str
    cid: str
    tag: str | None = None
    about: str | None = None
    members: dict = field(default_factory=dict)          # did -> role
    invites: dict = field(default_factory=dict)          # invite cid -> (inviter, to, eff_ts)
    disbanded: bool = False


@dataclass
class Social:
    handles: dict = field(default_factory=dict)          # name -> {"did","cid","since"}
    by_did: dict = field(default_factory=dict)           # did -> name
    friends: dict = field(default_factory=lambda: defaultdict(set))   # did -> {did}
    pending: dict = field(default_factory=dict)          # (from, to) -> request cid
    guilds: dict = field(default_factory=dict)           # gid -> Guild
    guild_names: dict = field(default_factory=dict)      # name -> gid
    party: dict = field(default_factory=dict)            # ask/offer/claim cid -> gid (verified member) | None
    ignored: dict = field(default_factory=dict)          # cid -> reason
    applied: list = field(default_factory=list)          # cids that changed state, in order

    # ── queries ──
    def name(self, did: str) -> str | None:
        return self.by_did.get(did)

    def resolve(self, who: str) -> str | None:
        """'@name', 'name' or a did:key -> did:key (None if unknown)."""
        if who.startswith("did:key:"):
            return who
        h = self.handles.get(who.lstrip("@").lower())
        return h["did"] if h else None

    def friends_of(self, did: str) -> list[str]:
        return sorted(self.friends.get(did, ()))

    def are_friends(self, a: str, b: str) -> bool:
        return b in self.friends.get(a, ())

    def guilds_of(self, did: str) -> list[str]:
        return sorted(g.gid for g in self.guilds.values() if did in g.members)

    def guild(self, ref: str) -> Guild | None:
        if ref.startswith("guild:"):
            return self.guilds.get(ref)
        gid = self.guild_names.get(ref.lstrip("@").lower())
        return self.guilds.get(gid) if gid else None


def guild_reputation(members_scores: Iterable[float], cap_frac: float = 0.40) -> float:
    """Largest T with T = sum_i min(s_i, cap_frac*T): no member supplies more than cap_frac of T.

    With cap_frac = 0.4 a guild needs at least three members with work scores to have any
    guild reputation, so one worker (or a pair) cannot dress up as a guild.
    """
    s = sorted((x for x in members_scores if x > 0), reverse=True)
    if not s:
        return 0.0
    for k in range(len(s) + 1):          # k = number of members hitting the cap
        denom = 1.0 - cap_frac * k
        if denom <= 0:
            break
        t = sum(s[k:]) / denom
        if t <= 0:
            break
        cap = cap_frac * t
        if (k == 0 or s[k - 1] >= cap - 1e-12) and (k == len(s) or s[k] <= cap + 1e-12):
            return t
    return 0.0


def guild_rep(soc: Social, gid: str, reps: dict, cap_frac: float = 0.40) -> dict:
    g = soc.guilds.get(gid)
    if not g or g.disbanded:
        return {"guild": gid, "score": 0.0, "members": 0, "contributors": 0, "transferable": False}
    scores = {d: (reps[d].score if d in reps else 0.0) for d in g.members}
    total = guild_reputation(scores.values(), cap_frac)
    return {"guild": gid, "name": g.name, "score": round(total, 6), "members": len(g.members),
            "contributors": sum(1 for v in scores.values() if v > 0),
            "member_share_cap": cap_frac, "transferable": False,
            "note": "derived view: sum of members' own settled+attested work scores, each capped at "
                    f"{int(cap_frac * 100)}% of the total; leaving takes your contribution with you"}


def compute(messages: Iterable[dict], now_ms: int, order_key: Callable[[dict], tuple] | None = None,
            params: Params | None = None) -> Social:
    """Fold validated envelopes (any intents) into social state, in indexer order."""
    p = params or Params()
    key = order_key or (lambda m: (m["ts"], m["cid"]))
    msgs = sorted(messages, key=key)
    soc = Social()
    clock: dict[str, int] = {}                 # signer -> running max ts
    last_seen: dict[str, int] = {}             # signer -> eff ts of latest valid message
    holds: dict[str, tuple[str, int]] = {}     # handle -> (previous holder, hold until)
    last_claim: dict[str, int] = {}
    req_times: dict[str, list[int]] = defaultdict(list)
    blocked: dict[tuple, int] = {}             # (from, to) -> requests ignored until
    inv_by_inviter: dict[str, list[int]] = defaultdict(list)
    inv_by_guild: dict[str, list[int]] = defaultdict(list)
    creates: dict[str, list[int]] = defaultdict(list)

    def ign(m, why):
        soc.ignored[m["cid"]] = why

    def ok(m):
        soc.applied.append(m["cid"])

    def win(lst, t, span):
        return [x for x in lst if t - x < span]

    def owner_of_skeleton(sk: str, names: dict) -> str | None:
        for n in names:
            if codec.handle_skeleton(n) == sk:
                return n
        return None

    for m in msgs:
        t, b, me = m["t"], m["b"], m["from"]
        eff = max(m["ts"], clock.get(me, 0))
        if t in SOCIAL and m["ts"] > now_ms + p.max_future_ms:
            ign(m, "future-dated")
            continue
        clock[me] = eff
        last_seen[me] = eff
        if t in ("ask", "offer", "claim") and "guild" in b:
            g = soc.guilds.get(b["guild"])
            soc.party[m["cid"]] = b["guild"] if g and not g.disbanded and me in g.members else None
            continue
        if t not in SOCIAL:
            continue

        # ── handles ──
        if t == "handle":
            name = b["name"]
            if b["op"] == "release":
                if soc.handles.get(name, {}).get("did") != me:
                    ign(m, "not your handle"); continue
                del soc.handles[name]; soc.by_did.pop(me, None)
                holds[name] = (me, eff + p.release_hold_ms); ok(m); continue
            cur = soc.handles.get(name)
            if cur and cur["did"] == me:
                ign(m, "duplicate"); continue
            if me in last_claim and eff - last_claim[me] < p.rename_cooldown_ms:
                ign(m, "rate limit: rename cooldown"); continue
            sk = codec.handle_skeleton(name)
            why = reserved.reason(name)
            if why:
                ign(m, why); continue
            hold = holds.get(name) or next(((d, u) for n, (d, u) in holds.items() if codec.handle_skeleton(n) == sk), None)
            if hold and hold[0] != me and eff < hold[1]:
                ign(m, "held for previous holder"); continue
            clash = owner_of_skeleton(sk, soc.handles)
            if clash and soc.handles[clash]["did"] != me:
                holder = soc.handles[clash]["did"]
                if eff - last_seen.get(holder, 0) > p.handle_lapse_ms:     # squatter went dormant
                    soc.by_did.pop(holder, None); del soc.handles[clash]
                else:
                    ign(m, "taken" if clash == name else f"lookalike of @{clash}"); continue
            old = soc.by_did.get(me)
            if old:
                del soc.handles[old]
                holds[old] = (me, eff + p.release_hold_ms)
            soc.handles[name] = {"did": me, "cid": m["cid"], "since": eff}
            soc.by_did[me] = name
            last_claim[me] = eff
            ok(m); continue

        # ── friends ──
        if t == "friend_request":
            to = b["to"]
            if soc.are_friends(me, to):
                ign(m, "already friends"); continue
            if (me, to) in soc.pending:
                ign(m, "duplicate"); continue
            if eff < blocked.get((me, to), 0):
                ign(m, "rate limit: re-request cooldown"); continue
            w = win(req_times[me], eff, DAY_MS)
            if len(w) >= p.friend_requests_per_day:
                ign(m, "rate limit: friend requests per day"); continue
            req_times[me] = w + [eff]
            if (to, me) in soc.pending:                     # crossed requests: both have signed
                del soc.pending[(to, me)]
                soc.friends[me].add(to); soc.friends[to].add(me)
            else:
                soc.pending[(me, to)] = m["cid"]
            ok(m); continue
        if t == "friend_accept":
            req = b["to"]
            if soc.pending.get((req, me)) != m["ref"]:
                ign(m, "no matching pending request"); continue
            del soc.pending[(req, me)]
            soc.friends[me].add(req); soc.friends[req].add(me)
            ok(m); continue
        if t == "unfriend":
            to = b["to"]
            if soc.are_friends(me, to):
                soc.friends[me].discard(to); soc.friends[to].discard(me)
                blocked[(to, me)] = eff + p.rerequest_cooldown_ms
            elif (me, to) in soc.pending:                   # withdraw my request
                del soc.pending[(me, to)]
            elif (to, me) in soc.pending:                   # decline theirs
                del soc.pending[(to, me)]
                blocked[(to, me)] = eff + p.rerequest_cooldown_ms
            else:
                ign(m, "nothing to undo"); continue
            ok(m); continue

        # ── guilds ──
        if t == "guild_create":
            name = b["name"]; sk = codec.handle_skeleton(name)
            w = win(creates[me], eff, 30 * DAY_MS)
            if len(w) >= p.guild_creates_per_30d:
                ign(m, "rate limit: guild creates"); continue
            why = reserved.reason(name)
            if why:
                ign(m, why); continue
            clash = owner_of_skeleton(sk, soc.guild_names)
            if clash:
                ign(m, "taken" if clash == name else f"lookalike of guild @{clash}"); continue
            creates[me] = w + [eff]
            g = Guild(gid_of(m["cid"]), name, me, m["cid"], b.get("tag"), b.get("about"), {me: "founder"})
            soc.guilds[g.gid] = g; soc.guild_names[name] = g.gid
            ok(m); continue
        g = soc.guilds.get(b["guild"])
        if not g or g.disbanded:
            ign(m, "unknown guild"); continue
        role = g.members.get(me)
        if t == "guild_invite":
            to = b["to"]
            if role not in ("founder", "officer"):
                ign(m, "not allowed to invite"); continue
            if to in g.members:
                ign(m, "already a member"); continue
            g.invites = {c: v for c, v in g.invites.items() if eff - v[2] < p.invite_ttl_ms}
            if any(v[1] == to for v in g.invites.values()):
                ign(m, "duplicate"); continue
            wi, wg = win(inv_by_inviter[me], eff, DAY_MS), win(inv_by_guild[g.gid], eff, DAY_MS)
            if len(wi) >= p.invites_per_inviter_per_day:
                ign(m, "rate limit: invites per inviter per day"); continue
            if len(wg) >= p.invites_per_guild_per_day or len(g.invites) >= p.max_pending_invites_per_guild:
                ign(m, "rate limit: invites per guild"); continue
            inv_by_inviter[me] = wi + [eff]; inv_by_guild[g.gid] = wg + [eff]
            g.invites[m["cid"]] = (me, to, eff)
            ok(m); continue
        if t == "guild_join":
            inv = g.invites.get(m["ref"])
            if not inv or inv[1] != me:
                ign(m, "no matching invite"); continue
            if eff - inv[2] >= p.invite_ttl_ms:
                del g.invites[m["ref"]]; ign(m, "invite expired"); continue
            if g.members.get(inv[0]) not in ("founder", "officer"):
                del g.invites[m["ref"]]; ign(m, "inviter no longer allowed to invite"); continue
            del g.invites[m["ref"]]
            if me in g.members:
                ign(m, "already a member"); continue
            g.members[me] = "member"
            ok(m); continue
        if t == "guild_leave":
            target = b.get("to", me)
            trole = g.members.get(target)
            if trole is None:
                ign(m, "not a member"); continue
            if target != me and not (role == "founder" or (role == "officer" and trole == "member")):
                ign(m, "not allowed to remove"); continue
            if trole == "founder":
                if len(g.members) > 1:
                    ign(m, "founder must hand off (guild_role founder) before leaving"); continue
                g.disbanded = True
                soc.guild_names.pop(g.name, None)
            del g.members[target]
            ok(m); continue
        if t == "guild_role":
            to, new = b["to"], b["role"]
            if role != "founder":
                ign(m, "only the founder sets roles"); continue
            if to not in g.members:
                ign(m, "not a member"); continue
            if new == "founder":
                g.members[me] = "officer"; g.founder = to
            g.members[to] = new
            ok(m); continue
    return soc
