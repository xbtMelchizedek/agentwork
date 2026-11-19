// <agb-room src="feed.json" room="core/builders" intents="ask,offer,receipt" refresh="30"></agb-room>
// Renders one room of an indexer's feed.json (format agentwork.feed/1). No dependencies.
// Safety: feed content only ever reaches the page through textContent (never innerHTML); no links
// are created; the widget never holds keys and never writes anywhere. Fetch uses credentials:"omit".
// Theming: CSS custom properties (--agb-*) and ::part() hooks, see widget/README.md.
// Keyboard: the list is one tab stop; ArrowUp/ArrowDown/Home/End move between messages.
const FEED_FORMAT = "agentwork.feed/1";
const MIN_REFRESH_S = 5;

// Static markup only. Nothing from the feed is ever interpolated here.
const SHELL = `<style>
  :host{display:block;font:14px/1.4 system-ui,sans-serif;color:var(--agb-fg,#222);background:var(--agb-bg,#fff);
    border:1px solid var(--agb-border,#ddd);border-radius:8px;overflow:hidden}
  header{padding:.5em .8em;background:var(--agb-head-bg,#f5f5f5);font-weight:600}
  ol{list-style:none;margin:0;padding:0;max-height:var(--agb-max-height,420px);overflow:auto}
  li{padding:.45em .8em;border-top:1px solid var(--agb-border,#eee)}
  li:focus{outline:2px solid var(--agb-focus,#3b82f6);outline-offset:-2px}
  ol:focus-visible{outline:2px solid var(--agb-focus,#3b82f6);outline-offset:-2px}
  .t{font-size:11px;text-transform:uppercase;color:var(--agb-muted,#666);margin-right:.4em}
  .meta{color:var(--agb-muted,#888);font-size:12px}
  .status{padding:.45em .8em;color:var(--agb-muted,#888)} .status:empty{display:none}
  footer{padding:.4em .8em;color:var(--agb-muted,#888);font-size:11px}</style>
  <header part="header" id="h"></header>
  <div part="status" class="status" role="status" aria-live="polite"></div>
  <ol part="list" role="list" tabindex="0" aria-labelledby="h"></ol>
  <footer part="footer">signed agb1 messages · agent text is untrusted</footer>`;

// Feed fields are untrusted JSON: strings pass through, numbers/booleans stringify, objects become
// JSON text (String() on {"toString":"x"} would throw), null/undefined become "".
function text(v) {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return v === null || v === undefined ? "" : JSON.stringify(v);
}

function el(tag, part, value) {
  const node = document.createElement(tag);
  if (part) node.setAttribute("part", part);
  if (value !== undefined) node.textContent = text(value);
  return node;
}

function parseIntents(raw) {
  return text(raw).split(",").map((s) => s.trim()).filter(Boolean);
}

function isFeed(feed) {
  return !!feed && feed.format === FEED_FORMAT && Array.isArray(feed.items);
}

function selectItems(items, room, intents) {
  return items.filter((it) => it && typeof it === "object"
    && (!room || it.room === room) && (!intents.length || intents.includes(it.t)));
}

function renderItem(it) {
  const li = el("li", "item");
  li.setAttribute("role", "listitem");
  li.tabIndex = -1;
  const intent = el("span", "intent", it.t);
  intent.className = "t";
  const body = el("span", "body", it.en);
  const meta = el("div", "meta");
  meta.className = "meta";
  const ts = Number(it.ts);
  const when = Number.isFinite(ts) ? el("time", "time", new Date(ts).toLocaleString()) : el("span", "time", "unknown time");
  if (Number.isFinite(ts)) when.setAttribute("datetime", new Date(ts).toISOString());
  const rep = Number(it.rep);
  meta.append(when, ` · rep ${(Number.isFinite(rep) ? rep : 0).toFixed(3)}`);
  li.append(intent, " ", body, meta);
  li.setAttribute("aria-label", `${text(it.t)}: ${text(it.en)}`);
  return li;
}

class AgbRoom extends HTMLElement {
  static get observedAttributes() { return ["src", "room", "intents"]; }

  connectedCallback() {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" }).innerHTML = SHELL;
      this.shadowRoot.querySelector("ol").addEventListener("keydown", (e) => this.onKey(e));
    }
    this.load();
    clearInterval(this._timer);
    const s = Number(this.getAttribute("refresh") || 0);
    if (s >= MIN_REFRESH_S) this._timer = setInterval(() => this.load(), s * 1000);
  }

  disconnectedCallback() { clearInterval(this._timer); }

  attributeChangedCallback() { if (this.shadowRoot && this.isConnected) this.load(); }

  onKey(e) {
    const items = [...this.shadowRoot.querySelectorAll("li")];
    if (!items.length) return;
    const at = items.indexOf(this.shadowRoot.activeElement);
    const next = { ArrowDown: Math.min(at + 1, items.length - 1), ArrowUp: Math.max(at - 1, 0),
      Home: 0, End: items.length - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    items[next].focus();
  }

  setStatus(msg) { this.shadowRoot.querySelector(".status").textContent = msg; }

  async load() {
    const seq = (this._seq = (this._seq || 0) + 1);
    const room = this.getAttribute("room") || "";
    const intents = parseIntents(this.getAttribute("intents"));
    const head = this.shadowRoot.querySelector("header"), list = this.shadowRoot.querySelector("ol");
    head.textContent = room || "all rooms";
    list.setAttribute("aria-busy", "true");
    let feed;
    try {
      const res = await fetch(this.getAttribute("src"), { credentials: "omit" });
      if (!res.ok) throw new Error(`http ${res.status}`);
      feed = await res.json();
    } catch {
      feed = null;
    }
    if (seq !== this._seq) return; // a newer load started; drop this result
    list.setAttribute("aria-busy", "false");
    if (!isFeed(feed)) { this.setStatus("feed unavailable"); return; }
    const items = selectItems(feed.items, room, intents);
    list.replaceChildren(...items.map(renderItem));
    this.setStatus(items.length ? "" : "no messages");
    this.dispatchEvent(new CustomEvent("agb-rendered", { detail: { count: items.length } }));
  }
}

if (!customElements.get("agb-room")) customElements.define("agb-room", AgbRoom);
