import { api } from "../api.js";
import { badge, card, designLink, empty, fmt, h, notice, stat, table, term } from "../dom.js";
import { hbar } from "../charts.js";
import { workflow } from "./common.js";

export async function render(route) {
  const o = await api(`/api/overview${route.query.benchmark ? `?benchmark=${encodeURIComponent(route.query.benchmark)}` : ""}`);
  const r = o.runs;
  const page = h("div", {},
    h("div", { class: "page-head" },
      h("h1", {}, "Overview"),
      h("p", { class: "lede" }, "HackTwin runs Hack CPU designs written in HDL through the official nand2tetris Hardware Simulator, records cycle-by-cycle traces, checks results against an independent ISA reference model, and compares designs on identical workloads. Every number on this page comes from stored simulator runs."),
      workflow()));

  page.append(h("div", { class: "grid g4" },
    card(null, stat("CPU designs", fmt.int(o.designs.total), `${o.designs.distinct_source_hashes} distinct source hashes · ${o.designs.distinct_netlists} distinct netlists`, "A source hash changes with any edit; the netlist hash only changes when the wiring of parts changes.")),
    card(null, stat("Benchmarks", fmt.int(o.benchmarks.total), `${o.benchmarks.core} core programs (${o.benchmarks.core_cases} input cases) · ${o.benchmarks.workloads} GitHub workloads`)),
    card(null, stat("Simulation runs (current)", fmt.int(r.current_total || 0),
      `${fmt.int(r.current_succeeded || 0)} completed · ${fmt.int(r.current_failed || 0)} failed · ${fmt.int(r.current_invalid || 0)} did not halt`,
      "Runs of each design's current HDL revision under each case's current configuration.")),
    card(null, stat("Latest simulator activity", o.latest_run_at ? fmt.ago(o.latest_run_at) : "none",
      o.latest_run_at ? fmt.time(o.latest_run_at) : "No timestamped runs yet")),
  ));

  if (r.historical_total) page.append(notice(`${fmt.int(r.historical_total)} older runs (earlier HDL revisions or case configurations, including ${fmt.int(r.historical_failed || 0)} failures) are kept for traceability and excluded from comparisons.`));
  const wrong = r.current_correct_fail || 0;
  page.append(h("div", { class: "grid g2", style: "margin-top:14px" }, rankingCard(o), bestCard(o)));
  page.append(h("div", { class: "grid g2", style: "margin-top:14px" }, findingsCard(o, wrong), coverageCard(o)));
  page.append(h("div", { class: "grid g2", style: "margin-top:14px" }, experimentsCard(o), predictorCard(o)));
  return page;
}

function rankingCard(o) {
  const ranked = o.ranking.ranked;
  if (!ranked.length) return card("Design ranking", empty("No design has complete, correct results yet."));
  const items = ranked.map((d) => ({
    label: `#${d.rank} ${d.design_id}`, value: d.geomean_speedup, valueLabel: fmt.x(d.geomean_speedup),
    color: d.geomean_speedup > 1 + 1e-9 ? "var(--s1)" : d.geomean_speedup < 1 - 1e-9 ? "var(--s2)" : "var(--neutral)",
    tip: `${d.design_id}: geometric-mean speedup ${d.geomean_speedup.toFixed(4)} over ${d.cases} cases (${d.fewer_cycles} fewer cycles, ${d.ties} ties, ${d.more_cycles} more)`,
  }));
  const ties = ranked.filter((d) => d.tied_with.length).map((d) => d.rank);
  return card(h("div", { class: "card-head" }, h("h2", {}, "Design ranking"), h("a", { href: "#/compare" }, "Open comparison →")),
    h("p", { class: "small muted" }, "Geometric-mean ", term("speedup"), ` vs the baseline over ${o.ranking.case_count} core cases. Only designs that are correct on every case are ranked. Dashed line = baseline (1.0).`),
    hbar(items, { reference: 1, valueFmt: (v) => v.toFixed(2), width: 560 }),
    ties.length ? h("p", { class: "small" }, `Tied designs share a rank (rank ${[...new Set(ties)].join(", ")}). A tie is reported as a tie, not broken by another metric.`) : null,
    o.ranking.unranked_count ? h("p", { class: "small muted" }, `${o.ranking.unranked_count} designs are not ranked (missing or incorrect cases) — see Design comparison.`) : null);
}

function bestCard(o) {
  const sel = h("select", { onchange: (e) => (location.hash = `#/overview?benchmark=${e.target.value}`) },
    o.benchmarks.list.map((b) => h("option", { value: b.benchmark_id, selected: b.benchmark_id === o.selected_benchmark }, b.title)));
  const best = o.best_for_benchmark;
  let body;
  if (!best || !best.top) body = empty("No valid, correct results for this benchmark.");
  else {
    const tied = best.tied_at_top;
    body = h("div", {},
      h("p", {}, tied.length > 1 ? `${tied.length} designs tie for fewest cycles: ` : "Fewest cycles: ",
        ...tied.slice(0, 6).flatMap((d, i) => [i ? ", " : "", designLink(d)]), tied.length > 6 ? ` and ${tied.length - 6} more` : "",
        ` — speedup ${fmt.x(best.top.geomean_speedup)} over ${best.cases.length} case(s) (total ${fmt.int(best.top.total_cycles)} cycles).`),
      table([
        { label: "Rank", value: (d) => `#${d.rank}`, num: true },
        { label: "Design", value: (d) => designLink(d.design_id) },
        { label: "Total cycles", value: (d) => fmt.int(d.total_cycles), num: true },
        { label: "Speedup", value: (d) => fmt.x(d.geomean_speedup), num: true, tip: "Geometric mean of baseline cycles / design cycles over this benchmark's cases." },
      ], best.ranked.filter((d) => !d.design_id.startsWith("gh-")).concat(best.ranked.filter((d) => d.design_id.startsWith("gh-")).slice(0, 5)), { maxHeight: "260px" }),
      best.ranked.length > 13 ? h("p", { class: "small muted" }, `Showing HackTwin designs and the first imported designs of ${best.ranked.length} ranked.`) : null);
  }
  return card(h("div", { class: "card-head" }, h("h2", {}, "Best valid design for a benchmark"), sel), body,
    h("p", { class: "small muted" }, "Fewer simulated cycles; clock period, area and power are not measured."));
}

function findingsCard(o, wrong) {
  const rows = o.baseline_findings;
  return card(h("div", { class: "card-head" }, h("h2", {}, "Detected bottleneck signals (baseline)"), h("a", { href: "#/bottlenecks" }, "Analyse →")),
    h("p", { class: "small muted" }, `Findings from the baseline's ${o.baseline_cases_analysed} core cases. Observations describe the trace; heuristics suggest an experiment; neither is a confirmed defect.`),
    rows.length ? table([
      { label: "Finding", value: (f) => f.title },
      { label: "Type", value: (f) => badge(f.kind) },
      { label: "Cases", key: "cases", num: true },
    ], rows, { maxHeight: "260px" }) : empty("No findings."),
    wrong ? notice(`${wrong} current runs produced wrong results.`, "bad") : h("p", { class: "small" }, badge("pass", "all completed current runs produced correct results")));
}

function coverageCard(o) {
  const d = o.dataset || {};
  const c = d.candidates || {}, rp = d.repositories || {};
  const conf = o.conformance;
  return card(h("div", { class: "card-head" }, h("h2", {}, "Dataset coverage and validation"), h("a", { href: "#/dataset" }, "Details →")),
    h("h3", {}, "Official conformance tests"),
    h("p", {}, `${fmt.int(conf.passed || 0)} passed · ${fmt.int(conf.comparison_failed || 0)} failed comparison · ${fmt.int(conf.error || 0)} errors · ${fmt.int(conf.not_applicable || 0)} not applicable`),
    h("h3", {}, "GitHub HDL dataset"),
    rp.discovered ? table([{ label: "Stage", key: "s" }, { label: "Count", key: "n", num: true }], [
      { s: "Repositories discovered", n: fmt.int(rp.discovered) },
      { s: "Repositories retrieved", n: fmt.int(rp.retrieved) },
      { s: "Candidate designs (CPU.hdl folders)", n: fmt.int(c.retrieved) },
      { s: "Structurally valid", n: fmt.int(c.structurally_valid) },
      { s: "Unique (not duplicates)", n: fmt.int(c.unique_structurally_valid) },
      { s: "Simulator-compatible", n: fmt.int(c.simulator_compatible) },
      { s: "Correct on all tests", n: fmt.int(c.correct) },
      { s: "Complete & comparable", n: fmt.int(c.complete) },
    ]) : empty("GitHub ingestion has not been run."));
}

function experimentsCard(o) {
  return card(h("div", { class: "card-head" }, h("h2", {}, "Recent experiments"), h("a", { href: "#/dataset" }, "All →")),
    o.experiments.length ? table([
      { label: "Experiment", value: (e) => e.title },
      { label: "When", value: (e) => fmt.ago(e.created_at) },
      { label: "Runs", value: (e) => fmt.int(e.summary?.runs_attempted), num: true },
      { label: "Status", value: (e) => badge(e.status) },
    ], o.experiments) : empty("No experiments recorded."));
}

function predictorCard(o) {
  const p = o.predictor;
  if (!p.trained) return card("Performance predictor", empty("Not trained yet."));
  const rows = Object.entries(p.macro_mae).map(([k, v]) => ({ k, v }));
  return card(h("div", { class: "card-head" }, h("h2", {}, "Performance predictor"), h("a", { href: "#/predictor" }, "Evaluation →")),
    h("p", { class: "small muted" }, `${fmt.int(p.rows)} (design, case) rows from ${p.programs} programs. Leave-programs-out `, term("prediction_error", "mean absolute error"), " in cycles, averaged over designs:"),
    table([{ label: "Predictor", key: "k" }, { label: "MAE (cycles)", value: (r) => fmt.num(r.v, 2), num: true }], rows));
}
