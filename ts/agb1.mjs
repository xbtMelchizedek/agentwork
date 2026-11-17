// agb1 codec for Node >= 18. Zero dependencies (node:crypto only).
// Mirrors agentwork/codec.py; both are checked against vectors/agb1-v1.json.
import { readFileSync } from "node:fs";
import { createHash, createPrivateKey, createPublicKey, sign as edSign, verify as edVerify, randomBytes } from "node:crypto";

export const PREFIX = "agb1 ";
const DOMAIN = "FLOP::agb::v1|";
const SIG_DOMAIN = "agb1-sig|";
const MAX_WIRE = 4096;
const MAX_SAFE = Number.MAX_SAFE_INTEGER;
export const SOCIAL_INTENTS = ["handle", "friend_request", "friend_accept", "unfriend", "guild_create", "guild_invite", "guild_join", "guild_leave", "guild_role"];
export const INTENTS = ["ask", "offer", "accept", "claim", "attest", "receipt", "review", "flag", "lineage", "resolve", "note", ...SOCIAL_INTENTS];
const NEEDS_REF = new Set(["offer", "accept", "claim", "attest", "receipt", "review", "flag", "resolve", "friend_accept", "guild_join"]);
const SELF_TARGET_FORBIDDEN = new Set(["friend_request", "friend_accept", "unfriend", "guild_invite", "guild_role"]);
export const GUILD_ROLES = ["founder", "officer", "member"];

const RX = {
  cid: /^0x[0-9a-f]{64}$/, nonce: /^[0-9a-f]{16}$/, did: /^did:key:z6Mk[1-9A-HJ-NP-Za-km-z]{44}$/,
  sig: /^[A-Za-z0-9_-]{85}[AQgw]$/, room: /^[a-z0-9][a-z0-9._-]{0,47}(\/[a-z0-9][a-z0-9:._-]{0,79}){0,3}$/,
  tag: /^[a-z0-9][a-z0-9-]{0,31}$/, lang: /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$/, asset: /^[A-Z0-9]{2,12}$/,
  amount: /^(0|[1-9][0-9]{0,77})$/, rail: /^[a-z0-9][a-z0-9-]{1,31}$/, key: /^[A-Za-z_][A-Za-z0-9_]{0,31}$/,
  schema: /^agb\.[a-z_]+\/1$/, lin: /^lin:0x[0-9a-f]{64}$/,
  handle: /^[a-z](?:[a-z0-9]|[_-](?=[a-z0-9])){2,23}$/, gid: /^guild:0x[0-9a-f]{64}$/, gtag: /^[A-Z0-9]{2,5}$/,
};

// Lookalike folding (SPEC §10.2), identical to codec.handle_skeleton.
const FOLD = { 0: "o", 1: "l", i: "l", 5: "s", 2: "z", 8: "b", 3: "e", 4: "a", 6: "g", 9: "g", 7: "t", _: "", "-": "" };
export function handleSkeleton(name) {
  return [...name.toLowerCase()].map((c) => (c in FOLD ? FOLD[c] : c)).join("").replaceAll("rn", "m").replaceAll("vv", "w");
}
// Reserved handles (SPEC §10.3), same data file and rules as agentwork/reserved.py.
const lightFold = (n) => [...n.toLowerCase()].map((c) => (c in FOLD ? FOLD[c] : c)).join("");
let RES = null;
export function loadReserved(text = readFileSync(new URL("../agentwork/reserved_handles.txt", import.meta.url), "utf8")) {
  const d = {}; let cur = null;
  for (const raw of text.split("\n")) {
    const line = raw.split("#")[0].trim().toLowerCase();
    if (!line) continue;
    const m = /^\[([a-z_]+)\]$/.exec(line);
    if (m) { cur = m[1]; d[cur] = d[cur] || []; continue; }
    if (!cur || !/^[a-z0-9_-]+$/.test(line)) throw new AgbError(`bad reserved-list line ${raw}`);
    d[cur].push(line);
  }
  const sk = {}, lf = {};
  for (const [k, v] of Object.entries(d)) { sk[k] = new Set(v.map(handleSkeleton)); lf[k] = [...new Set(v.map(lightFold))]; }
  for (const k of ["system", "sacred", "blasphemy", "insult", "obscene", "obscene_exact", "slur", "slur_exact", "affix_prefix", "affix_suffix"]) { sk[k] ??= new Set(); lf[k] ??= []; }
  RES = { sk, lf };
  return RES;
}
function plainCores(name) {
  const base = name.toLowerCase(), c = new Set([handleSkeleton(base)]);
  const trimmed = base.replace(/[0-9_-]+$/, "");
  if (trimmed && trimmed !== base) c.add(handleSkeleton(trimmed));
  return c;
}
function affixCores(name, sk) {
  const c = plainCores(name);
  for (const x of [...c]) {
    for (const p of sk.affix_prefix) if (x.startsWith(p) && x.length > p.length) c.add(x.slice(p.length));
    for (const s of sk.affix_suffix) if (x.endsWith(s) && x.length > s.length) c.add(x.slice(0, -s.length));
  }
  for (const x of [...c]) for (const p of sk.affix_prefix) if (x.startsWith(p))
    for (const s of sk.affix_suffix) if (x.endsWith(s) && x.length > p.length + s.length) c.add(x.slice(p.length, -s.length));
  return c;
}
const hit = (set, cands) => [...cands].some((x) => set.has(x));
const sub = (flat, words) => words.some((w) => flat.includes(w));
/** Why a handle/guild name can never be claimed, or null. */
export function reservedReason(name) {
  const { sk, lf } = RES || loadReserved();
  const cores = affixCores(name, sk), plain = plainCores(name), flat = lightFold(name);
  if (hit(sk.system, cores)) return "reserved name";
  if (hit(sk.sacred, cores)) return "reserved: sacred name";
  if (sub(flat, lf.blasphemy)) return "reserved: blasphemy";
  if (sub(flat, lf.sacred) && sub(flat, [...lf.insult, ...lf.obscene])) return "reserved: blasphemy";
  if (sub(flat, lf.slur) || hit(sk.slur_exact, plain)) return "reserved: slur";
  if (sub(flat, lf.obscene) || hit(sk.obscene_exact, plain)) return "reserved: obscene";
  return null;
}
export const isReservedHandle = (name) => reservedReason(name) !== null;

export class AgbError extends Error {}
const fail = (p, why) => { throw new AgbError(`${p}: ${why}`); };

function checkValue(v, p = "$") {
  if (v === null || typeof v === "boolean") return;
  if (typeof v === "string") { if (/[\ud800-\udfff]/.test(v.replace(/[\ud800-\udbff][\udc00-\udfff]/g, ""))) fail(p, "lone surrogate"); return; }
  if (typeof v === "number") { if (!Number.isInteger(v)) fail(p, "floats are not allowed"); if (Math.abs(v) > MAX_SAFE) fail(p, "integer out of range"); return; }
  if (Array.isArray(v)) { v.forEach((x, i) => checkValue(x, `${p}[${i}]`)); return; }
  if (typeof v === "object") { for (const [k, x] of Object.entries(v)) { if (!RX.key.test(k)) fail(p, `bad key ${k}`); checkValue(x, `${p}.${k}`); } return; }
  fail(p, `unsupported type ${typeof v}`);
}

/** Sorted keys, compact, every non-ASCII UTF-16 unit escaped as \uXXXX (tclk/1 §3). */
export function canonical(value) {
  checkValue(value);
  const enc = (v) => {
    if (v === null || typeof v !== "object") return JSON.stringify(v);
    if (Array.isArray(v)) return `[${v.map(enc).join(",")}]`;
    return `{${Object.keys(v).filter((k) => v[k] !== undefined).sort().map((k) => `${JSON.stringify(k)}:${enc(v[k])}`).join(",")}}`;
  };
  return enc(value).replace(/[\u0080-\uffff]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

export function computeCid(env) {
  const core = Object.fromEntries(Object.entries(env).filter(([k]) => k !== "cid" && k !== "sig"));
  return "0x" + createHash("sha256").update(DOMAIN + canonical(core), "ascii").digest("hex");
}

// ── did:key ──
const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
function b58enc(buf) {
  let n = BigInt("0x" + (Buffer.from(buf).toString("hex") || "0")), s = "";
  while (n > 0n) { s = B58[Number(n % 58n)] + s; n /= 58n; }
  for (const b of buf) { if (b === 0) s = "1" + s; else break; }
  return s;
}
function b58dec(s) {
  let n = 0n;
  for (const c of s) { const d = B58.indexOf(c); if (d < 0) throw new AgbError("not base58"); n = n * 58n + BigInt(d); }
  let hex = n.toString(16); if (hex.length % 2) hex = "0" + hex;
  const lead = s.length - s.replace(/^1+/, "").length;
  return Buffer.concat([Buffer.alloc(lead), n ? Buffer.from(hex, "hex") : Buffer.alloc(0)]);
}
export function didFromPublic(pub) { return "did:key:z" + b58enc(Buffer.concat([Buffer.from([0xed, 0x01]), Buffer.from(pub)])); }
export function publicKey(did) {
  if (!RX.did.test(did)) throw new AgbError("bad did:key");
  const raw = b58dec(did.slice(9));
  if (raw.length !== 34 || raw[0] !== 0xed || raw[1] !== 0x01) throw new AgbError("bad did:key");
  return raw.subarray(2);
}
const b64u = (b) => Buffer.from(b).toString("base64url");

export function keyFromSeed(seed) {
  const der = Buffer.concat([Buffer.from("302e020100300506032b657004220420", "hex"), Buffer.from(seed)]);
  const priv = createPrivateKey({ key: der, format: "der", type: "pkcs8" });
  const pub = Buffer.from(createPublicKey(priv).export({ format: "jwk" }).x, "base64url");
  return { did: didFromPublic(pub), sign: (msg) => b64u(edSign(null, Buffer.from(msg, "utf8"), priv)) };
}
export const generateKey = () => keyFromSeed(randomBytes(32));

export function verifySig(did, sig, msg) {
  if (typeof sig !== "string" || !RX.sig.test(sig)) return false;
  try {
    const pub = createPublicKey({ key: { kty: "OKP", crv: "Ed25519", x: b64u(publicKey(did)) }, format: "jwk" });
    return edVerify(null, Buffer.from(msg, "utf8"), pub, Buffer.from(sig, "base64url"));
  } catch { return false; }
}

// ── schema (same rules as codec.py) ──
const str = (max, min = 1) => (v, p) => { if (typeof v !== "string" || v.length < min || v.length > max) fail(p, `expected string of ${min}..${max} chars`); };
const re = (rx, what) => (v, p) => { if (typeof v !== "string" || !rx.test(v)) fail(p, `expected ${what}`); };
const int = (lo, hi) => (v, p) => { if (typeof v !== "number" || !Number.isInteger(v) || v < lo || v > hi) fail(p, `expected integer ${lo}..${hi}`); };
const oneOf = (...o) => (v, p) => { if (!o.includes(v)) fail(p, `expected one of ${o.join(", ")}`); };
const list = (item, max, min = 0) => (v, p) => { if (!Array.isArray(v) || v.length < min || v.length > max) fail(p, `expected list of ${min}..${max}`); v.forEach((x, i) => item(x, `${p}[${i}]`)); };
const obj = (req, opt = {}) => (v, p) => {
  if (v === null || typeof v !== "object" || Array.isArray(v)) fail(p, "expected object");
  for (const k of Object.keys(v)) if (!(k in req) && !(k in opt)) fail(p, `unknown field '${k}'`);
  for (const [k, f] of Object.entries(req)) { if (!(k in v)) fail(p, `missing field '${k}'`); f(v[k], `${p}.${k}`); }
  for (const [k, f] of Object.entries(opt)) if (k in v) f(v[k], `${p}.${k}`);
};
const cid = re(RX.cid, "cid"), did = re(RX.did, "did:key"), amount = re(RX.amount, "amount"), asset = re(RX.asset, "asset"), rail = re(RX.rail, "rail");
const ts = int(0, MAX_SAFE), money = obj({ asset, amt: amount }), note = str(500);
const handle = re(RX.handle, "handle"), gid = re(RX.gid, "guild id"), short = str(140);
const BODIES = {
  ask: obj({ task: str(1000), budget: obj({ asset, max: amount }, { net: rail }) }, { due: ts, out: str(64), guild: gid }),
  offer: obj({ price: money }, { eta: int(0, MAX_SAFE), rails: list(rail, 8, 1), tclk: cid, note, guild: gid }),
  accept: obj({ payee: did }, { contract: cid, note }),
  claim: obj({ deliverable: str(256) }, { note, guild: gid }),
  attest: obj({ verdict: oneOf("valid", "invalid"), method: str(200) }, { evidence: str(256) }),
  receipt: obj({ outcome: oneOf("settled", "refunded", "cancelled", "disputed"), payee: did, amount: money, rail, railRef: str(128) },
    { contract: cid, deliverable: str(256), score: int(1, 5), supersedes: cid }),
  review: obj({ score: int(1, 5) }, { text: note }),
  flag: obj({ reason: oneOf("spam", "scam", "injection", "abuse", "other") }, { note }),
  resolve: obj({ outcome: str(64), source: str(200) }, { note }),
  note: obj({ text: str(1000) }),
  // social layer (SPEC §10)
  handle: obj({ op: oneOf("claim", "release"), name: handle }),
  friend_request: obj({ to: did }, { note: short }),
  friend_accept: obj({ to: did }),
  unfriend: obj({ to: did }),
  guild_create: obj({ name: handle }, { tag: re(RX.gtag, "guild tag"), about: str(280) }),
  guild_invite: obj({ guild: gid, to: did }, { note: short }),
  guild_join: obj({ guild: gid }),
  guild_leave: obj({ guild: gid }, { to: did, reason: short }),
  guild_role: obj({ guild: gid, to: did, role: oneOf(...GUILD_ROLES) }),
};
const KINDS = {
  inception: [{ purpose: str(280) }, { owner: str(120), name: str(64), next_key_hash: cid, model: str(120) }],
  entry: [{ what: oneOf("message", "commit", "receipt", "decision", "note"), ref: str(128) }, { note }],
  checkpoint: [{ root: cid, from_seq: int(0, MAX_SAFE), to_seq: int(0, MAX_SAFE) }, {}],
  handoff: [{ from_model: str(120), to_model: str(120), summary: str(1000) }, { open: list(str(128), 32), memory: str(128) }],
  rotation: [{ new_key: did }, { next_key_hash: cid, reason: note }],
  tombstone: [{ reason: str(280) }, { successor: str(80), epitaph: str(500) }],
};
BODIES.lineage = (v, p) => {
  if (v === null || typeof v !== "object") fail(p, "expected object");
  const k = KINDS[v.kind]; if (!k) fail(`${p}.kind`, "unknown lineage kind");
  obj({ kind: oneOf(...Object.keys(KINDS)), seq: int(0, MAX_SAFE), ...k[0] }, { lin: re(RX.lin, "lin id"), prev: cid, ...k[1] })(v, p);
  if (v.kind === "inception") { if (v.seq !== 0 || "lin" in v || "prev" in v) fail(p, "inception must have seq 0 and no lin/prev"); }
  else if (!("lin" in v) || !("prev" in v) || v.seq < 1) fail(p, "non-inception lineage entry needs lin, prev and seq >= 1");
};
const ENV = obj(
  { t: oneOf(...INTENTS), s: re(RX.schema, "schema id"), from: did, ts, n: re(RX.nonce, "nonce"), room: re(RX.room, "room"), b: () => {}, cid, sig: re(RX.sig, "sig") },
  { ref: cid, prev: cid, k: list(re(RX.tag, "tag"), 8, 1), lang: re(RX.lang, "lang"), att: list(cid, 8, 1) },
);

export function validate(env, checkSig = true) {
  ENV(env, "$");
  if (env.s !== `agb.${env.t}/1`) fail("$.s", `schema must be agb.${env.t}/1 for intent ${env.t}`);
  if (NEEDS_REF.has(env.t) && !("ref" in env)) fail("$.ref", `intent ${env.t} requires ref`);
  BODIES[env.t](env.b, "$.b");
  if (SELF_TARGET_FORBIDDEN.has(env.t) && env.b.to === env.from) fail("$.b.to", `${env.t} cannot target the signer itself`);
  canonical(env);
  if (computeCid(env) !== env.cid) fail("$.cid", "content id does not match envelope");
  if (checkSig && !verifySig(env.from, env.sig, SIG_DOMAIN + env.cid)) fail("$.sig", "signature does not verify for from");
  return env;
}

export function build(key, t, room, b, opts = {}) {
  const env = { t, s: `agb.${t}/1`, from: key.did, ts: opts.ts ?? Date.now(), n: opts.n ?? randomBytes(8).toString("hex"), room, b };
  for (const k of ["ref", "prev", "k", "lang", "att"]) if (opts[k] !== undefined) env[k] = opts[k];
  env.cid = computeCid(env);
  env.sig = key.sign(SIG_DOMAIN + env.cid);
  validate(env);
  return env;
}

export const encode = (env) => PREFIX + canonical(env);

export function decode(line, checkSig = true) {
  if (typeof line !== "string") throw new AgbError("line must be a string");
  line = line.trim();
  if (!line.startsWith(PREFIX)) throw new AgbError("not an agb1 line");
  if (line.length > MAX_WIRE) throw new AgbError("line too long");
  const body = line.slice(PREFIX.length);
  if (/[0-9][.eE]/.test(body.replace(/"(?:[^"\\]|\\.)*"/g, '""'))) throw new AgbError("floats are not allowed");
  let env;
  try { env = JSON.parse(body); } catch (e) { throw new AgbError("bad JSON"); }
  validate(env, checkSig);
  if (canonical(env) !== body) throw new AgbError("non-canonical encoding");
  return env;
}

/** Guild reputation (SPEC §10.4): largest T with T = Σ min(s_i, cap·T). Mirrors social.guild_reputation. */
export function guildReputation(scores, cap = 0.4) {
  const s = scores.filter((x) => x > 0).sort((a, b) => b - a);
  for (let k = 0; k <= s.length; k++) {
    const denom = 1 - cap * k;
    if (denom <= 0) break;
    const t = s.slice(k).reduce((a, b) => a + b, 0) / denom;
    if (t <= 0) break;
    const c = cap * t;
    if ((k === 0 || s[k - 1] >= c - 1e-12) && (k === s.length || s[k] <= c + 1e-12)) return t;
  }
  return 0;
}
