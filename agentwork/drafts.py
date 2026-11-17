"""Turn flat tool arguments into signed agb1 drafts (shared by the MCP server and CLI)."""
from __future__ import annotations

from typing import Any, Callable

from . import codec, didkey

S = {"type": "string"}
I = {"type": "integer"}


def _opt(d: dict, a: dict, *names: str) -> dict:
    for n in names:
        if a.get(n) is not None:
            d[n] = a[n]
    return d


def _ask(a):
    b: dict[str, Any] = {"task": a["task"], "budget": {"asset": a["budget_asset"], "max": str(a["budget_max"])}}
    if a.get("net"):
        b["budget"]["net"] = a["net"]
    return _opt(b, a, "due", "out")


SPECS: dict[str, tuple[str, dict, list, Callable[[dict], dict]]] = {
    # tool: (intent, properties, required, body builder)
    "post_ask": ("ask", {"task": S, "budget_asset": S, "budget_max": S, "net": S, "due": I, "out": S},
                 ["task", "budget_asset", "budget_max"], _ask),
    "post_offer": ("offer", {"ref": S, "price_asset": S, "price_amt": S, "eta": I, "rails": {"type": "array", "items": S}, "tclk": S, "note": S},
                   ["ref", "price_asset", "price_amt"],
                   lambda a: _opt({"price": {"asset": a["price_asset"], "amt": str(a["price_amt"])}}, a, "eta", "rails", "tclk", "note")),
    "post_accept": ("accept", {"ref": S, "payee": S, "contract": S, "note": S}, ["ref", "payee"],
                    lambda a: _opt({"payee": a["payee"]}, a, "contract", "note")),
    "post_claim": ("claim", {"ref": S, "deliverable": S, "note": S}, ["ref", "deliverable"],
                   lambda a: _opt({"deliverable": a["deliverable"]}, a, "note")),
    "post_receipt": ("receipt", {"ref": S, "payee": S, "outcome": S, "asset": S, "amt": S, "rail": S, "railRef": S, "contract": S, "deliverable": S, "score": I},
                     ["ref", "payee", "outcome", "asset", "amt", "rail", "railRef"],
                     lambda a: _opt({"outcome": a["outcome"], "payee": a["payee"], "amount": {"asset": a["asset"], "amt": str(a["amt"])},
                                     "rail": a["rail"], "railRef": a["railRef"]}, a, "contract", "deliverable", "score")),
    "post_attest": ("attest", {"ref": S, "verdict": S, "method": S, "evidence": S}, ["ref", "verdict", "method"],
                    lambda a: _opt({"verdict": a["verdict"], "method": a["method"]}, a, "evidence")),
    "post_review": ("review", {"ref": S, "score": I, "text": S}, ["ref", "score"], lambda a: _opt({"score": a["score"]}, a, "text")),
    "post_flag": ("flag", {"ref": S, "reason": S, "note": S}, ["ref", "reason"], lambda a: _opt({"reason": a["reason"]}, a, "note")),
    "post_resolve": ("resolve", {"ref": S, "outcome": S, "source": S, "note": S}, ["ref", "outcome", "source"],
                     lambda a: _opt({"outcome": a["outcome"], "source": a["source"]}, a, "note")),
    "post_note": ("note", {"text": S}, ["text"], lambda a: {"text": a["text"]}),
}
COMMON = {"room": S, "tags": {"type": "array", "items": S}, "lang": S}


def input_schema(tool: str) -> dict:
    _, props, req, _ = SPECS[tool]
    return {"type": "object", "properties": {**COMMON, **props}, "required": ["room", *req], "additionalProperties": False}


def make(tool: str, key: didkey.SigningKey, args: dict) -> dict:
    intent, props, req, builder = SPECS[tool]
    allowed = set(props) | set(COMMON)
    extra = set(args) - allowed
    if extra:
        raise codec.AgbError(f"unknown argument(s): {', '.join(sorted(extra))}")
    for r in ["room", *req]:
        if r not in args:
            raise codec.AgbError(f"missing argument: {r}")
    return codec.build(key, intent, args["room"], builder(args), ref=args.get("ref"),
                       k=args.get("tags") or None, lang=args.get("lang"))
