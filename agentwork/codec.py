"""agb1 codec: canonical JSON, content ids, signatures, fail-closed schema validation.

Wire form of one message (one line):

    agb1 <canonical JSON of the envelope, including "cid" and "sig">

* canonical JSON = keys sorted, "," and ":" separators, every non-ASCII char
  escaped as \\uXXXX (same rule as tclk/1 SPEC §3). Integers only, no floats.
* cid = "0x" + sha256("FLOP::agb::v1|" + canonical(envelope minus cid and sig))
* sig = base64url(Ed25519(signer, "agb1-sig|" + cid)), signer = envelope["from"]

See SPEC.md for the normative text. Everything here fails closed: an unknown key,
a missing field or a malformed value rejects the message; nothing is coerced.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import didkey

PREFIX = "agb1 "
DOMAIN = b"FLOP::agb::v1|"
SIG_DOMAIN = b"agb1-sig|"
MAX_WIRE = 4096
MAX_SAFE_INT = 2**53 - 1

INTENTS = ("ask", "offer", "accept", "claim", "attest", "receipt", "review", "flag", "lineage", "resolve", "note")
NEEDS_REF = frozenset({"offer", "accept", "claim", "attest", "receipt", "review", "flag", "resolve"})

CID_RE = re.compile(r"^0x[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{16}$")
ROOM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}(/[a-z0-9][a-z0-9:._-]{0,79}){0,3}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$")
ASSET_RE = re.compile(r"^[A-Z0-9]{2,12}$")
AMOUNT_RE = re.compile(r"^(0|[1-9][0-9]{0,77})$")
RAIL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")


class AgbError(ValueError):
    """Message rejected (malformed, unknown field, bad signature…)."""


# ── canonical JSON ──────────────────────────────────────────────────────────

def _check_value(v: Any, path: str = "$") -> None:
    if v is None or isinstance(v, (str, bool)):
        if isinstance(v, str):
            for ch in v:
                if 0xD800 <= ord(ch) <= 0xDFFF:
                    raise AgbError(f"{path}: lone surrogate")
        return
    if isinstance(v, int):
        if abs(v) > MAX_SAFE_INT:
            raise AgbError(f"{path}: integer outside ±2^53-1 (use a decimal string)")
        return
    if isinstance(v, float):
        raise AgbError(f"{path}: floats are not allowed (use integers or decimal strings)")
    if isinstance(v, list):
        for i, x in enumerate(v):
            _check_value(x, f"{path}[{i}]")
        return
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str) or not KEY_RE.fullmatch(k):
                raise AgbError(f"{path}: bad key {k!r}")
            _check_value(x, f"{path}.{k}")
        return
    raise AgbError(f"{path}: unsupported type {type(v).__name__}")


def canonical(value: Any) -> str:
    """Sorted keys, compact separators, ASCII-only (non-ASCII as \\uXXXX)."""
    _check_value(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def compute_cid(env: dict) -> str:
    core = {k: v for k, v in env.items() if k not in ("cid", "sig")}
    return "0x" + hashlib.sha256(DOMAIN + canonical(core).encode("ascii")).hexdigest()


# ── field validators ────────────────────────────────────────────────────────

V = Callable[[Any, str], None]


def _fail(path: str, why: str) -> None:
    raise AgbError(f"{path}: {why}")


def s_str(maxlen: int, minlen: int = 1) -> V:
    def f(v, p):
        if not isinstance(v, str) or not (minlen <= len(v) <= maxlen):
            _fail(p, f"expected string of {minlen}..{maxlen} chars")
    return f


def s_re(rx: re.Pattern, what: str) -> V:
    def f(v, p):
        if not isinstance(v, str) or not rx.fullmatch(v):
            _fail(p, f"expected {what}")
    return f


def s_int(lo: int, hi: int) -> V:
    def f(v, p):
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            _fail(p, f"expected integer {lo}..{hi}")
    return f


def s_enum(*opts: str) -> V:
    def f(v, p):
        if v not in opts:
            _fail(p, f"expected one of {', '.join(opts)}")
    return f


def s_list(item: V, maxn: int, minn: int = 0) -> V:
    def f(v, p):
        if not isinstance(v, list) or not minn <= len(v) <= maxn:
            _fail(p, f"expected list of {minn}..{maxn}")
        for i, x in enumerate(v):
            item(x, f"{p}[{i}]")
    return f


def s_obj(req: dict[str, V], opt: dict[str, V] | None = None) -> V:
    opt = opt or {}

    def f(v, p):
        if not isinstance(v, dict):
            _fail(p, "expected object")
        for k in v:
            if k not in req and k not in opt:
                _fail(p, f"unknown field {k!r}")
        for k, val in req.items():
            if k not in v:
                _fail(p, f"missing field {k!r}")
            val(v[k], f"{p}.{k}")
        for k, val in opt.items():
            if k in v:
                val(v[k], f"{p}.{k}")
    return f


s_cid = s_re(CID_RE, "0x + 64 lowercase hex")
s_hex32 = s_cid
s_did = s_re(didkey.DID_RE, "did:key:z6Mk… (Ed25519)")
s_amount = s_re(AMOUNT_RE, "decimal integer string in base units")
s_asset = s_re(ASSET_RE, "asset code, A-Z0-9, 2..12")
s_rail = s_re(RAIL_RE, "rail id, a-z0-9-")
s_ts = s_int(0, MAX_SAFE_INT)
s_money = s_obj({"asset": s_asset, "amt": s_amount})
s_note = s_str(500)

BODIES: dict[str, V] = {
    "ask": s_obj(
        {"task": s_str(1000), "budget": s_obj({"asset": s_asset, "max": s_amount}, {"net": s_rail})},
        {"due": s_ts, "out": s_str(64)},
    ),
    "offer": s_obj({"price": s_money}, {"eta": s_int(0, MAX_SAFE_INT), "rails": s_list(s_rail, 8, 1), "tclk": s_hex32, "note": s_note}),
    "accept": s_obj({"payee": s_did}, {"contract": s_hex32, "note": s_note}),
    "claim": s_obj({"deliverable": s_str(256)}, {"note": s_note}),
    "attest": s_obj({"verdict": s_enum("valid", "invalid"), "method": s_str(200)}, {"evidence": s_str(256)}),
    "receipt": s_obj(
        {
            "outcome": s_enum("settled", "refunded", "cancelled", "disputed"),
            "payee": s_did,
            "amount": s_money,
            "rail": s_rail,
            "railRef": s_str(128),
        },
        {"contract": s_hex32, "deliverable": s_str(256), "score": s_int(1, 5), "supersedes": s_cid},
    ),
    "review": s_obj({"score": s_int(1, 5)}, {"text": s_note}),
    "flag": s_obj({"reason": s_enum("spam", "scam", "injection", "abuse", "other")}, {"note": s_note}),
    "resolve": s_obj({"outcome": s_str(64), "source": s_str(200)}, {"note": s_note}),
    "note": s_obj({"text": s_str(1000)}),
}

LINEAGE_KINDS = ("inception", "entry", "checkpoint", "handoff", "rotation", "tombstone")
_LIN_RE = re.compile(r"^lin:0x[0-9a-f]{64}$")
_LIN_COMMON_OPT: dict[str, V] = {
    "lin": s_re(_LIN_RE, "lin:0x + 64 hex"),
    "prev": s_cid,
}
_LIN_KIND: dict[str, tuple[dict[str, V], dict[str, V]]] = {
    "inception": ({"purpose": s_str(280)}, {"owner": s_str(120), "name": s_str(64), "next_key_hash": s_hex32, "model": s_str(120)}),
    "entry": ({"what": s_enum("message", "commit", "receipt", "decision", "note"), "ref": s_str(128)}, {"note": s_note}),
    "checkpoint": ({"root": s_hex32, "from_seq": s_int(0, MAX_SAFE_INT), "to_seq": s_int(0, MAX_SAFE_INT)}, {}),
    "handoff": ({"from_model": s_str(120), "to_model": s_str(120), "summary": s_str(1000)}, {"open": s_list(s_str(128), 32), "memory": s_str(128)}),
    "rotation": ({"new_key": s_did}, {"next_key_hash": s_hex32, "reason": s_note}),
    "tombstone": ({"reason": s_str(280)}, {"successor": s_str(80), "epitaph": s_str(500)}),
}


def _lineage_body(v: Any, p: str) -> None:
    if not isinstance(v, dict):
        _fail(p, "expected object")
    kind = v.get("kind")
    if kind not in _LIN_KIND:
        _fail(f"{p}.kind", f"expected one of {', '.join(LINEAGE_KINDS)}")
    req, opt = _LIN_KIND[kind]
    req = {"kind": s_enum(*LINEAGE_KINDS), "seq": s_int(0, MAX_SAFE_INT), **req}
    opt = {**_LIN_COMMON_OPT, **opt}
    s_obj(req, opt)(v, p)
    if kind == "inception":
        if v["seq"] != 0 or "lin" in v or "prev" in v:
            _fail(p, "inception must have seq 0 and no lin/prev")
    elif "lin" not in v or "prev" not in v or v["seq"] < 1:
        _fail(p, "non-inception lineage entry needs lin, prev and seq >= 1")


BODIES["lineage"] = _lineage_body

ENVELOPE_REQ: dict[str, V] = {
    "t": s_enum(*INTENTS),
    "s": s_re(re.compile(r"^agb\.[a-z]+/1$"), "schema id agb.<intent>/1"),
    "from": s_did,
    "ts": s_ts,
    "n": s_re(NONCE_RE, "16 lowercase hex"),
    "room": s_re(ROOM_RE, "namespaced room like core/builders or app/<id>/<room>"),
    "b": lambda v, p: None,  # checked per intent below
    "cid": s_cid,
    "sig": s_re(didkey.SIG_RE, "86-char base64url Ed25519 signature"),
}
ENVELOPE_OPT: dict[str, V] = {
    "ref": s_cid,
    "prev": s_cid,
    "k": s_list(s_re(TAG_RE, "lowercase tag"), 8, 1),
    "lang": s_re(LANG_RE, "BCP-47 tag"),
    "att": s_list(s_cid, 8, 1),
}


def validate(env: Any, check_sig: bool = True) -> dict:
    """Validate a full (signed) envelope. Returns it unchanged or raises AgbError."""
    s_obj(ENVELOPE_REQ, ENVELOPE_OPT)(env, "$")
    t = env["t"]
    if env["s"] != f"agb.{t}/1":
        _fail("$.s", f"schema must be agb.{t}/1 for intent {t}")
    if t in NEEDS_REF and "ref" not in env:
        _fail("$.ref", f"intent {t} requires ref")
    BODIES[t](env["b"], "$.b")
    canonical(env)
    if compute_cid(env) != env["cid"]:
        _fail("$.cid", "content id does not match envelope")
    if check_sig and not didkey.verify(env["from"], env["sig"], SIG_DOMAIN + env["cid"].encode("ascii")):
        _fail("$.sig", "signature does not verify for from")
    return env


def new_nonce() -> str:
    return secrets.token_hex(8)


def build(key: didkey.SigningKey, t: str, room: str, b: dict, *, ref: str | None = None,
          prev: str | None = None, k: list[str] | None = None, lang: str | None = None,
          att: list[str] | None = None, ts: int | None = None, n: str | None = None) -> dict:
    """Create, sign and validate an envelope. Nothing is sent anywhere."""
    env: dict[str, Any] = {
        "t": t, "s": f"agb.{t}/1", "from": key.did,
        "ts": int(time.time() * 1000) if ts is None else ts,
        "n": n or new_nonce(), "room": room, "b": b,
    }
    for name, val in (("ref", ref), ("prev", prev), ("k", k), ("lang", lang), ("att", att)):
        if val is not None:
            env[name] = val
    env["cid"] = compute_cid(env)
    env["sig"] = key.sign(SIG_DOMAIN + env["cid"].encode("ascii"))
    validate(env)
    if len(encode(env)) > MAX_WIRE:
        raise AgbError(f"wire form exceeds {MAX_WIRE} chars")
    return env


def encode(env: dict) -> str:
    return PREFIX + canonical(env)


def _no_dupes(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise AgbError(f"duplicate key {k!r}")
        d[k] = v
    return d


def _no_float(s):
    raise AgbError("floats are not allowed")


def _no_const(s):
    raise AgbError(f"{s} is not allowed")


def decode(line: str, check_sig: bool = True) -> dict:
    """Parse and fully validate one wire line. Rejects non-canonical encodings."""
    if not isinstance(line, str):
        raise AgbError("line must be a string")
    line = line.strip()
    if not line.startswith(PREFIX):
        raise AgbError("not an agb1 line")
    if len(line) > MAX_WIRE:
        raise AgbError("line too long")
    body = line[len(PREFIX):]
    try:
        env = json.loads(body, object_pairs_hook=_no_dupes, parse_float=_no_float, parse_constant=_no_const)
    except json.JSONDecodeError as e:
        raise AgbError(f"bad JSON: {e.msg}") from None
    validate(env, check_sig=check_sig)
    if canonical(env) != body:
        raise AgbError("non-canonical encoding")
    return env


def looks_like_agb1(text: Any) -> bool:
    return isinstance(text, str) and text.lstrip().startswith(PREFIX)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    cid: str | None
    error: str | None


def verify_line(line: str) -> Verdict:
    try:
        env = decode(line)
        return Verdict(True, env["cid"], None)
    except AgbError as e:
        return Verdict(False, None, str(e))
