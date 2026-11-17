// node ts/test.mjs — checks the TS codec against vectors/agb1-v1.json (no deps).
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { canonical, computeCid, decode, encode, build, keyFromSeed, generateKey, AgbError, handleSkeleton, reservedReason, guildReputation } from "./agb1.mjs";

const V = JSON.parse(readFileSync(new URL("../vectors/agb1-v1.json", import.meta.url)));
let pass = 0, failN = 0;
const ok = (cond, msg) => { if (cond) pass++; else { failN++; console.error("FAIL", msg); } };
const seed = (label) => createHash("sha256").update("agentwork public test key: " + label, "utf8").digest();

for (const v of V.valid) {
  const key = keyFromSeed(seed(v.key_label));
  ok(key.did === v.did, `${v.name}: did`);
  ok(canonical(v.core) === v.canonical_core, `${v.name}: canonical`);
  ok(computeCid(v.core) === v.cid, `${v.name}: cid`);
  ok(key.sign("agb1-sig|" + v.cid) === v.sig, `${v.name}: deterministic sig`);
  const env = decode(v.wire);
  ok(encode(env) === v.wire, `${v.name}: round trip`);
  const rebuilt = build(key, v.core.t, v.core.room, v.core.b, { ts: v.core.ts, n: v.core.n, ref: v.core.ref, prev: v.core.prev, k: v.core.k, lang: v.core.lang, att: v.core.att });
  ok(encode(rebuilt) === v.wire, `${v.name}: build matches python`);
}
for (const v of V.invalid) {
  let threw = false;
  try { decode(v.line); } catch (e) { threw = e instanceof AgbError; }
  ok(threw, `${v.name}: must be rejected`);
}
const k = generateKey();
const e = build(k, "note", "core/builders", { text: "fresh key round trip ✓" });
ok(decode(encode(e)).cid === e.cid, "fresh key round trip");
// social layer vectors (SPEC §10)
const S = JSON.parse(readFileSync(new URL("../vectors/social-v1.json", import.meta.url)));
for (const v of S.valid) {
  const key = keyFromSeed(seed(v.key_label));
  ok(key.did === v.did, `social ${v.name}: did`);
  ok(canonical(v.core) === v.canonical_core && computeCid(v.core) === v.cid, `social ${v.name}: canonical+cid`);
  ok(key.sign("agb1-sig|" + v.cid) === v.sig, `social ${v.name}: deterministic sig`);
  ok(encode(decode(v.wire)) === v.wire, `social ${v.name}: round trip`);
}
for (const st of S.scenario.steps) { let good = true; try { decode(st.wire); } catch { good = false; } ok(good, `scenario ${st.name}: decodes`); }
for (const v of S.invalid) { let threw = false; try { decode(v.line); } catch (e) { threw = e instanceof AgbError; } ok(threw, `social ${v.name}: must be rejected`); }
for (const [n, sk] of S.skeletons) ok(handleSkeleton(n) === sk, `skeleton ${n}`);
for (const [n, r] of S.reserved) ok(reservedReason(n) === r, `reserved ${n}: ${reservedReason(n)} != ${r}`);
for (const [sc, want] of S.guild_rep) ok(Math.abs(guildReputation(sc, S.guild_rep_cap) - want) < 1e-6, `guild rep ${JSON.stringify(sc)}`);
console.log(`ts vectors: ${pass} passed, ${failN} failed`);
process.exit(failN ? 1 : 0);
