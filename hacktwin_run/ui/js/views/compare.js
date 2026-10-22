import { api } from "../api.js";
import { badge, card, clear, designLink, empty, fmt, h, notice, runLink, table, term } from "../dom.js";
import { groupedBars, legend, ratioDots, seriesColor } from "../charts.js";
import { benchmarksList, designsList, workflow } from "./common.js";

export async function render(route) {
  const [designs, benches] = await Promise.all([designsList(), benchmarksList()]);
  const core = benches.filter((b) => b.suite === "hacktwin-core-v1");
  const baseline = route.query.baseline || "1";
  const chosen = (route.query.candidates || "jumpfold-8,multicycle-2phase").split(",").filter(Boolean);
  const benchSel = route.query.benchmarks ? route.query.benchmarks.split(",") : core.map((b) => b.benchmark_id);
  const base = h("select", {}, designs.map((d) => h("option", { value: d.design_id, selected: d.design_id === baseline }, `${d.design_id} — ${d.display_name}`.slice(0, 70))));
  const candBox = h("div", { class: "chips" }, designs.filter((d) => d.kind !== "external").map((d) =>
    h("span", { class: `chip ${chosen.includes(d.design_id) ? "on" : ""}`, dataset: { id: d.design_id }, onclick: (e) => e.currentTarget.classList.toggle("on") }, d.design_id)));
  const extSel = h("select", {}, h("option", { value: "" }, "+ add an imported GitHub design…"), designs.filter((d) => d.kind === "external" && d.ranking).map((d) =>
    h("option", { value: d.design_id, selected: chosen.includes(d.design_id) }, d.design_id)));
  const benchBox = h("div", { class: "chips" }, benches.filter((b) => b.suite === "hacktwin-core-v1").map((b) =>
    h("span", { class: `chip ${benchSel.includes(b.benchmark_id) ? "on" : ""}`, dataset: { id: b.benchmark_id }, onclick: (e) => e.currentTarget.classList.toggle("on") }, b.title)));
  const apply = () => {
    const c = [...candBox.querySelectorAll(".chip.on")].map((x) => x.dataset.id).filter((x) => x !== base.value);
    if (extSel.value) c.push(extSel.value);
    const b = [...benchBox.querySelectorAll(".chip.on")].map((x) => x.dataset.id);
    location.hash = `#/compare?baseline=${encodeURIComponent(base.value)}&candidates=${encodeURIComponent(c.slice(0, 3).join(","))}&benchmarks=${encodeURIComponent(b.join(","))}`;
  };
  const page = h("div", {}, h("div", { class: "page-head" }, h("h1", {}, "Design comparison"),
    h("p", { class: "lede" }, term("speedup", "Speedup"), " = baseline cycles / candidate cycles on the same case; ", term("cycle_reduction", "cycle reduction"),
      " = (baseline − candidate) / baseline × 100. Only valid, correct runs of each design's current revision under an identical case configuration are compared. Fewer simulated cycles is not evidence of faster hardware: clock period is not measured."),
    workflow("compare")),
    card("Select designs and benchmarks",
      h("div", { class: "controls" }, h("label", { class: "field" }, "Baseline", base), h("label", { class: "field" }, "Imported design", extSel)),
      h("div", { class: "field" }, "Candidates (up to 3 shown)", candBox),
      h("div", { class: "field", style: "margin-top:8px" }, "Benchmarks", benchBox),
      h("div", { style: "margin-top:10px" }, h("button", { class: "primary", onclick: apply }, "Compare"))));
  const cands = chosen.filter((c) => c !== baseline).slice(0, 3);
  if (!cands.length) { page.append(empty("Choose at least one candidate design.")); return page; }
  const data = await api(`/api/compare?baseline=${encodeURIComponent(baseline)}&candidates=${encodeURIComponent(cands.join(","))}&benchmarks=${encodeURIComponent(benchSel.join(","))}`);
  const series = data.candidates.map((c) => ({ key: c.design_id, label: c.design_id }));

  page.append(h("div", { class: `grid ${data.candidates.length > 1 ? "g" + Math.min(3, data.candidates.length) : ""}` },
    data.candidates.map((c, i) => summaryCard(c, i, baseline))));

  // Chart: per-case speedup dots
  const caseRows = data.candidates[0].cases.map((row, i) => ({
    label: row.case_id,
    values: Object.fromEntries(data.candidates.map((c) => [c.design_id, c.cases[i].speedup])),
    tips: Object.fromEntries(data.candidates.map((c) => [c.design_id, c.cases[i].status === "comparable" ? `${fmt.int(c.cases[i].baseline.cycles.mean)} → ${fmt.int(c.cases[i].candidate.cycles.mean)} cycles` : c.cases[i].status])),
  }));
  page.append(card(h("div", { class: "card-head" }, h("h2", {}, "Speedup per case"), legend(series)),
    h("p", { class: "small muted" }, "Each dot is one case; the dashed line is the baseline (1.0). Missing dots are non-comparable cases (listed in the table)."),
    ratioDots(caseRows, series)));

  const benchIds = [...new Set(data.candidates[0].cases.map((r) => r.benchmark_id))];
  const totals = benchIds.map((b) => {
    const values = { [baseline]: null };
    const rows0 = data.candidates[0].cases.filter((r) => r.benchmark_id === b && r.baseline && r.baseline.valid_runs);
    values.__base = rows0.reduce((s, r) => s + r.baseline.cycles.mean, 0) || null;
    for (const c of data.candidates) {
      const rs = c.cases.filter((r) => r.benchmark_id === b);
      values[c.design_id] = rs.every((r) => r.status === "comparable") ? rs.reduce((s, r) => s + r.candidate.cycles.mean, 0) : null;
    }
    return { label: b, values };
  });
  const s2 = [{ key: "__base", label: `${baseline} (baseline)`, color: "var(--neutral)" }, ...series.map((sv, i) => ({ ...sv, color: seriesColor(i) }))];
  page.append(card(h("div", { class: "card-head" }, h("h2", {}, "Total cycles per benchmark (sum over its cases)"), legend(s2)),
    groupedBars(totals, s2),
    h("p", { class: "small muted" }, "A bar is omitted when any case of that benchmark is not comparable, so sums never mix complete and incomplete data.")));

  for (const c of data.candidates) page.append(caseTable(c, baseline));
  page.append(card("Metric definitions", h("dl", { class: "kv" }, Object.entries(data.definitions).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))));
  return page;
}

function summaryCard(c, i, baseline) {
  const a = c.aggregate;
  return card(h("div", { class: "card-head" }, h("h2", {}, h("i", { style: `display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;background:${seriesColor(i)}` }), designLink(c.design_id), ` vs ${baseline}`)),
    h("div", { class: "grid g2" },
      h("div", { class: "stat" }, h("span", { class: "label" }, "Geometric-mean speedup"), h("span", { class: "value" }, fmt.x(a.geomean_speedup)), h("span", { class: "sub" }, `range ${fmt.num(a.min_speedup, 3)}–${fmt.num(a.max_speedup, 3)}`)),
      h("div", { class: "stat" }, h("span", { class: "label" }, "Total cycle reduction"), h("span", { class: "value" }, fmt.pct(a.total_cycle_reduction_pct, 2)), h("span", { class: "sub" }, `${fmt.int(a.total_baseline_cycles)} → ${fmt.int(a.total_candidate_cycles)} cycles`))),
    h("p", { class: "small", style: "margin-top:8px" }, `${a.comparable_cases}/${a.total_cases} cases comparable: ${a.fewer_cycles} fewer cycles · ${a.ties} ties · ${a.more_cycles} more cycles. Repeats ${a.repeat_consistent ? "consistent" : "NOT consistent"}.`),
    h("p", { class: "small" }, badge(c.identity.level === "netlist_differs" ? "info" : "neutral", (c.identity.level || "").replaceAll("_", " ")), " ", c.identity.summary),
    notice(c.conclusion, a.comparable_cases < a.total_cases ? "warn" : ""));
}

function caseTable(c, baseline) {
  const st = { comparable: "pass", candidate_failed: "fail", candidate_incorrect: "fail", baseline_failed: "fail", baseline_incorrect: "fail", candidate_missing: "invalid", baseline_missing: "invalid" };
  return card(`${c.design_id} vs ${baseline}: per-case measurements`,
    table([
      { label: "Case", key: "case_id" },
      { label: "Status", value: (r) => badge(st[r.status] || "unknown", r.status.replaceAll("_", " ")) },
      { label: "Baseline cycles", value: (r) => r.baseline?.valid_runs ? fmt.int(r.baseline.cycles.mean) : "—", num: true },
      { label: "Candidate cycles", value: (r) => r.candidate?.valid_runs ? fmt.int(r.candidate.cycles.mean) : "—", num: true },
      { label: "Δ cycles", value: (r) => r.cycle_delta == null ? "—" : `${r.cycle_delta > 0 ? "+" : ""}${fmt.int(r.cycle_delta)}`, num: true },
      { label: "Speedup", value: (r) => fmt.num(r.speedup, 4), num: true },
      { label: "Reduction", value: (r) => fmt.pct(r.cycle_reduction_pct, 2), num: true },
      { label: "CPI (b → c)", value: (r) => r.telemetry ? `${fmt.num(r.telemetry.baseline?.cpi, 2)} → ${fmt.num(r.telemetry.candidate?.cpi, 2)}` : "—" },
      { label: "Writes (b → c)", value: (r) => r.telemetry ? `${r.telemetry.baseline?.memory_writes} → ${r.telemetry.candidate?.memory_writes}` : "—", tip: "Observed memory writes before halt; equal counts are expected for ISA-compatible designs." },
      { label: "A changes (b → c)", value: (r) => r.telemetry ? `${r.telemetry.baseline?.a_changes ?? "—"} → ${r.telemetry.candidate?.a_changes ?? "—"}` : "—" },
      { label: "Repeats", value: (r) => r.candidate ? `${r.candidate.valid_runs}/${r.candidate.runs}${r.candidate.repeat_consistent ? "" : " ⚠"}` : "—", num: true, tip: "Valid runs / all current runs of the candidate; ⚠ = repeats disagree." },
      { label: "Notes", value: (r) => r.status !== "comparable" ? h("span", { class: "small", style: "color:var(--bad)" }, [...(r.candidate?.failure_reasons || []), ...(r.baseline?.failure_reasons || [])].join("; ") || r.status) : r.telemetry ? h("span", { class: "small muted" }, newFindings(r.telemetry)) : "" },
      { label: "Runs", value: (r) => h("span", {}, r.baseline?.representative_run_id ? runLink(r.baseline.representative_run_id, "base") : "", " ", r.candidate?.representative_run_id ? runLink(r.candidate.representative_run_id, "cand") : "") },
    ], c.cases, { maxHeight: "520px" }));
}

function newFindings(t) {
  const a = new Set(t.baseline?.finding_ids || []), b = new Set(t.candidate?.finding_ids || []);
  const add = [...b].filter((x) => !a.has(x)), gone = [...a].filter((x) => !b.has(x));
  return [add.length ? `new: ${add.join(", ")}` : "", gone.length ? `gone: ${gone.join(", ")}` : ""].filter(Boolean).join(" · ");
}
