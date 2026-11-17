"""key-link v1: one agent identity across did:key, a FLOP account, an IMD seat and an EVM owner.

Spec: docs/KEYLINK.md. Offline only: this module never touches a network, a chain or funds.
It builds and verifies records; signing helpers exist for tests and local test keys.

Path A  one Ed25519 key -> did:key and FLOP AccountId32 (the raw public key, SS58 prefix 42).
Path B  a canonical link record signed by every key it names (dual-signed at minimum).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import codec, didkey, lineage

FORMAT = "agentwork.keylink/1"
FORMAT_REVOKE = "agentwork.keylink-revoke/1"
DOMAIN = b"agentwork-keylink/1|"
SS58_PREFIX = 42  # every FLOP network (yellowpaper v0.5.0 R6.5a, D-0513)
NETS = ("flop-devnet", "flop-testnet", "flop-mainnet")
SCHEMES = ("ed25519", "sr25519")
ROLES = ("did", "flop", "device", "evm")
CLOCK_SKEW_MS = 300_000
LINEAGE_REF = "keylink:"

_HEX32 = re.compile(r"^0x[0-9a-f]{64}$")
_RAWHEX32 = re.compile(r"^[0-9a-f]{64}$")
_HEX64 = re.compile(r"^0x[0-9a-f]{128}$")
_RAWHEX64 = re.compile(r"^[0-9a-f]{128}$")
_EVMSIG = re.compile(r"^0x[0-9a-f]{130}$")
_ADDR = re.compile(r"^0x[0-9a-f]{40}$")
_DEC = re.compile(r"^(0|[1-9][0-9]{0,77})$")
_NONCE = re.compile(r"^[0-9a-f]{16,64}$")


class KeylinkError(ValueError):
    pass


# ── Path A: did:key <-> AccountId32 <-> SS58 ────────────────────────────────

def _ss58_checksum(payload: bytes) -> bytes:
    return hashlib.blake2b(b"SS58PRE" + payload, digest_size=64).digest()[:2]


def ss58_encode(account: bytes, prefix: int = SS58_PREFIX) -> str:
    if len(account) != 32:
        raise KeylinkError("AccountId32 must be 32 bytes")
    if not 0 <= prefix < 64:
        raise KeylinkError("only simple (one-byte) SS58 prefixes are supported")
    payload = bytes([prefix]) + account
    return didkey.b58encode(payload + _ss58_checksum(payload))


def ss58_decode(address: str, prefix: int = SS58_PREFIX) -> bytes:
    try:
        raw = didkey.b58decode(address)
    except Exception as e:  # noqa: BLE001
        raise KeylinkError(f"bad SS58: {e}") from None
    if len(raw) != 35 or raw[0] != prefix:
        raise KeylinkError(f"not a prefix-{prefix} SS58 AccountId32")
    if _ss58_checksum(raw[:33]) != raw[33:]:
        raise KeylinkError("SS58 checksum mismatch")
    return raw[1:33]


def account_from_did(did: str) -> str:
    """Path A: the did:key's raw Ed25519 public key IS the AccountId32 (MultiSigner::Ed25519 -> identity)."""
    return "0x" + didkey.public_key(did).hex()


def ss58_from_did(did: str) -> str:
    return ss58_encode(didkey.public_key(did))


def did_from_account(account: str) -> str:
    """Reverse map. CAUTION: 32 bytes do not say which scheme they are; only an Ed25519 signature
    (a valid key-link record) proves that an account is an Ed25519 key and so this did:key."""
    acct = _account_bytes(account)
    return didkey.did_from_public(acct)


def _account_bytes(account: str) -> bytes:
    if isinstance(account, str) and _HEX32.fullmatch(account):
        return bytes.fromhex(account[2:])
    if isinstance(account, str):
        return ss58_decode(account)
    raise KeylinkError("account must be 0x-hex or SS58")


# ── record ──────────────────────────────────────────────────────────────────

def _req(cond: bool, why: str) -> None:
    if not cond:
        raise KeylinkError(why)


def check_record(r: Any) -> dict:
    _req(isinstance(r, dict), "record must be an object")
    allowed = {"v", "did", "flop", "imd", "evm", "issued", "expires", "nonce", "prev"}
    _req(set(r) <= allowed, f"unknown record fields: {sorted(set(r) - allowed)}")
    _req(r.get("v") == FORMAT, f"v must be {FORMAT}")
    _req(didkey.is_did(r.get("did")), "did must be an Ed25519 did:key")
    f = r.get("flop")
    _req(isinstance(f, dict) and set(f) <= {"account", "scheme", "net", "genesis"}, "flop must be an object")
    _req(isinstance(f.get("account"), str) and bool(_HEX32.fullmatch(f["account"])), "flop.account must be 0x + 64 lowercase hex")
    _req(f.get("scheme") in SCHEMES, "flop.scheme must be ed25519 or sr25519")
    _req(f.get("net") in NETS, f"flop.net must be one of {NETS}")
    _req("genesis" not in f or bool(_HEX32.fullmatch(str(f["genesis"]))), "flop.genesis must be 0x + 64 hex")
    if "imd" in r:
        m = r["imd"]
        _req(isinstance(m, dict), "imd must be an object")
        need = {"chainId", "collection", "tokenId", "agentId", "identityRegistry"}
        _req(need <= set(m) <= need | {"device"}, "imd needs chainId, collection, tokenId, agentId, identityRegistry (+ optional device)")
        _req(isinstance(m["chainId"], int) and not isinstance(m["chainId"], bool) and 0 < m["chainId"] < 2**53, "imd.chainId must be a positive integer")
        _req(bool(_ADDR.fullmatch(str(m["collection"]))) and bool(_ADDR.fullmatch(str(m["identityRegistry"]))), "imd addresses must be lowercase 0x + 40 hex")
        _req(bool(_DEC.fullmatch(str(m["tokenId"]))) and bool(_DEC.fullmatch(str(m["agentId"]))), "imd tokenId/agentId must be decimal strings")
        _req("device" not in m or bool(_RAWHEX32.fullmatch(str(m["device"]))), "imd.device must be 64 lowercase hex (Ed25519 public key)")
        _req("evm" in r, "an imd seat link needs the seat holder's evm signature")
    if "evm" in r:
        e = r["evm"]
        _req(isinstance(e, dict) and set(e) == {"address"} and bool(_ADDR.fullmatch(str(e["address"]))), "evm must be {address: lowercase 0x + 40 hex}")
    _req(isinstance(r.get("issued"), int) and 0 < r["issued"] <= codec.MAX_SAFE_INT, "issued must be Unix ms")
    if "expires" in r:
        _req(isinstance(r["expires"], int) and r["issued"] < r["expires"] <= codec.MAX_SAFE_INT, "expires must be > issued")
    _req(isinstance(r.get("nonce"), str) and bool(_NONCE.fullmatch(r["nonce"])), "nonce must be 16-64 lowercase hex")
    _req("prev" not in r or bool(_HEX32.fullmatch(str(r["prev"]))), "prev must be a key-link cid")
    codec.canonical(r)
    return r


def record_cid(r: dict) -> str:
    return "0x" + hashlib.sha256(DOMAIN + codec.canonical(r).encode("ascii")).hexdigest()


def path_of(r: dict) -> str:
    f = r["flop"]
    return "A" if f["scheme"] == "ed25519" and f["account"] == account_from_did(r["did"]) else "B"


def required_roles(r: dict) -> list[str]:
    roles = ["did"]
    if path_of(r) == "B":
        roles.append("flop")
    if "imd" in r and "device" in r["imd"]:
        roles.append("device")
    if "evm" in r:
        roles.append("evm")
    return roles


def sig_message(role: str, cid: str) -> bytes:
    """Bytes an Ed25519/sr25519 key signs. The role is bound so one signature can't fill two slots."""
    if role not in ("did", "flop", "device"):
        raise KeylinkError("role signs EIP-191 text, not raw bytes" if role == "evm" else "unknown role")
    return DOMAIN + b"sig|" + role.encode() + b"|" + cid.encode("ascii")


def evm_text(r: dict, cid: str) -> str:
    """EIP-191 personal_sign text for the EVM owner. Derived from the record; never free-form."""
    lines = ["agentwork key-link v1",
             "I link the identities below. This moves no funds and grants no spending rights.",
             f"cid: {cid}", f"did: {r['did']}",
             f"flop: {ss58_encode(bytes.fromhex(r['flop']['account'][2:]))} ({r['flop']['scheme']}, {r['flop']['net']})"]
    if "imd" in r:
        m = r["imd"]
        lines.append(f"imd seat: chain {m['chainId']} collection {m['collection']} token {m['tokenId']} agent {m['agentId']}")
    lines += [f"evm: {r['evm']['address']}", f"issued: {r['issued']}"]
    return "\n".join(lines)


# ── signature checks ────────────────────────────────────────────────────────

def _ed25519_ok(pub: bytes, sig: bytes, msg: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
        return True
    except (InvalidSignature, ValueError):
        return False


def _sr25519_ok(pub: bytes, sig: bytes, msg: bytes) -> bool:
    try:
        import sr25519  # optional: py-sr25519-bindings
    except ImportError as e:
        raise KeylinkError("sr25519 verifier unavailable (pip install py-sr25519-bindings); failing closed") from e
    # polkadot.js signRaw wraps payloads in <Bytes>...</Bytes>; accept both spellings.
    return any(sr25519.verify(sig, m, pub) for m in (msg, b"<Bytes>" + msg + b"</Bytes>"))


def _evm_recover(text: str, sig_hex: str) -> str:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    return Account.recover_message(encode_defunct(text=text), signature=sig_hex).lower()


@dataclass
class Result:
    ok: bool
    path: str | None = None
    cid: str | None = None
    did: str | None = None
    flop_ss58: str | None = None
    errors: list = field(default_factory=list)
    onchain_checks: list = field(default_factory=list)

    def to_json(self) -> dict:
        return self.__dict__.copy()


def _check_sig(role: str, r: dict, cid: str, sig: Any) -> str | None:
    if role == "did":
        ok = isinstance(sig, str) and didkey.verify(r["did"], sig, sig_message("did", cid))
    elif role == "device":
        ok = isinstance(sig, str) and bool(_HEX64.fullmatch(sig)) and _ed25519_ok(
            bytes.fromhex(r["imd"]["device"]), bytes.fromhex(sig[2:]), sig_message("device", cid))
    elif role == "flop":
        if not (isinstance(sig, str) and _HEX64.fullmatch(sig)):
            return "flop signature must be 0x + 128 hex"
        pub, raw = bytes.fromhex(r["flop"]["account"][2:]), bytes.fromhex(sig[2:])
        ok = _ed25519_ok(pub, raw, sig_message("flop", cid)) if r["flop"]["scheme"] == "ed25519" \
            else _sr25519_ok(pub, raw, sig_message("flop", cid))
    else:
        if not (isinstance(sig, str) and _EVMSIG.fullmatch(sig)):
            return "evm signature must be 0x + 130 hex"
        try:
            ok = _evm_recover(evm_text(r, cid), sig) == r["evm"]["address"]
        except Exception:  # noqa: BLE001
            ok = False
    return None if ok else f"{role} signature does not verify"


def verify(doc: Any, now_ms: int | None = None) -> Result:
    """Verify a signed key-link document {"record": ..., "sigs": {...}} offline."""
    res = Result(ok=False)
    try:
        _req(isinstance(doc, dict) and set(doc) == {"record", "sigs"}, "document must be {record, sigs}")
        r = check_record(doc["record"])
        sigs = doc["sigs"]
        _req(isinstance(sigs, dict) and set(sigs) <= set(ROLES), "sigs keys must be roles: did, flop, device, evm")
    except (KeylinkError, codec.AgbError) as e:
        res.errors.append(str(e))
        return res
    res.cid, res.path, res.did = record_cid(r), path_of(r), r["did"]
    res.flop_ss58 = ss58_encode(bytes.fromhex(r["flop"]["account"][2:]))
    need = required_roles(r)
    extra = set(sigs) - set(need)
    if extra:
        res.errors.append(f"unexpected signatures: {sorted(extra)} (Path A carries no separate flop signature)")
    for role in need:
        if role not in sigs:
            res.errors.append(f"missing {role} signature")
            continue
        try:
            err = _check_sig(role, r, res.cid, sigs[role])
        except KeylinkError as e:
            err = str(e)
        if err:
            res.errors.append(err)
    if now_ms is not None:
        if r["issued"] > now_ms + CLOCK_SKEW_MS:
            res.errors.append("issued is in the future")
        if "expires" in r and r["expires"] <= now_ms:
            res.errors.append("link has expired")
    if "imd" in r:
        m = r["imd"]
        res.onchain_checks += [
            f"eip155:{m['chainId']} ERC-721 {m['collection']}.ownerOf({m['tokenId']}) == {r['evm']['address']}",
            f"eip155:{m['chainId']} ERC-8004 {m['identityRegistry']} agentId {m['agentId']} is bound to seat tokenId {m['tokenId']}",
        ]
    res.onchain_checks.append(f"{r['flop']['net']}: genesis hash matches {r['flop'].get('genesis', 'the pinned network genesis')}")
    res.ok = not res.errors
    return res


# ── revocation ──────────────────────────────────────────────────────────────

def check_revocation(rv: Any) -> dict:
    _req(isinstance(rv, dict) and set(rv) <= {"v", "link", "issued", "nonce", "reason"}, "bad revocation fields")
    _req(rv.get("v") == FORMAT_REVOKE, f"v must be {FORMAT_REVOKE}")
    _req(bool(_HEX32.fullmatch(str(rv.get("link")))), "link must be a key-link cid")
    _req(isinstance(rv.get("issued"), int) and 0 < rv["issued"] <= codec.MAX_SAFE_INT, "issued must be Unix ms")
    _req(isinstance(rv.get("nonce"), str) and bool(_NONCE.fullmatch(rv["nonce"])), "nonce must be 16-64 lowercase hex")
    _req("reason" not in rv or (isinstance(rv["reason"], str) and 0 < len(rv["reason"]) <= 280), "reason is 1-280 chars")
    codec.canonical(rv)
    return rv


def revocation_cid(rv: dict) -> str:
    return "0x" + hashlib.sha256(DOMAIN + b"revoke|" + codec.canonical(rv).encode("ascii")).hexdigest()


def revoke_evm_text(rv: dict, rcid: str) -> str:
    return f"agentwork key-link v1 REVOKE\nThis withdraws my consent to the link below.\nlink: {rv['link']}\nrevocation: {rcid}"


def verify_revocation(rdoc: Any, link_doc: dict) -> Result:
    """{"revoke": {...}, "by": role, "sig": ...}. Any one key named in the link may revoke it."""
    res = Result(ok=False)
    try:
        _req(isinstance(rdoc, dict) and set(rdoc) == {"revoke", "by", "sig"}, "revocation must be {revoke, by, sig}")
        rv = check_revocation(rdoc["revoke"])
        r = check_record(link_doc["record"])
        _req(rv["link"] == record_cid(r), "revocation names a different link")
        _req(rdoc["by"] in required_roles(r), "revoker is not a key named in the link")
    except (KeylinkError, codec.AgbError, KeyError, TypeError) as e:
        res.errors.append(str(e))
        return res
    res.cid, res.did = revocation_cid(rv), r["did"]
    by, sig = rdoc["by"], rdoc["sig"]
    msg = DOMAIN + b"sig|" + by.encode() + b"|" + res.cid.encode("ascii")
    try:
        if by == "did":
            ok = isinstance(sig, str) and didkey.verify(r["did"], sig, msg)
        elif by == "evm":
            ok = isinstance(sig, str) and bool(_EVMSIG.fullmatch(sig)) and _evm_recover(revoke_evm_text(rv, res.cid), sig) == r["evm"]["address"]
        else:
            pub = bytes.fromhex(r["imd"]["device"]) if by == "device" else bytes.fromhex(r["flop"]["account"][2:])
            ok = isinstance(sig, str) and bool(_HEX64.fullmatch(sig)) and (
                _sr25519_ok(pub, bytes.fromhex(sig[2:]), msg) if by == "flop" and r["flop"]["scheme"] == "sr25519"
                else _ed25519_ok(pub, bytes.fromhex(sig[2:]), msg))
    except Exception as e:  # noqa: BLE001
        res.errors.append(str(e))
        return res
    if not ok:
        res.errors.append(f"{by} revocation signature does not verify")
    res.ok = not res.errors
    return res


# ── resolution (lineage tie-in) ─────────────────────────────────────────────

def lineage_ref(cid: str) -> str:
    return LINEAGE_REF + cid


def active_link(lineage_lines: list[str], links: list[dict], revocations: list[dict] = (), now_ms: int | None = None) -> dict:
    """The lineage's current link: the LAST lineage `entry` with ref "keylink:<cid>" whose link verifies,
    whose did is the key that signed that lineage entry, and that no valid revocation withdraws.
    Order comes from the hash-linked lineage, never from record timestamps."""
    rep = lineage.verify_chain(lineage_lines)
    if not rep.ok:
        return {"ok": False, "error": "lineage does not verify: " + "; ".join(rep.errors)}
    by_cid = {}
    for d in links:
        v = verify(d, now_ms)
        if v.ok:
            by_cid[v.cid] = (d, v)
    revoked = {d["revoke"]["link"] for d in revocations
               if d.get("revoke", {}).get("link") in by_cid and verify_revocation(d, by_cid[d["revoke"]["link"]][0]).ok}
    current = None
    for line in (l for l in lineage_lines if l.strip()):
        env = codec.decode(line)
        b = env["b"]
        if b.get("kind") == "entry" and str(b.get("ref", "")).startswith(LINEAGE_REF):
            cid = b["ref"][len(LINEAGE_REF):]
            if cid in by_cid and by_cid[cid][1].did == env["from"]:
                current = cid
    if rep.tombstoned:
        return {"ok": True, "lin": rep.lin, "active": None, "reason": "lineage tombstoned"}
    if current is None or current in revoked:
        return {"ok": True, "lin": rep.lin, "active": None, "reason": "revoked" if current else "no link in lineage"}
    v = by_cid[current][1]
    return {"ok": True, "lin": rep.lin, "active": current, "path": v.path, "did": v.did, "flop": v.flop_ss58}


# ── test-only signing helpers (local keys; nothing leaves the process) ──────

def sign_ed25519_hex(key: didkey.SigningKey, msg: bytes) -> str:
    return "0x" + didkey.b64u_decode(key.sign(msg)).hex()


def make_doc(record: dict, *, did_key: didkey.SigningKey, flop_key: didkey.SigningKey | None = None,
             device_key: didkey.SigningKey | None = None, evm_account: Any = None) -> dict:
    r = check_record(record)
    cid = record_cid(r)
    sigs = {"did": did_key.sign(sig_message("did", cid))}
    if flop_key is not None:
        sigs["flop"] = sign_ed25519_hex(flop_key, sig_message("flop", cid))
    if device_key is not None:
        sigs["device"] = sign_ed25519_hex(device_key, sig_message("device", cid))
    if evm_account is not None:
        from eth_account.messages import encode_defunct
        sigs["evm"] = "0x" + evm_account.sign_message(encode_defunct(text=evm_text(r, cid))).signature.hex().removeprefix("0x")
    return {"record": r, "sigs": sigs}


def make_revocation(link_doc: dict, *, by: str, key: Any, issued: int, nonce: str, reason: str | None = None) -> dict:
    rv = {"v": FORMAT_REVOKE, "link": record_cid(link_doc["record"]), "issued": issued, "nonce": nonce}
    if reason:
        rv["reason"] = reason
    rcid = revocation_cid(check_revocation(rv))
    msg = DOMAIN + b"sig|" + by.encode() + b"|" + rcid.encode("ascii")
    if by == "did":
        sig = key.sign(msg)
    elif by == "evm":
        from eth_account.messages import encode_defunct
        sig = "0x" + key.sign_message(encode_defunct(text=revoke_evm_text(rv, rcid))).signature.hex().removeprefix("0x")
    else:
        sig = sign_ed25519_hex(key, msg)
    return {"revoke": rv, "by": by, "sig": sig}
