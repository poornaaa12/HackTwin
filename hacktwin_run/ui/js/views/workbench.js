import { api, post } from "../api.js";
import { badge, card, clear, designLink, empty, fmt, h, kv, notice, runLink, table, term } from "../dom.js";
import { benchmarksList, designOptions, designsList, invalidateCaches, workflow } from "./common.js";

export async function render(route) {
  if (route.parts[0] === "run" && route.parts[1]) return renderRun(route.parts[1]);
  const [designs, benches] = await Promise.all([designsList(), benchmarksList()]);
  const dSel = h("select", {}, designOptions(designs, route.query.design || "1"));
  const bSel = h("select", {}, benches.map((b) => h("option", { value: b.benchmark_id, selected: b.benchmark_id === (route.query.benchmark || "mult") },
    `${b.title}${b.suite === "hacktwin-core-v1" ? "" : " — GitHub workload"}`)));
  const caseBox = h("div", { class: "chips" });
  const reps = h("input", { type: "number", min: 1, max: 5, value: 2, style: "width:80px;min-width:0" });
  const conf = h("input", { type: "checkbox" });
  const jobArea = h("div");
  const results = h("div");
  const bench = () => benches.find((b) => b.benchmark_id === bSel.value);

  function renderCases() {
    clear(caseBox);
    for (const c of bench().cases) {
      const chip = h("span", { class: "chip on", dataset: { case: c.case_id }, title: `inputs ${JSON.stringify(c.inputs)}` }, c.case_id);
      chip.addEventListener("click", () => chip.classList.toggle("on"));
      caseBox.append(chip);
    }
  }
  async function renderResults() {
    clear(results).append(h("p", { class: "muted" }, "Loading results…"));
    const res = await api(`/api/runs?design=${encodeURIComponent(dSel.value)}&benchmark=${encodeURIComponent(bSel.value)}&scope=current&limit=500`);
    clear(results).append(resultsTable(res.runs, bench()));
  }
  async function launch() {
    const cases = [...caseBox.querySelectorAll(".chip.on")].map((c) => c.dataset.case);
    if (!cases.length) { clear(jobArea).append(notice("Select at least one case.", "warn")); return; }
    try {
      const job = await post("/api/jobs", { kind: "simulate", design_ids: [dSel.value], case_ids: cases, repeats: Number(reps.value), conformance: conf.checked });
      pollJob(job.job_id);
    } catch (err) { clear(jobArea).append(notice(err.message, "bad")); }
  }
  async function pollJob(id) {
    const j = await api(`/api/jobs/${id}`);
    const p = j.progress || {};
    clear(jobArea).append(card(h("div", { class: "card-head" }, h("h2", {}, "Simulation job"), badge(j.status)),
      h("p", { class: "small muted" }, `${id} · ${j.params?.title || ""}`),
      p.total ? h("div", {}, h("div", { class: "progress" }, h("div", { style: `width:${(100 * p.done) / Math.max(1, p.total)}%` })),
        h("p", { class: "small" }, `${p.done} of ${p.total} simulator invocations finished — ${p.message || ""}`)) : h("p", { class: "small" }, j.status === "queued" ? "Waiting for the job worker…" : "Starting…"),
      j.status === "failed" ? notice(j.error || "Job failed", "bad") : null,
      j.status === "finished" ? notice(`Finished: ${fmt.int(j.result.runs_attempted)} runs — ${Object.entries(j.result.by_status || {}).map(([k, v]) => `${v} ${k}`).join(", ") || "none"}; correctness ${Object.entries(j.result.by_correctness || {}).map(([k, v]) => `${v} ${k}`).join(", ")}.`,
        Object.keys(j.result.by_status || {}).some((k) => k !== "succeeded") ? "warn" : "good") : null));
    if (j.status === "queued" || j.status === "running") setTimeout(() => location.hash.startsWith("#/workbench") && pollJob(id), 800);
    else { invalidateCaches(); renderResults(); }
  }
  bSel.addEventListener("change", () => { renderCases(); renderResults(); });
  dSel.addEventListener("change", renderResults);
  renderCases(); renderResults();

  return h("div", {},
    h("div", { class: "page-head" }, h("h1", {}, "Simulation workbench"),
      h("p", { class: "lede" }, "Run a benchmark on a design in the real Hardware Simulator. Each run executes until the program reaches its halt address; HackTwin then checks the result against the expected values and against the ISA reference model's memory writes."),
      workflow("workbench")),
    card("Configure a run",
      h("div", { class: "controls" }, h("label", { class: "field" }, "CPU design", dSel), h("label", { class: "field" }, "Benchmark", bSel),
        h("label", { class: "field" }, "Repeats", reps),
        h("label", { class: "field", style: "flex-direction:row;align-items:center;gap:6px" }, conf, "Also run official conformance tests"),
        h("button", { class: "primary", onclick: launch }, "Run simulation")),
      h("div", { class: "field" }, "Input cases (click to toggle)", caseBox),
      h("p", { class: "small muted", style: "margin-top:8px" }, "Repeats check determinism: identical repeats must produce byte-identical traces. Runs execute as a background job; this page stays responsive.")),
    jobArea,
    card("Current results for this design and benchmark", results));
}

function resultsTable(runs, bench) {
  if (!runs.length) return empty("No current runs yet. Choose cases and press Run simulation.");
  runs.sort((a, b) => a.case_id.localeCompare(b.case_id) || a.repeat_index - b.repeat_index);
  return table([
    { label: "Case", value: (r) => runLink(r.run_id, r.case_id) },
    { label: "Repeat", key: "repeat_index", num: true },
    { label: "Status", value: (r) => badge(r.status) },
    { label: "Result", value: (r) => r.status === "succeeded" ? badge(r.correctness) : "—" },
    { label: "Cycles to halt", value: (r) => fmt.int(r.cycles_to_halt), num: true, tip: "Simulated clock cycles until the program first reached its halt address." },
    { label: "ISA instructions", value: (r) => fmt.int(r.reference_instructions), num: true },
    { label: "CPI", value: (r) => r.cycles_to_halt && r.reference_instructions ? fmt.num(r.cycles_to_halt / r.reference_instructions, 3) : "—", num: true },
    { label: "Host time (s)", value: (r) => fmt.num(r.elapsed_s, 2), num: true, tip: "Wall-clock time of the simulator process including Java start-up. Not hardware time." },
    { label: "Failure", value: (r) => r.failure_reason ? h("span", { class: "small", style: "color:var(--bad)" }, r.failure_reason) : "" },
  ], runs, { onRow: (r) => (location.hash = `#/workbench/run/${encodeURIComponent(r.run_id)}`), maxHeight: "460px" });
}

async function renderRun(runId) {
  const data = await api(`/api/runs/${encodeURIComponent(runId)}`);
  if (data.legacy) return legacyRun(data);
  const r = data.run, det = r.details || {}, c = data.case || {};
  const eq = det.architectural_equivalence;
  const values = Object.entries(det.values || {});
  const ok = r.status === "succeeded" && r.correctness === "pass";
  const enc = encodeURIComponent(runId);
  const statusNote = r.status === "failed" ? notice(`The simulation did not complete: ${r.failure_reason}`, "bad")
    : r.status === "invalid" ? notice(`Measurement invalid: ${r.failure_reason}`, "warn")
    : r.correctness === "fail" ? notice(`The program produced a wrong result: ${r.failure_reason}`, "bad")
    : notice("The run completed, reached its halt address, and every expected value matched.", "good");
  return h("div", {},
    h("div", { class: "page-head" },
      h("div", { class: "small" }, h("a", { href: `#/workbench?design=${encodeURIComponent(r.design_id)}&benchmark=${encodeURIComponent(r.benchmark_id)}` }, "← Workbench")),
      h("h1", {}, `${r.case_id} on design ${r.design_id}`),
      h("p", { class: "lede" }, data.benchmark?.description || ""),
      h("div", { class: "chips" }, badge(r.status), r.status === "succeeded" ? badge(r.correctness, `result ${r.correctness}`) : null,
        data.is_current_config && r.is_current_revision ? badge("pass", "current revision & config") : badge("invalid", "historical run"),
        h("a", { class: "chip", href: `#/trace/${enc}` }, "Trace explorer"), h("a", { class: "chip", href: `#/bottlenecks/${enc}` }, "Bottleneck analysis"),
        h("a", { class: "chip", href: `#/compare?candidates=${encodeURIComponent(r.design_id)}&benchmarks=${encodeURIComponent(r.benchmark_id)}` }, "Compare designs"),
        h("a", { class: "chip", href: `#/recommendations/${encodeURIComponent(r.design_id)}?run=${enc}` }, "Recommendations"))),
    statusNote,
    h("div", { class: "grid g2" },
      card("Measurement", kv([
        ["Design", designLink(r.design_id)], ["Benchmark", `${data.benchmark?.title || r.benchmark_id} (${r.benchmark_id})`],
        [term("cycle_count", "Cycles to halt"), r.cycles_to_halt != null ? `${fmt.int(r.cycles_to_halt)} simulated clock cycles` : "not measured"],
        ["ISA reference instructions", fmt.int(r.reference_instructions)],
        [term("cpi", "CPI"), r.cycles_to_halt && r.reference_instructions ? fmt.num(r.cycles_to_halt / r.reference_instructions, 4) : "—"],
        ["Cycle budget", `${fmt.int(c.max_cycles)} cycles (4 × reference + 64)`],
        ["Host wall-clock", `${fmt.num(r.elapsed_s, 3)} s (simulator process incl. Java start-up; not hardware time)`],
        ["Repeat", r.repeat_index], ["Started", fmt.time(r.started_at)],
        ["HDL revision", h("span", { class: "hash" }, fmt.hash(r.run_hdl_hash, 16))],
        ["Case config hash", h("span", { class: "hash" }, fmt.hash(r.config_hash, 16))],
        ["Trace SHA-256", h("span", { class: "hash" }, fmt.hash(r.trace_sha256, 16))],
      ])),
      card("Correctness: expected vs actual",
        values.length ? table([
          { label: "Address", value: ([a]) => `RAM[${a}]` },
          { label: "Expected", value: ([, v]) => v.expected, num: true },
          { label: "Actual", value: ([, v]) => v.actual, num: true },
          { label: "", value: ([, v]) => badge(v.match ? "pass" : "fail", v.match ? "match" : "mismatch") },
          { label: "Source", value: ([, v]) => h("span", { class: "small" }, v.source === "observed_ram_probe" ? "observed RAM probe" : "reconstructed from write bus") },
        ], values, { maxHeight: "280px" }) : empty("No result values (the run did not produce a trace)."),
        h("p", { class: "small muted" }, `Expected values: ${c.expected_source || (c.reference ? "specification" : "—")}. Input values: ${JSON.stringify(c.inputs || {})}.`),
        eq ? h("p", {}, eq.equivalent ? badge("pass", "architecturally equivalent") : badge("fail", "writes diverge"),
          ` ${eq.observed} memory writes observed vs ${eq.reference} in the ISA reference model`, eq.equivalent ? "" : ` — first difference at write #${eq.first_divergence}: observed ${JSON.stringify(eq.observed_at)} vs reference ${JSON.stringify(eq.reference_at)}`) : null)),
    card("Repeat-run consistency",
      h("p", {}, data.repeat_consistency.consistent ? badge("pass", "consistent") : badge("fail", "inconsistent"),
        ` ${data.repeat_consistency.succeeded} of ${data.repeat_consistency.repeats} repeats completed; distinct cycle counts: ${data.repeat_consistency.distinct_cycle_counts.join(", ") || "—"}; distinct trace hashes: ${data.repeat_consistency.distinct_traces}.`),
      table([
        { label: "Run", value: (s) => s.run_id === runId ? h("strong", {}, `r${s.repeat_index} (this run)`) : runLink(s.run_id, `r${s.repeat_index}`) },
        { label: "Status", value: (s) => badge(s.status) },
        { label: "Cycles", value: (s) => fmt.int(s.cycles_to_halt), num: true },
        { label: "Trace SHA-256", value: (s) => h("span", { class: "hash" }, fmt.hash(s.trace_sha256, 16)) },
        { label: "When", value: (s) => fmt.time(s.started_at) },
      ], data.repeats)),
    r.trace ? card("Recorded signals", h("p", { class: "small" }, `Columns: ${r.trace.columns.join(", ")}. ${fmt.int(r.trace.row_count)} rows stored of ${fmt.int(r.trace.total_rows)} simulated${r.trace.truncated_after_halt ? " (rows after the halt loop are not stored)" : ""}.`),
      det.register_probes === false ? notice("This CPU does not use the ARegister/DRegister built-ins, so A and D are not observable.", "warn") : null) : null,
    r.status === "failed" ? card("Simulator diagnostics", h("pre", { class: "code" }, [det.simulator_message, det.stdout_tail, det.stderr_tail].filter(Boolean).join("\n\n") || "No output captured.")) : null);
}

function legacyRun(data) {
  const r = data.run;
  return h("div", {}, h("h1", {}, r.run_id),
    notice("This is an official .tst run from the original Phase 1 pipeline. Its cycle count is the number of rows the test script prints (fixed by `repeat N`), so it is the same for every design and is not a performance measurement.", "warn"),
    card("Run", kv([["Design", designLink(r.design_id)], ["Benchmark", r.benchmark_id], ["Status", badge(r.status)], ["Comparison", badge(r.correctness)], ["Script rows", r.cycle_count]])));
}
