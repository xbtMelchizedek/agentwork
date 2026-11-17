"""IMD (identity.md) adapter: read-only, no signing, no payments, no network.

Two directions:

* IMD -> agentwork: `observe_job()` turns a saved public IMD job document
  (`GET https://api.imd.fun/jobs/:id`, optionally `/result` and `/records`) into an
  *observed* record. Observed records are NOT agb1 messages: nobody signed them, so
  they are rendered with an ``external: imd`` badge and never enter reputation.
* agentwork -> IMD: `receipt_proof()` packages a signed agb1 receipt thread
  (ask -> offer -> accept -> claim -> receipt -> attest) as a JSON document whose shape
  mirrors IMD's work record (`/work-records/:hash.json`): nodes, reviews keyed by
  ERC-8004 agentId, and a document hash. IMD does not ingest this today; it is a
  proposal format (`agentwork.imd-proof/0`) for IMD maintainers and indexers.

Also: `did_from_device_key()` (an IMD worker's device key is an Ed25519 public key,
64 hex; the same bytes form a `did:key`) and `binding_statement()`, an UNSIGNED text a
seat holder can choose to sign with their own wallet to link a seat to a did:key.
This module never signs, fetches URLs found in documents, or contacts IMD.

Constants come from https://imd.fun/docs/ (Ethereum mainnet, checked Nov 17 2026).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import codec, didkey, safe

FORMAT_OBSERVED = "agentwork.imd-observed/0"
FORMAT_PROOF = "agentwork.imd-proof/0"
FORMAT_BINDING = "agentwork.imd-binding/0"
ROOM = "ext/imd/jobs"

CHAIN_ID = 1
SEAT_COLLECTION = "0x0000ec93127baa929e58e97dd0095a2bfb38ec1d"
WORK_REGISTRY = "0xb6d0a187b050fa5bb0b87033a203f37becf4a775"
ERC8004_IDENTITY = "0x8004a169fb4a3325136eb29fa0ceb6d2e539a432"
ERC8004_REPUTATION = "0x8004baa17c55a88189ae136b182e5fda19de9b63"
JOB_PRICE = {"asset": "IMD", "amt": "500000000000000000", "note": "documented list price, 0.5 IMD; not read from chain"}

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TX = re.compile(r"^0x[0-9a-fA-F]{64}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_DEC = re.compile(r"^[0-9]{1,20}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_WORD = re.compile(r"^[a-z][a-z0-9_:.-]{0,63}$")

# IMD job state -> board status (observed; IMD's own verifier decided it, not agb1)
STATUS = {"completed": "completed", "blocked": "blocked", "cancelled": "cancelled", "superseded": "superseded",
          "failed": "failed", "queued": "open", "planning": "open", "running": "in progress", "waiting": "open"}


class ImdError(ValueError):
    pass


def _s(v, pat: re.Pattern) -> str | None:
    return v if isinstance(v, str) and pat.match(v) else None


def _ms(iso) -> int | None:
    if not isinstance(iso, str):
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def did_from_device_key(device_key: str) -> str:
    """IMD device keys are Ed25519 public keys as 64 lowercase hex; map to did:key."""
    if not _s(device_key, _HEX64):
        raise ImdError("device key must be 64 lowercase hex")
    return didkey.did_from_public(bytes.fromhex(device_key))


def _seat(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    tok, agent = _s(v.get("tokenId"), _DEC), _s(v.get("agentId"), _DEC)
    return {"tokenId": tok, "agentId": agent} if tok or agent else None


def observe_job(job: dict, result: dict | None = None, records: dict | None = None) -> dict:
    """Saved public IMD job JSON -> one observed record (unsigned, weight 0)."""
    if not isinstance(job, dict) or not _s(job.get("id"), _UUID):
        raise ImdError("not an IMD job document (missing/invalid id)")
    state = job.get("state") if isinstance(job.get("state"), str) else "unknown"
    rec: dict = {
        "format": FORMAT_OBSERVED, "external": "imd", "signed": False, "weight": 0,
        "room": ROOM, "t": "ask", "id": "imd:job:" + job["id"],
        "ts": _ms(job.get("createdAt")), "updated": _ms(job.get("updatedAt")),
        "state": safe.sanitize(state, 32), "status": STATUS.get(state, "unknown"),
        "template": safe.sanitize(job["template"], 64) if isinstance(job.get("template"), str) else None,
        "task": safe.sanitize(job.get("objective") or "", 280),
        "payer": (_s(job.get("paidBy"), _ADDR) or "").lower() or None,
        "blocked": safe.sanitize(job["blockedReason"], 200) if isinstance(job.get("blockedReason"), str) else None,
        "parent": "imd:job:" + job["parentJobId"] if _s(job.get("parentJobId"), _UUID) else None,
    }
    rec["price"] = JOB_PRICE if rec["payer"] else None
    nodes = []
    for n in job.get("nodes") or []:
        if not isinstance(n, dict) or not _s(n.get("key"), _KEY):
            continue
        v = n.get("verdict") if isinstance(n.get("verdict"), dict) else {}
        nodes.append({
            "key": n["key"], "role": _s(n.get("role"), _WORD), "state": _s(n.get("state"), _WORD),
            "seat": _seat(n.get("seat")), "verdict": _s(v.get("status"), _WORD),
            "evaluation": _s(v.get("evaluation"), _WORD), "rejection": _s(v.get("rejectionCode"), _WORD),
            "treeHash": _s(v.get("verifiedTreeHash"), _HEX40),
        })
    rec["nodes"] = nodes
    reviews = []
    for r in job.get("reviews") or []:
        if not isinstance(r, dict) or not _s(r.get("txHash"), _TX):
            continue
        for e in r.get("entries") or []:
            if isinstance(e, dict) and _s(e.get("agentId"), _DEC) and e.get("value") in (0, 1):
                reviews.append({"agentId": e["agentId"], "node": _s(e.get("nodeKey"), _KEY), "value": e["value"],
                                "role": _s(e.get("role"), _WORD), "chainId": r.get("chainId") if isinstance(r.get("chainId"), int) else None,
                                "txHash": r["txHash"].lower()})
    rec["reviews"] = reviews
    recs = []
    for r in (records or {}).get("records") or []:
        if isinstance(r, dict) and _s(r.get("txHash"), _TX) and _s(r.get("hash"), _HEX64):
            recs.append({"workRecordHash": r["hash"], "registry": (_s(r.get("registry"), _ADDR) or "").lower() or None,
                         "chainId": r.get("chainId") if isinstance(r.get("chainId"), int) else None,
                         "txHash": r["txHash"].lower(), "status": _s(r.get("status"), _WORD)})
    rec["workRecords"] = recs
    d = (result or {}).get("delivery") or job.get("delivery") or {}
    commit = _s(d.get("commit"), _HEX40) if isinstance(d, dict) else None
    # Locators are kept as defanged text for humans; they are never fetched.
    repo = safe.sanitize(d["repoUrl"], 200) if isinstance(d, dict) and isinstance(d.get("repoUrl"), str) else None
    rec["delivery"] = {"repo": repo, "commit": commit} if (repo or commit) else None
    rec["accepted"] = sum(1 for n in nodes if n["verdict"] == "accepted" or n["state"] == "accepted")
    return rec


def observe_list(listing: dict) -> list[dict]:
    """`GET /jobs` page -> observed stubs (no nodes or reviews)."""
    out = []
    for j in (listing or {}).get("jobs") or []:
        try:
            out.append(observe_job(j))
        except ImdError:
            continue
    return out


def load_observed(paths: Iterable[Path]) -> list[dict]:
    """Read saved IMD JSON files (job docs or /jobs pages). Pairs `<x>_result.json` / `<x>_records.json`."""
    out = []
    for p in map(Path, paths):
        if p.stem.endswith(("_result", "_records")):
            continue
        doc = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("jobs"), list):
            out += observe_list(doc)
            continue
        extra = {}
        for suf in ("result", "records"):
            q = p.with_name(f"{p.stem}_{suf}.json")
            extra[suf] = json.loads(q.read_text(encoding="utf-8")) if q.exists() else None
        out.append(observe_job(doc, extra["result"], extra["records"]))
    return out


def observed_md(records: list[dict], tz: str = "UTC") -> str:
    from .render import when  # local import keeps render independent of this module
    lines = ["## IMD swarm jobs `external: imd`",
             "_Observed from public IMD job documents. Unsigned by the parties, so they never count toward agb1 "
             "reputation. Status is IMD's own verifier verdict. Text is untrusted; links are defanged._", ""]
    for r in sorted(records, key=lambda r: -(r["ts"] or 0)):
        t = when(r["ts"], tz) if r["ts"] else "unknown time"
        paid = "paid 0.5 IMD (list price)" if r["payer"] else "no payer recorded"
        lines.append(f"- **{r['status'].upper()}** `external: imd` · {t} · {r['template'] or 'job'} · \"{r['task']}\"  ")
        lines.append(f"  {paid} · {len(r['nodes'])} node(s), {r['accepted']} accepted · "
                     f"{len(r['reviews'])} ERC-8004 review(s) · id `{r['id']}`")
        for n in r["nodes"]:
            seat = f"seat #{n['seat']['tokenId']} (agent {n['seat']['agentId']})" if n["seat"] else "unassigned"
            lines.append(f"  - {n['key']} ({n['role']}): {n['state']} · {seat}")
    return "\n".join(lines) + "\n"


# ── agentwork -> IMD-shaped proof ────────────────────────────────────────────
def _thread(receipt: dict, by_cid: dict) -> dict:
    """Walk refs up from the receipt: claim, then (accept, offer) if present, then the ask."""
    out = {"ask": None, "offer": None, "accept": None, "claim": None}
    cur = by_cid.get(receipt.get("ref"))
    for _ in range(4):
        if not cur or cur["t"] not in out or out[cur["t"]] is not None:
            break
        out[cur["t"]] = cur
        if cur["t"] == "ask":
            break
        cur = by_cid.get(cur.get("ref"))
    return out


def receipt_proof(receipt_cid: str, messages: Iterable[dict], *, agent_ids: dict | None = None) -> dict:
    """Package one agb1 receipt thread as an IMD-work-record-shaped JSON (no signing).

    `agent_ids` optionally maps did:key -> ERC-8004 agentId (decimal string) from a binding
    the seat holder signed. Without it, reviews carry agentId None.
    """
    by_cid = {}
    for m in messages:
        try:
            codec.validate(m)   # signatures checked; anything unsigned or altered is ignored
        except codec.AgbError:
            continue
        by_cid.setdefault(m["cid"], m)
    r = by_cid.get(receipt_cid)
    if not r or r["t"] != "receipt":
        raise ImdError("receipt not found among valid signed messages")
    th = _thread(r, by_cid)
    claim, ask = th["claim"], th["ask"]
    linked = bool(claim and claim["t"] == "claim" and claim["from"] == r["b"]["payee"]
                  and ask and ask["t"] == "ask" and ask["from"] == r["from"])
    attests = sorted((m for m in by_cid.values() if m["t"] == "attest" and m.get("ref") == receipt_cid),
                     key=lambda m: (m["ts"], m["cid"]))
    third = [a for a in attests if a["from"] not in (r["from"], r["b"]["payee"])]
    valid = [a for a in third if a["b"]["verdict"] == "valid"]
    invalid = [a for a in third if a["b"]["verdict"] == "invalid"]
    ids = agent_ids or {}
    worker_ok = r["b"]["outcome"] == "settled" and linked and len(valid) > len(invalid)
    doc = {
        "format": FORMAT_PROOF,
        "interpretation": ("Signed agb1 work thread. Settlement is as claimed by the payer until the rail reference is "
                           "checked on the rail itself; attestations are third-party opinions, not proofs of correctness."),
        "source": "agb1",
        "jobRef": ask["cid"] if ask else None,
        "kind": ("agb1:" + ask["b"]["out"]) if ask and ask["b"].get("out") else "agb1:ask",
        "threadLinked": linked,
        "settlement": {"outcome": r["b"]["outcome"], "amount": r["b"]["amount"], "rail": r["b"]["rail"],
                       "railRef": r["b"]["railRef"], "contract": r["b"].get("contract"), "verifiedOnRail": None},
        "nodes": [{"key": "work", "role": "implement", "did": r["b"]["payee"], "agentId": ids.get(r["b"]["payee"]),
                   "claim": claim["cid"] if claim else None,
                   "deliverable": safe.sanitize(claim["b"]["deliverable"], 256) if claim else None,
                   "state": "accepted" if worker_ok else "unverified"}],
        "reviews": ([{"node": "work", "did": r["b"]["payee"], "agentId": ids.get(r["b"]["payee"]),
                      "value": 1 if worker_ok else 0, "role": "agb1:receipt+attest"}]
                    + [{"node": "work", "did": a["from"], "agentId": ids.get(a["from"]),
                        "value": 1 if a["b"]["verdict"] == "valid" else 0,
                        "role": "agb1:attest:" + safe.sanitize(a["b"]["method"], 48)} for a in third]),
        "lines": [codec.encode(m) for m in (ask, th["offer"], th["accept"], claim, r, *attests) if m],
    }
    doc["documentHash"] = "sha256:" + hashlib.sha256(codec.canonical(doc).encode()).hexdigest()
    return doc


# ── seat <-> did:key binding (UNSIGNED template; the seat holder signs, never us) ──
def binding_statement(*, token_id: str, agent_id: str, wallet: str, did: str, issued_at_ms: int, nonce: str) -> str:
    """Plain text for an EIP-191 personal_sign by the seat holder's wallet. Returns text only."""
    if not _s(token_id, _DEC) or not _s(agent_id, _DEC):
        raise ImdError("tokenId and agentId must be decimal strings")
    if not _s(wallet, _ADDR):
        raise ImdError("wallet must be a 0x address")
    if not didkey.is_did(did):
        raise ImdError("did must be an Ed25519 did:key")
    if not re.fullmatch(r"[0-9a-f]{16,64}", nonce or ""):
        raise ImdError("nonce must be 16-64 lowercase hex")
    fields = [("format", FORMAT_BINDING), ("chainId", str(CHAIN_ID)), ("collection", SEAT_COLLECTION),
              ("tokenId", token_id), ("agentId", agent_id), ("identityRegistry", ERC8004_IDENTITY),
              ("wallet", wallet.lower()), ("did", did), ("issuedAt", str(int(issued_at_ms))), ("nonce", nonce)]
    head = ("I hold this identity.md seat and link it to the did:key below for agentwork receipts. "
            "This moves no funds and grants no spending rights.")
    return head + "\n" + "\n".join(f"{k}: {v}" for k, v in fields)


def parse_binding_statement(text: str) -> dict:
    lines = (text or "").split("\n")
    out = {}
    for ln in lines[1:]:
        k, sep, v = ln.partition(": ")
        if not sep or k in out:
            raise ImdError("malformed binding statement")
        out[k] = v
    if out.get("format") != FORMAT_BINDING:
        raise ImdError("not an agentwork IMD binding statement")
    # round-trip: only the exact canonical text is accepted
    again = binding_statement(token_id=out["tokenId"], agent_id=out["agentId"], wallet=out["wallet"], did=out["did"],
                              issued_at_ms=int(out["issuedAt"]), nonce=out["nonce"])
    if again != text:
        raise ImdError("binding statement is not in canonical form")
    return out
