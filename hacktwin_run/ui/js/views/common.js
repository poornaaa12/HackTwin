// Shared widgets: workflow strip and a design/case run picker.
import { api } from "../api.js";
import { h } from "../dom.js";

export function workflow(active) {
  const steps = [["designs", "Choose a CPU design"], ["workbench", "Choose a benchmark & run"], ["workbench", "Validate correctness"],
    ["trace", "Inspect telemetry"], ["bottlenecks", "Analyse bottlenecks"], ["compare", "Compare designs"], ["recommendations", "Review recommendations"]];
  const el = h("nav", { class: "flow", "aria-label": "Guided workflow" });
  steps.forEach(([id, label], i) => {
    if (i) el.append(h("span", { class: "arrow" }, "→"));
    el.append(h("a", { href: `#/${id}`, style: id === active ? "border-color:var(--accent);color:var(--accent-text)" : null }, label));
  });
  return el;
}

let designsCache = null, benchCache = null;
export async function designsList() { return (designsCache ||= api("/api/designs").then((d) => d.designs)); }
export async function benchmarksList() { return (benchCache ||= api("/api/benchmarks").then((d) => d.benchmarks)); }
export function invalidateCaches() { designsCache = null; benchCache = null; }

export function designOptions(designs, selected, { includeExternal = true } = {}) {
  const groups = {};
  for (const d of designs) {
    if (!includeExternal && d.kind === "external") continue;
    const g = d.kind === "external" ? "Imported from GitHub" : "HackTwin designs";
    (groups[g] ||= []).push(d);
  }
  return Object.entries(groups).map(([g, ds]) => h("optgroup", { label: g }, ds.map((d) =>
    h("option", { value: d.design_id, selected: d.design_id === selected }, `${d.design_id} — ${d.display_name}`.slice(0, 90)))));
}

export function caseOptions(benchmarks, selected, { coreOnly = false } = {}) {
  return benchmarks.filter((b) => !coreOnly || b.suite === "hacktwin-core-v1").map((b) =>
    h("optgroup", { label: `${b.title}${b.suite === "hacktwin-core-v1" ? "" : " (GitHub workload)"}` },
      b.cases.map((c) => h("option", { value: c.case_id, selected: c.case_id === selected }, c.case_id))));
}

// Find the representative current run for (design, case).
export async function currentRun(design, caseId) {
  const res = await api(`/api/runs?design=${encodeURIComponent(design)}&case=${encodeURIComponent(caseId)}&scope=current&limit=20`);
  const runs = res.runs.slice().sort((a, b) => (a.status === "succeeded" ? 0 : 1) - (b.status === "succeeded" ? 0 : 1) || a.repeat_index - b.repeat_index);
  return runs[0] || null;
}

// Picker bar: design + case selects; calls onPick(runId) for the current run.
export async function runPicker({ design = "1", caseId = "mult-25-40", onPick, target }) {
  const [designs, benches] = await Promise.all([designsList(), benchmarksList()]);
  const dSel = h("select", { "aria-label": "Design" }, designOptions(designs, design));
  const cSel = h("select", { "aria-label": "Benchmark case" }, caseOptions(benches, caseId));
  const msg = h("span", { class: "muted small" });
  const go = async () => {
    msg.textContent = "Finding run…";
    const run = await currentRun(dSel.value, cSel.value);
    msg.textContent = run ? "" : "No current run for this design and case yet. Run it in the workbench.";
    onPick(run, dSel.value, cSel.value);
  };
  const bar = h("div", { class: "controls" },
    h("label", { class: "field" }, "CPU design", dSel), h("label", { class: "field" }, "Benchmark case", cSel),
    h("button", { class: "primary", onclick: go }, target || "Open"), msg);
  return { bar, go, dSel, cSel };
}
