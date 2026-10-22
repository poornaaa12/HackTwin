// Hand-built SVG charts. One y-axis per chart, recessive grid, thin marks with
// rounded data ends, per-mark hover tooltips, and every chart is accompanied by
// a table or labels elsewhere in its view so identity is never colour-only.
import { h, showTip, hideTip, fmt } from "./dom.js";

const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k != null) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}
function hover(el, text) {
  el.addEventListener("mousemove", (e) => showTip(text, e.clientX, e.clientY));
  el.addEventListener("mouseleave", hideTip);
  el.style.cursor = "default";
  return el;
}
function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v)); const m = v / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}
const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)"];
export const seriesColor = (i) => SERIES[i] || "var(--neutral)";

// Horizontal bars: items [{label, value, tip, color}]
export function hbar(items, { width = 560, unit = "", max, valueFmt = (v) => fmt.int(v), reference } = {}) {
  const rowH = 22, left = Math.min(220, 12 + Math.max(...items.map((i) => String(i.label).length), 4) * 6.6), right = 70;
  const H = items.length * rowH + 24;
  const vmax = max ?? niceMax(Math.max(...items.map((i) => i.value || 0), reference || 0));
  const x = (v) => left + (width - left - right) * (v / vmax);
  const svg = s("svg", { viewBox: `0 0 ${width} ${H}`, width: "100%", role: "img" });
  for (let t = 0; t <= 4; t++) {
    const v = (vmax * t) / 4;
    svg.append(s("line", { x1: x(v), x2: x(v), y1: 0, y2: H - 18, class: "gridline" }), s("text", { x: x(v), y: H - 4, "text-anchor": "middle" }, valueFmt(v)));
  }
  if (reference != null) svg.append(s("line", { x1: x(reference), x2: x(reference), y1: 0, y2: H - 18, stroke: "var(--text-3)", "stroke-dasharray": "3 3" }));
  items.forEach((it, i) => {
    const y = i * rowH + 4, w = Math.max(1, x(it.value || 0) - left);
    svg.append(s("text", { x: left - 8, y: y + 12, "text-anchor": "end" }, String(it.label).slice(0, 34)));
    const bar = s("rect", { x: left, y: y + 2, width: w, height: rowH - 8, rx: 3, fill: it.color || "var(--s1)" });
    svg.append(hover(bar, it.tip || `${it.label}: ${valueFmt(it.value)}${unit}`));
    svg.append(s("text", { x: left + w + 6, y: y + 12 }, it.valueLabel ?? `${valueFmt(it.value)}${unit}`));
  });
  return svg;
}

// Dot plot of ratios around a reference line (speedup, 1.0 = baseline).
export function ratioDots(rows, series, { width = 1080, label = "Speedup vs baseline" } = {}) {
  const left = 150, right = 20, rowH = 18, top = 22;
  const vals = rows.flatMap((r) => series.map((sv) => r.values[sv.key]).filter((v) => v != null));
  let lo = Math.min(1, ...vals), hi = Math.max(1, ...vals);
  const pad = (hi - lo) * 0.08 || 0.05; lo = Math.max(0, lo - pad); hi += pad;
  const H = top + rows.length * rowH + 26;
  const x = (v) => left + (width - left - right) * ((v - lo) / (hi - lo));
  const svg = s("svg", { viewBox: `0 0 ${width} ${H}`, width: "100%", role: "img", "aria-label": label });
  const ticks = 5;
  for (let t = 0; t <= ticks; t++) {
    const v = lo + ((hi - lo) * t) / ticks;
    svg.append(s("line", { x1: x(v), x2: x(v), y1: top - 6, y2: H - 22, class: "gridline" }), s("text", { x: x(v), y: H - 8, "text-anchor": "middle" }, v.toFixed(2)));
  }
  svg.append(s("line", { x1: x(1), x2: x(1), y1: top - 10, y2: H - 22, stroke: "var(--text-2)", "stroke-dasharray": "4 3" }), s("text", { x: x(1), y: 11, "text-anchor": "middle" }, "1.00 = baseline"));
  rows.forEach((r, i) => {
    const y = top + i * rowH + rowH / 2;
    svg.append(s("text", { x: left - 8, y: y + 4, "text-anchor": "end" }, r.label.slice(0, 24)));
    svg.append(s("line", { x1: left, x2: width - right, y1: y, y2: y, class: "gridline", "stroke-dasharray": "1 3" }));
    series.forEach((sv, j) => {
      const v = r.values[sv.key];
      if (v == null) return;
      const dot = s("circle", { cx: x(v), cy: y + (j - (series.length - 1) / 2) * 3, r: 4.5, fill: seriesColor(j), stroke: "var(--surface)", "stroke-width": 2 });
      svg.append(hover(dot, `${sv.label} on ${r.label}: speedup ${v.toFixed(4)} (${r.tips?.[sv.key] || ""})`));
    });
  });
  return svg;
}

// PC over cycle: step line (observed). points: [{cycle, pc}], optional marks [{cycle, kind}]
export function pcTimeline(points, { width = 900, height = 220, halt, onPick, highlight } = {}) {
  const left = 44, right = 12, top = 10, bottom = 26;
  const n = points.length; const maxC = Math.max(1, points[n - 1]?.cycle ?? 1);
  const maxPc = niceMax(Math.max(1, ...points.map((p) => p.pc)));
  const x = (c) => left + (width - left - right) * (c / maxC);
  const y = (pc) => top + (height - top - bottom) * (1 - pc / maxPc);
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", role: "img", "aria-label": "Program counter over cycles" });
  for (let t = 0; t <= 4; t++) { const v = (maxPc * t) / 4; svg.append(s("line", { x1: left, x2: width - right, y1: y(v), y2: y(v), class: "gridline" }), s("text", { x: left - 6, y: y(v) + 4, "text-anchor": "end" }, Math.round(v))); }
  for (let t = 0; t <= 6; t++) { const c = Math.round((maxC * t) / 6); svg.append(s("text", { x: x(c), y: height - 8, "text-anchor": "middle" }, c)); }
  let d = "";
  points.forEach((p, i) => { d += i ? `H${x(p.cycle).toFixed(1)}V${y(p.pc).toFixed(1)}` : `M${x(p.cycle).toFixed(1)},${y(p.pc).toFixed(1)}`; });
  svg.append(s("path", { d, fill: "none", stroke: "var(--s1)", "stroke-width": 1.5 }));
  if (halt != null) svg.append(s("line", { x1: x(halt), x2: x(halt), y1: top, y2: height - bottom, stroke: "var(--s2)", "stroke-dasharray": "4 3" }), s("text", { x: x(halt) - 4, y: top + 10, "text-anchor": "end" }, `halt @ ${halt}`));
  if (highlight) for (const [a, b] of highlight) svg.append(s("rect", { x: x(a), y: top, width: Math.max(1, x(b) - x(a)), height: height - top - bottom, fill: "var(--accent)", opacity: .12 }));
  const cross = s("line", { y1: top, y2: height - bottom, stroke: "var(--text-3)", visibility: "hidden" });
  svg.append(cross);
  const hit = s("rect", { x: left, y: top, width: width - left - right, height: height - top - bottom, fill: "transparent" });
  const at = (e) => {
    const r = svg.getBoundingClientRect(); const cx = ((e.clientX - r.left) / r.width) * width;
    const c = Math.max(0, Math.min(maxC, Math.round(((cx - left) / (width - left - right)) * maxC)));
    let lo = 0, hi = n - 1; while (lo < hi) { const m = (lo + hi + 1) >> 1; if (points[m].cycle <= c) lo = m; else hi = m - 1; }
    return { c, p: points[lo] };
  };
  hit.addEventListener("mousemove", (e) => { const { c, p } = at(e); cross.setAttribute("x1", x(c)); cross.setAttribute("x2", x(c)); cross.setAttribute("visibility", "visible"); showTip(`cycle ${c}: PC ${p.pc}${onPick ? " — click to open" : ""}`, e.clientX, e.clientY); });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
  if (onPick) { hit.style.cursor = "pointer"; hit.addEventListener("click", (e) => onPick(at(e).c)); }
  svg.append(hit);
  return svg;
}

// Column histogram over integer bins (e.g. cycles per PC). bins: [{x, value, tip}]
export function columns(bins, { width = 900, height = 170, xLabel = "", color = "var(--s1)", onPick } = {}) {
  const left = 44, right = 10, top = 8, bottom = 30;
  const maxV = niceMax(Math.max(1, ...bins.map((b) => b.value)));
  const n = bins.length || 1, bw = (width - left - right) / n;
  const y = (v) => top + (height - top - bottom) * (1 - v / maxV);
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", role: "img" });
  for (let t = 0; t <= 4; t++) { const v = (maxV * t) / 4; svg.append(s("line", { x1: left, x2: width - right, y1: y(v), y2: y(v), class: "gridline" }), s("text", { x: left - 6, y: y(v) + 4, "text-anchor": "end" }, fmt.int(v))); }
  const step = Math.max(1, Math.ceil(n / 16));
  bins.forEach((b, i) => {
    const bx = left + i * bw;
    const bar = s("rect", { x: bx + Math.min(1, bw * .1), y: y(b.value), width: Math.max(1, bw - Math.min(2, bw * .2)), height: Math.max(0, y(0) - y(b.value)), rx: Math.min(3, bw / 3), fill: b.color || color });
    svg.append(hover(bar, b.tip || `${b.x}: ${b.value}`));
    if (onPick) { bar.style.cursor = "pointer"; bar.addEventListener("click", () => onPick(b)); }
    if (i % step === 0) svg.append(s("text", { x: bx + bw / 2, y: height - 14, "text-anchor": "middle" }, b.x));
  });
  if (xLabel) svg.append(s("text", { x: (width + left) / 2, y: height - 1, "text-anchor": "middle" }, xLabel));
  return svg;
}

// Grouped vertical bars for a few series across categories. groups: [{label, values:{key:v}}]
export function groupedBars(groups, series, { width = 1080, height = 240, valueFmt = fmt.int } = {}) {
  const left = 54, right = 10, top = 10, bottom = 46;
  const maxV = niceMax(Math.max(1, ...groups.flatMap((g) => series.map((sv) => g.values[sv.key] || 0))));
  const gw = (width - left - right) / Math.max(1, groups.length), bw = Math.min(18, (gw - 8) / series.length - 2);
  const y = (v) => top + (height - top - bottom) * (1 - v / maxV);
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", role: "img" });
  for (let t = 0; t <= 4; t++) { const v = (maxV * t) / 4; svg.append(s("line", { x1: left, x2: width - right, y1: y(v), y2: y(v), class: "gridline" }), s("text", { x: left - 6, y: y(v) + 4, "text-anchor": "end" }, valueFmt(v))); }
  groups.forEach((g, i) => {
    const gx = left + i * gw + (gw - series.length * (bw + 2)) / 2;
    series.forEach((sv, j) => {
      const v = g.values[sv.key];
      if (v == null) return;
      const bar = s("rect", { x: gx + j * (bw + 2), y: y(v), width: bw, height: Math.max(0, y(0) - y(v)), rx: 2, fill: sv.color || seriesColor(j) });
      svg.append(hover(bar, `${sv.label} · ${g.label}: ${valueFmt(v)}`));
    });
    const t = s("text", { x: left + i * gw + gw / 2, y: height - bottom + 14, "text-anchor": "end", transform: `rotate(-35 ${left + i * gw + gw / 2} ${height - bottom + 14})` }, g.label.slice(0, 18));
    svg.append(t);
  });
  return svg;
}

export function legend(series) {
  return h("div", { class: "legend" }, series.map((sv, i) => h("span", {}, h("i", { style: `background:${sv.color || seriesColor(i)}` }), sv.label)));
}
