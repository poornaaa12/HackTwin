// Small DOM helpers. All text goes through textContent (never innerHTML) so
// data from the store or external repositories cannot inject markup.
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}
export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
export const clear = (el) => { el.replaceChildren(); return el; };

export const fmt = {
  int: (v) => v == null ? "—" : Math.round(v).toLocaleString("en-US"),
  num: (v, d = 2) => v == null || Number.isNaN(v) ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }),
  pct: (v, d = 1) => v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`,
  pctAbs: (v, d = 1) => v == null ? "—" : `${Number(v).toFixed(d)}%`,
  x: (v, d = 4) => v == null ? "—" : `${Number(v).toFixed(d)}×`,
  hash: (v, n = 10) => v ? String(v).slice(0, n) : "—",
  time: (t) => t ? new Date(t * 1000).toLocaleString() : "—",
  ago: (t) => {
    if (!t) return "—";
    const s = Date.now() / 1000 - t;
    if (s < 90) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  },
};

const STATUS = {
  succeeded: "good", pass: "good", passed: "good", validated: "good", complete: "good", correct: "good",
  experimentally_verified: "good", finished: "good",
  failed: "bad", fail: "bad", comparison_failed: "bad", error: "bad", rejected: "bad", incompatible: "bad",
  failed_tests: "bad", confirmed_problem: "bad", timeout: "bad",
  invalid: "warn", experimentally_refuted: "warn", duplicate: "neutral", heuristic: "info", running: "info",
  queued: "neutral", pending: "neutral", not_applicable: "neutral", unknown: "neutral", software_suggestion: "neutral",
  observation: "info", quality_warning: "warn",
};
export function badge(value, label) {
  const tone = STATUS[value] || "neutral";
  return h("span", { class: `badge b-${tone}` }, label ?? String(value ?? "—").replaceAll("_", " "));
}

// Glossary-backed term with a hover/focus explanation.
let glossary = {};
export function setGlossary(g) { glossary = g || {}; }
export function term(key, label) {
  const g = glossary[key];
  const el = h("span", { class: "term", tabindex: "0", "data-tip": g ? g.text : "" }, label || (g ? g.term : key));
  return el;
}
export function tip(el, text) { el.dataset.tip = text; return el; }

const tooltip = () => document.getElementById("tooltip");
export function showTip(text, x, y) {
  const t = tooltip(); t.textContent = text; t.hidden = false;
  const r = t.getBoundingClientRect();
  t.style.left = `${Math.min(window.innerWidth - r.width - 8, x + 12)}px`;
  t.style.top = `${Math.max(8, y - r.height - 10)}px`;
}
export function hideTip() { tooltip().hidden = true; }
document.addEventListener("mouseover", (e) => { const el = e.target.closest?.("[data-tip]"); if (el && el.dataset.tip) showTip(el.dataset.tip, e.clientX, e.clientY); });
document.addEventListener("mousemove", (e) => { const el = e.target.closest?.("[data-tip]"); if (el && el.dataset.tip) showTip(el.dataset.tip, e.clientX, e.clientY); });
document.addEventListener("mouseout", (e) => { if (e.target.closest?.("[data-tip]")) hideTip(); });
document.addEventListener("focusin", (e) => { const el = e.target.closest?.("[data-tip]"); if (el && el.dataset.tip) { const r = el.getBoundingClientRect(); showTip(el.dataset.tip, r.left, r.top); } });
document.addEventListener("focusout", hideTip);

export function card(title, ...body) {
  const head = title == null ? null : (title instanceof Node ? title : h("div", { class: "card-head" }, h("h2", {}, title)));
  return h("section", { class: "card" }, head, ...body);
}
export function empty(text, action) { return h("div", { class: "empty" }, text, action ? h("div", { style: "margin-top:10px" }, action) : null); }
export function notice(text, tone = "") { return h("div", { class: `notice ${tone}` }, text); }
export function stat(label, value, sub, tipText) {
  const l = h("span", { class: "label" }, label);
  if (tipText) tip(l, tipText), l.classList.add("term");
  return h("div", { class: "stat" }, l, h("span", { class: "value" }, value), sub ? h("span", { class: "sub" }, sub) : null);
}
export function kv(pairs) {
  const dl = h("dl", { class: "kv" });
  for (const [k, v] of pairs) if (v !== undefined) dl.append(h("dt", {}, k), h("dd", {}, v ?? "—"));
  return dl;
}
// table(columns, rows, {onRow, selected}) where column = {label, key|value(row), num, tip}
export function table(columns, rows, opts = {}) {
  const thead = h("thead", {}, h("tr", {}, columns.map((c) => {
    const th = h("th", { class: c.num ? "num" : "" }, c.label);
    if (c.tip) { th.dataset.tip = c.tip; th.classList.add("term"); }
    return th;
  })));
  const tbody = h("tbody");
  for (const r of rows) {
    const tr = h("tr", { class: [opts.onRow ? "clickable" : "", opts.rowClass ? opts.rowClass(r) : ""].join(" ") });
    if (opts.onRow) tr.addEventListener("click", (e) => { if (!e.target.closest("a,button,input")) opts.onRow(r, tr); });
    for (const c of columns) {
      const v = c.value ? c.value(r) : r[c.key];
      tr.append(h("td", { class: c.num ? "num" : "" }, v == null ? "—" : v));
    }
    tbody.append(tr);
  }
  return h("div", { class: "table-wrap", style: opts.maxHeight ? `max-height:${opts.maxHeight}` : null }, h("table", {}, thead, tbody));
}
export function tabs(items, onSelect, initial) {
  const bar = h("div", { class: "tabs", role: "tablist" });
  const btns = items.map(([id, label]) => h("button", { class: "tab", role: "tab", onclick: () => select(id) }, label));
  bar.append(...btns);
  function select(id) { btns.forEach((b, i) => b.classList.toggle("on", items[i][0] === id)); onSelect(id); }
  queueMicrotask(() => select(initial || items[0][0]));
  return bar;
}
export function link(href, text) { return h("a", { href }, text); }
export function designLink(id, name) { return h("a", { href: `#/designs/${encodeURIComponent(id)}` }, name || id); }
export function runLink(id, text) { return h("a", { href: `#/workbench/run/${encodeURIComponent(id)}` }, text || id); }
export function loading(text = "Loading…") { return h("div", { class: "muted", style: "padding:20px 0" }, text); }
export function errorBox(err) { return notice(`Could not load this view: ${err.message || err}`, "bad"); }
