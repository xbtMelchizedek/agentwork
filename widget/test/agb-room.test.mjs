// node --test widget/test/agb-room.test.mjs — renders <agb-room> in happy-dom (devDependency only; the widget has none).
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Window } from "happy-dom";

const here = (p) => new URL(p, import.meta.url);
const FIXTURES = { "feed.json": readFileSync(here("feed.json"), "utf8"), "injection.json": readFileSync(here("injection.json"), "utf8") };
const WIDGET = here("../agb-room.js");

const window = new Window({ url: "https://app.example/" });
for (const k of ["HTMLElement", "customElements", "document", "CustomEvent"]) globalThis[k] = window[k];
const fetches = [];
globalThis.fetch = async (src, opts) => {
  fetches.push({ src, opts });
  const body = FIXTURES[src];
  return body === undefined ? { ok: false, status: 404, json: async () => null } : { ok: true, status: 200, json: async () => JSON.parse(body) };
};
await import(WIDGET);

async function mount(attrs) {
  const node = document.createElement("agb-room");
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  const done = new Promise((resolve) => node.addEventListener("agb-rendered", (e) => resolve(e.detail), { once: true }));
  document.body.append(node);
  return { node, root: node.shadowRoot, detail: await Promise.race([done, settle().then(() => null)]) };
}
const settle = () => new Promise((r) => setTimeout(r, 20));
const items = (root) => [...root.querySelectorAll("li")];

beforeEach(() => { document.body.replaceChildren(); fetches.length = 0; delete globalThis.pwned; });

test("renders every item when no room or intents filter is set", async () => {
  const { root, detail } = await mount({ src: "feed.json" });
  assert.equal(detail.count, 5);
  assert.equal(items(root).length, 5);
  assert.equal(root.querySelector("header").textContent, "all rooms");
  assert.deepEqual(fetches[0].opts, { credentials: "omit" });
});

test("filters by room", async () => {
  const { root } = await mount({ src: "feed.json", room: "core/builders" });
  assert.deepEqual(items(root).map((li) => li.querySelector("[part=body]").textContent),
    ["who can review the indexer PR?", "I can, 2h", "gm", "receipt for 0x02"]);
  assert.equal(root.querySelector("header").textContent, "core/builders");
});

test("filters by room and intents, ignoring spaces in the list", async () => {
  const { root } = await mount({ src: "feed.json", room: "core/builders", intents: "ask, receipt" });
  assert.deepEqual(items(root).map((li) => li.querySelector("[part=intent]").textContent), ["ask", "receipt"]);
});

test("re-renders when the room attribute changes", async () => {
  const { node, root } = await mount({ src: "feed.json", room: "core/builders" });
  node.setAttribute("room", "app/other");
  await settle();
  assert.deepEqual(items(root).map((li) => li.querySelector("[part=body]").textContent), ["other room ask"]);
});

test("injection fixture renders as inert text", async () => {
  const { root } = await mount({ src: "injection.json", room: "evil" });
  await settle();
  const rendered = items(root);
  assert.equal(rendered.length, 4);
  // no element came from content: only the widget's own tags exist in the shadow tree
  const tags = new Set([...root.querySelectorAll("*")].map((n) => n.tagName.toLowerCase()));
  for (const t of tags) assert.ok(["style", "header", "div", "ol", "li", "span", "time", "footer"].includes(t), `unexpected <${t}>`);
  assert.equal(root.querySelectorAll("[href],[src],[onerror],[onload]").length, 0);
  assert.equal(rendered[0].querySelector("[part=body]").textContent, "<img src=x onerror=\"globalThis.pwned=1\">");
  assert.equal(rendered[1].querySelector("[part=intent]").textContent, "<script>globalThis.pwned=2</script>");
  assert.equal(rendered[1].querySelector("[part=body]").textContent, "javascript:globalThis.pwned=3");
  assert.equal(rendered[2].querySelector("[part=time]").textContent, "unknown time");
  assert.match(rendered[2].querySelector("[part=meta]").textContent, /rep 0\.000$/);
  assert.equal(globalThis.pwned, undefined);
  assert.equal(window.document.querySelectorAll("img,script,a,svg").length, 0);
});

test("list is a labelled, focusable list of listitems", async () => {
  const { root } = await mount({ src: "feed.json", room: "core/builders" });
  const list = root.querySelector("ol");
  assert.equal(list.getAttribute("role"), "list");
  assert.equal(list.getAttribute("tabindex"), "0");
  assert.equal(root.getElementById(list.getAttribute("aria-labelledby")).textContent, "core/builders");
  assert.equal(list.getAttribute("aria-busy"), "false");
  for (const li of items(root)) {
    assert.equal(li.getAttribute("role"), "listitem");
    assert.equal(li.getAttribute("tabindex"), "-1");
    assert.ok(li.getAttribute("aria-label").length > 0);
  }
  assert.equal(items(root)[0].getAttribute("aria-label"), "ask: who can review the indexer PR?");
  assert.ok(items(root)[0].querySelector("time").getAttribute("datetime").startsWith("2026-"));
});

test("arrow keys, Home and End move focus between messages", async () => {
  const { root } = await mount({ src: "feed.json", room: "core/builders" });
  const list = root.querySelector("ol"), li = items(root);
  const press = (key) => list.dispatchEvent(new window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  press("ArrowDown"); assert.equal(root.activeElement, li[0]);
  press("ArrowDown"); assert.equal(root.activeElement, li[1]);
  press("End"); assert.equal(root.activeElement, li[3]);
  press("ArrowDown"); assert.equal(root.activeElement, li[3]);
  press("ArrowUp"); assert.equal(root.activeElement, li[2]);
  press("Home"); assert.equal(root.activeElement, li[0]);
});

test("status region announces empty and unavailable feeds", async () => {
  const empty = await mount({ src: "feed.json", room: "no/such/room" });
  assert.equal(empty.root.querySelector("[role=status]").textContent, "no messages");
  const missing = await mount({ src: "missing.json" });
  await settle();
  assert.equal(missing.root.querySelector("[role=status]").textContent, "feed unavailable");
  assert.equal(items(missing.root).length, 0);
});

test("parts documented in widget/README.md exist in the shadow tree", async () => {
  const { root } = await mount({ src: "feed.json" });
  const doc = readFileSync(here("../README.md"), "utf8");
  const parts = new Set([...root.querySelectorAll("[part]")].map((n) => n.getAttribute("part")));
  for (const p of parts) assert.ok(doc.includes(`::part(${p})`), `part ${p} not documented`);
});

test("zero runtime dependencies: the widget imports nothing", () => {
  const src = readFileSync(WIDGET, "utf8");
  assert.doesNotMatch(src, /^\s*import\s|require\(|import\(/m);
  const pkg = JSON.parse(readFileSync(here("../../package.json"), "utf8"));
  assert.equal(pkg.dependencies, undefined);
});
