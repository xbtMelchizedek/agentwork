// node ts/test.mjs — checks the TS codec against vectors/agb1-v1.json (no deps).
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { canonical, computeCid, decode, encode, build, keyFromSeed, generateKey, AgbError } from "./agb1.mjs";

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
// receipt-correction scenarios: every line must decode and round-trip in TS too
const S = JSON.parse(readFileSync(new URL("../vectors/supersede-v1.json", import.meta.url)));
for (const sc of S.scenarios) {
  for (const line of sc.lines) {
    let good = false;
    try { good = encode(decode(line)) === line; } catch (e) { good = false; }
    ok(good, `supersede ${sc.name}: line decodes`);
  }
}
const k = generateKey();
const e = build(k, "note", "core/builders", { text: "fresh key round trip ✓" });
ok(decode(encode(e)).cid === e.cid, "fresh key round trip");
console.log(`ts vectors: ${pass} passed, ${failN} failed`);
process.exit(failN ? 1 : 0);
