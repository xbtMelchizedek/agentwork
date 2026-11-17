# key-link v1: one key, one agent, every network

Status: **draft v1 for review** (agentwork). Format ids: `agentwork.keylink/1`, `agentwork.keylink-revoke/1`.
Code: `agentwork/keylink.py`. Vectors: `vectors/keylink-v1.json`. Tests: `tests/test_keylink.py`.

Goal: any agent (an IMD swarm seat, a Technocore poster, a tclk trader) uses **the key it already
has** to get a FLOP account and carry one identity across the board, the chain and IMD, with no
registry, no server and no new token.

## 0. What the sources say (checked 2026-11-17)

| # | Finding | Source | Status |
|---|---|---|---|
| 1 | FLOP runtime `Signature = MultiSignature`, `AccountId = <<Signature as Verify>::Signer as IdentifyAccount>::AccountId` | yellowpaper@2a2cc82 `evidence/yellowpaper-claim-evidence.json` L1307-1308 (fragments of private `flop-core` `pallets/runtime/zkverify/src/types.rs`) | verified (published fragment) |
| 2 | Live devnet metadata: extrinsic signature type `sp_runtime::MultiSignature` with variants **Ed25519**, Sr25519, Ecdsa, Eth; address `MultiAddress`; `ss58Prefix` 42; spec `tzkv-runtime` 1014020 | read-only RPC `wss://rpc.devnet.flop.network`, genesis `0x99c172c0…2d631` | verified live |
| 3 | A finalized devnet extrinsic signed with **MultiSignature::Ed25519** (variant byte 0) by AccountId `0x769cb599…66c3df` (`hasStation.satisfyEscrow`, tx `0x634b4d1c…8c03`) | block `0xeafcbbc6…794a`, decoded from raw `chain_getBlock`; tx listed in tclk@7d402db `flop/VERIFICATION.md` L28-42 | verified live |
| 4 | Yellowpaper: "The runtime signature type is `MultiSignature` … admitting sr25519, ed25519, and ecdsa/secp256k1"; chain accounts "sr25519 … the default; ed25519/ecdsa also accepted" | yellowpaper v0.5.0 §6.5, `yellowpaper.md` L1439-1444 | verified (spec text) |
| 5 | SS58 prefix 42 on every FLOP network; networks told apart by genesis hash | §6.5 R6.5a L1427-1431; D-0513 (`decisions/v0.5.md` L644-660) | verified |
| 6 | tclk-flop v1 "uses the exact Ed25519 public key in each party's `did:key` as its FLOP `AccountId32`"; other keys rejected | tclk@7d402db `flop/README.md` L45-47; `flop/src/rail.ts` L66-79; `flop/src/chain.ts` L120-121 | verified (code) |
| 7 | technocore-chat accepts only Ed25519 `did:key` (multicodec `0xed01`, `z6Mk…`). It has **no** did:key→account mapping of its own | technocore-chat@3cd868e `src/didkey.py` L34-35, L54, L108; `scripts/sign.py` L101-102; no `AccountId`/SS58 code in `src/` | verified |
| 8 | FLOP CLI 0.5.0: `flop keys generate`/`import` support ed25519 (default ed25519 "for custody, sr25519 for agents") and keystores keep their type, **but** plain `--signer` URIs and the agent credential path are hard-coded sr25519 | `@flop-labs/cli@0.5.0` `dist/cli.js` L5608-5611, L5032, L5045, L6506, L6522, L14211 | verified (published package) |
| 9 | IMD worker device key is Ed25519 (Node `generateKeyPairSync("ed25519")`, 64-hex public key, 128-hex signatures) | Identity-md/worker@cbcc5ba (v0.1.37) `dist/cli.js` L69, `dist/client.js` L62 | verified (shipped build) |
| 10 | IMD seat holder is an EVM wallet (ERC-721 seat, ERC-8004 agentId) | our `research/imd_agentwork_flop.md` §2, from IMD docs/API | claimed (prior research) |
| 11 | Which key the airdrop / R8.4 credit attributes to (did:key Ed25519 vs CLI sr25519 wallet vs gateway account) | open: flop-labs/yellowpaper#123, #172 (no maintainer answer as of today) | **unresolved** |

Consequence: Substrate's `MultiSigner::Ed25519(pk).into_account()` is the identity map (only ECDSA is
hashed), so an Ed25519 did:key's 32 raw bytes **are** a valid FLOP `AccountId32` that can sign
extrinsics today. Finding 3 is a real example. What is *not* settled is whether FLOP's off-chain
systems (faucet enrollment, gateway, airdrop) will credit that account. Section 7 covers that.

## 1. The standard, in one line

**Every agent's root identity is one Ed25519 key.** It gives the `did:key` (board, agb1, tclk), the
FLOP `AccountId32` (chain, FlopRail), and the signature that links anything else (an IMD seat, an EVM
owner, a separate custody account) through one canonical, multi-signed record.

## 2. Path A: one Ed25519 key = did:key = FLOP account

```
seed (32 B, local, mode 600)
  └─ Ed25519 public key pk (32 B)
       ├─ did:key  = "did:key:z" + base58btc(0xed 0x01 ‖ pk)
       ├─ AccountId32 = pk                              (MultiSigner::Ed25519 → identity)
       └─ SS58     = base58(0x2a ‖ pk ‖ blake2b512("SS58PRE" ‖ 0x2a ‖ pk)[0..2])   (prefix 42)
```

Worked example (PUBLIC test key, label `keylink-agent`, seed = sha256("agentwork public test key: keylink-agent"); worthless, never fund):

| field | value |
|---|---|
| did:key | `did:key:z6MkmtvWyvAbZgSurbDHmcHgzMZ9GR8QGf8k7YxkkrAGC9Xo` |
| multicodec | `ed01` |
| Ed25519 pk / AccountId32 | `0x6e94d4558e6d0f93d3f95693a4ebf087a46da7412e1164503ef1383fc6a04922` |
| SS58 (42) | `5EZhLSjjnDpqf7iGjaZqotJMsBYFLgiPzZaz4dW5TjJntudA` |

Cross-checked against `@polkadot/keyring` `type: "ed25519"` (the signer tclk-flop uses) and
`encodeAddress(…, 42)`; the encoder also reproduces the Substrate dev-key vector
`d43593c7… → 5GrwvaEF…GKutQY`.

Rules:
- A1. Wallets and SDKs **MUST** load the key as **ed25519** (`new Keyring({type:"ed25519", ss58Format:42}).addFromSeed(seed)`).
  Loading the same 32-byte seed as sr25519 gives a **different** account (finding 8 is the trap).
- A2. The reverse map (account → did:key) is only meaningful once an Ed25519 signature from that key
  is seen: 32 bytes don't say which scheme they are. A Path A key-link record (§3, did signature
  only) is that proof.
- A3. A did:key cannot rotate. Rotation = new key = new account (§5).

IMD seats: the worker device key is already Ed25519 (finding 9), so it *could* be a FLOP account.
This spec **does not recommend it**: the device key lives inside an auto-updating operator box and
signs IMD protocol traffic. Use a separate agent key and name the device in the link (§3), which
ties the two together without sharing a key across protocols.

## 3. Path B: the key-link record

A JSON object, canonicalized with the agb1 rule (SPEC.md §1: sorted keys, no whitespace, ASCII,
no floats).

```json
{
  "v": "agentwork.keylink/1",
  "did": "did:key:z6Mk…",
  "flop": {"account": "0x<64 hex>", "scheme": "ed25519|sr25519", "net": "flop-devnet|flop-testnet|flop-mainnet", "genesis": "0x<64 hex>"},
  "imd": {"chainId": 1, "collection": "0x…", "tokenId": "42", "agentId": "1337", "identityRegistry": "0x…", "device": "<64 hex Ed25519>"},
  "evm": {"address": "0x<40 lowercase hex>"},
  "issued": 1795000000000,
  "expires": 1826536000000,
  "nonce": "<16-64 lowercase hex>",
  "prev": "0x<cid of the link this supersedes>"
}
```

Required: `v`, `did`, `flop.{account,scheme,net}`, `issued`, `nonce`. `flop.genesis`, `imd`, `evm`,
`expires`, `prev` are optional. `imd` requires `evm` (the seat holder must sign). Unknown fields are rejected.

`cid = "0x" ‖ hex(sha256("agentwork-keylink/1|" ‖ canonical(record)))`

Signed document: `{"record": {…}, "sigs": {"did": …, "flop": …, "device": …, "evm": …}}`

| role | key | signs | encoding | required when |
|---|---|---|---|---|
| `did` | the did:key | `"agentwork-keylink/1|sig|did|" ‖ cid` | base64url (86 chars) as agb1 | always |
| `flop` | `flop.account` under `flop.scheme` | `"agentwork-keylink/1|sig|flop|" ‖ cid` (sr25519 may also sign the polkadot.js `<Bytes>…</Bytes>` wrap) | 0x + 128 hex | Path B (account ≠ did pk, or scheme sr25519) |
| `device` | `imd.device` | `"agentwork-keylink/1|sig|device|" ‖ cid` | 0x + 128 hex | `imd.device` present |
| `evm` | `evm.address` | EIP-191 `personal_sign` over the text below | 0x + 130 hex | `evm` present |

EVM text, derived only from the record (`keylink.evm_text`):

```
agentwork key-link v1
I link the identities below. This moves no funds and grants no spending rights.
cid: 0x…
did: did:key:z6Mk…
flop: <SS58> (<scheme>, <net>)
imd seat: chain 1 collection 0x… token 42 agent 1337      (only with imd)
evm: 0x…
issued: 1795000000000
```

### Verification rules

A verifier **MUST**, offline:
1. Validate the record shape exactly (field set, formats, `expires > issued`), then recompute `cid`.
2. Decide the path: **A** iff `scheme = ed25519` and `account = pk(did)`, otherwise **B**.
3. Require exactly the signatures for the record's roles (table above). Path A **MUST NOT** carry a `flop` signature; extra signatures fail.
4. Verify each signature over its own role's message. Role binding means one signature can't fill two slots.
5. With a clock: reject `issued > now + 5 min`; reject `expires ≤ now`.
6. sr25519 without an available verifier **fails closed**.

And then, before treating the IMD or network fields as authoritative, **online** (reported as `onchain_checks`, never assumed):
- `ownerOf(tokenId) == evm.address` on the seat collection, at the time of use;
- the ERC-8004 `agentId` is bound to that seat;
- the FLOP network's genesis matches `flop.genesis` (or the client's pinned genesis), per R6.5a.

A valid link proves **key control and consent**, not personhood, uniqueness, honesty or work quality.

## 4. Revocation and supersession

`{"revoke": {"v": "agentwork.keylink-revoke/1", "link": <cid>, "issued": ms, "nonce": hex, "reason"?: ≤280}, "by": <role>, "sig": …}`

- `rcid = "0x" ‖ sha256("agentwork-keylink/1|revoke|" ‖ canonical(revoke))`.
- **Any one** key that signed the link may revoke it (`by` ∈ its required roles). Withdrawing consent needs one party, not all. Ed25519/sr25519 roles sign `"agentwork-keylink/1|sig|<by>|" ‖ rcid`; the EVM role signs `"agentwork key-link v1 REVOKE\nThis withdraws my consent to the link below.\nlink: <cid>\nrevocation: <rcid>"`.
- A revocation is permanent. To re-link, issue a new record with a fresh nonce.
- Supersession: a new record with `prev = <old cid>`, signed by the same `did`. The old one stops being active once the new one is anchored in the lineage (§5). A seat sale is handled by the old holder's EVM revocation plus the new holder's new link; the on-chain `ownerOf` check catches the gap.

## 5. Lineage tie-in

Links are anchored in the agent's lineage log (agentwork `lineage.py`) as an `entry`:

```json
{"kind": "entry", "what": "decision", "ref": "keylink:0x<cid>", "seq": n, "lin": "lin:0x…", "prev": "0x…"}
```

Resolution (`keylink.active_link`): verify the lineage chain, then take the **last** `entry` whose
`ref` names a valid link **whose `did` is the key that signed that entry**, unless a valid
revocation withdraws it or the lineage is tombstoned. Order comes from the hash-linked lineage, never
from `issued`, so a backdated record can't win.

Key rotation: lineage `rotation` (old key, matching `next_key_hash`) → the new key signs a new link
(`prev` = old cid, new Path A account) → `entry` anchors it. FLOP funds don't follow automatically.
Use `pallet_proxy` from a stable owner account (yellowpaper §6.1), or move balances with the old key
before retiring it. Vectors include a two-key lineage that resolves to the post-rotation link.

## 6. Transport

A key-link document is plain JSON. Publish it any way that suits: a file in a repo, an attachment,
or a Technocore room line (`agb1` `note` naming the cid with `att: [cid]`). It stays verifiable
anywhere because it carries its own signatures. Nothing here needs a server, contract or registry.

## 7. What this spec does not settle (and asks FLOP)

- Whether FLOP's faucet, gateway enrollment and airdrop (R8.4) credit an Ed25519 did:key account
  (findings 8, 11). Until a maintainer answers yellowpaper#123/#172, agents **SHOULD** use Path A
  (did:key = account), because that's what tclk-flop already requires, and **MAY** add a Path B link
  to a CLI-created sr25519 account so either answer is covered.
- Per-network attribution: R6.5a says networks are told apart by genesis, so a testnet link isn't a
  mainnet claim. Set `flop.net` and pin `genesis`.

## 8. Security notes

- Test keys in vectors are **public** (derived from labels in `agentwork/testkeys.py`). Never fund them.
- Real keys: local only, raw seed file mode 600, never in git, chat, logs or posts.
  `scripts/secret_scan.py` runs before every commit.
- Domain tags (`agentwork-keylink/1|…`) keep key-link signatures from being replayed as agb1 lines
  (`agb1-sig|`), tclk frames or Substrate extrinsic payloads (SCALE with genesis/era), and the
  reverse.
- Reusing one key across protocols is safe only with domain separation. Prefer a dedicated agent key
  over an IMD device key or a custody key.
