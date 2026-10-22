"use strict";

const $ = (id) => document.getElementById(id);
const state = { runs: [], designs: [], programs: [] };

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function error(message = "") {
  const box = $("app-error");
  box.textContent = message;
  box.hidden = !message;
}

function cell(row, value, tag = "td") {
  const node = document.createElement(tag);
  node.textContent = value == null ? "—" : String(value);
  row.appendChild(node);
  return node;
}

function option(select, value, label) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label;
  select.appendChild(node);
}

function setMetric(container, label, value) {
  const box = document.createElement("article");
  box.className = "metric";
  const key = document.createElement("span"); key.textContent = label;
  const number = document.createElement("strong"); number.textContent = value ?? "—";
  box.append(key, number); container.appendChild(box);
}

function addParagraph(container, text, className = "") {
  const node = document.createElement("p");
  node.textContent = text;
  if (className) node.className = className;
  container.appendChild(node);
  return node;
}

async function loadOverview() {
  const [summary, runs, designs] = await Promise.all([
    api("/api/summary"), api("/api/runs"), api("/api/designs"),
  ]);
  state.runs = runs;
  state.designs = designs;
  $("stat-designs").textContent = summary.design_count;
  $("stat-runs").textContent = summary.run_count;
  $("stat-benchmarks").textContent = new Set(runs.map((run) => run.benchmark_id)).size;

  const findings = $("current-findings"); findings.replaceChildren();
  const baseline = designs.find((design) => !design.parent_design_id) || designs[0];
  const variants = designs.filter((design) => design.design_id !== baseline?.design_id);
  if (!baseline || !variants.length) {
    addParagraph(findings, "Add at least two CPU versions and run the same test programs to compare their results.");
  } else {
    const comparisonResults = [];
    for (const variant of variants) {
      const comparison = await api(`/api/compare?designs=${encodeURIComponent(`${baseline.design_id},${variant.design_id}`)}`);
      const rows = comparison.benchmark_comparisons || [];
      const allVerified = rows.length > 0 && rows.every((row) => row.outputs_verified);
      const allMatch = rows.length > 0 && rows.every((row) => row.outputs_match);
      const sameCycles = rows.length > 0 && rows.every((row) => row.cycle_delta === 0);
      comparisonResults.push({ rows, allVerified, allMatch, sameCycles });
      const block = document.createElement("div"); block.className = "finding";
      const title = document.createElement("h4"); title.textContent = `CPU version ${variant.design_id} compared with baseline ${baseline.design_id}`;
      block.appendChild(title);
      if (!rows.length) {
        addParagraph(block, "These versions do not have any test programs in common yet.");
      } else {
        const outputText = allVerified
          ? (allMatch ? "Saved outputs were verified and agree on every shared test." : "At least one verified output differs; investigate before interpreting the cycle counts.")
          : "Some saved outputs could not be verified from the available records.";
        const cycleText = sameCycles
          ? "Both versions used the same number of simulator steps on every shared test."
          : "At least one shared test used a different number of simulator steps.";
        addParagraph(block, `${rows.length} shared test programs. ${outputText} ${cycleText}`);
      }
      findings.appendChild(block);
    }
    const haveComparisons = comparisonResults.every((result) => result.rows.length > 0);
    const allVerifiedAndMatching = haveComparisons && comparisonResults.every((result) => result.allVerified && result.allMatch);
    const allCycleCountsEqual = haveComparisons && comparisonResults.every((result) => result.sameCycles);
    const takeaway = document.createElement("p"); takeaway.className = "takeaway";
    if (allVerifiedAndMatching && allCycleCountsEqual) {
      takeaway.textContent = "Current takeaway: saved outputs agree and these versions use the same number of simulator steps on the tests they share. The project demonstrates comparison and correctness checks; this dataset does not show a cycle-count improvement.";
    } else {
      takeaway.textContent = "Current takeaway: read each comparison above. Any unverified or different output needs investigation before cycle counts are interpreted.";
    }
    findings.appendChild(takeaway);
  }
}

function fillSelect(select, records, valueOf, labelOf) {
  select.replaceChildren();
  for (const record of records) option(select, valueOf(record), labelOf(record));
}

async function loadRuns() {
  state.runs = await api("/api/runs");
  const label = (run) => `CPU ${run.design_id} · ${run.benchmark_id} · repeat ${run.repeat_index}`;
  for (const id of ["run-select", "review-run-select"]) fillSelect($(id), state.runs, (r) => r.run_id, label);
  if (state.runs.length) { await loadRun(); await loadReview(); }
}

async function loadRun() {
  const runId = $("run-select").value;
  if (!runId) return;
  const record = await api(`/api/runs/${encodeURIComponent(runId)}`);
  const summary = $("run-summary"); summary.replaceChildren();
  setMetric(summary, "Run ID", record.run_id);
  setMetric(summary, "CPU version / test", `CPU ${record.design_id} / ${record.benchmark_id}`);
  setMetric(summary, "Measured simulator steps", record.cycle_count);
  const output = record.program_output;
  setMetric(summary, "Recorded output or check", output?.value ?? "No output recorded");
  const body = $("trace-body"); body.replaceChildren();
  const trace = record.instruction_trace || [];
  const registers = record.register_snapshot || [];
  for (let i = 0; i < Math.min(trace.length, 100); i++) {
    const row = document.createElement("tr");
    cell(row, trace[i].cycle ?? i); cell(row, trace[i].pc);
    cell(row, trace[i].opcode || "not captured");
    cell(row, registers[i]?.A); cell(row, registers[i]?.D); body.appendChild(row);
  }
  if (!trace.length) body.innerHTML = '<tr><td colspan="5">This saved run has no instruction trace.</td></tr>';
}

async function loadDesigns() {
  state.designs = await api("/api/designs");
  fillSelect($("design-a"), state.designs, (d) => d.design_id, (d) => `CPU version ${d.design_id}`);
  fillSelect($("design-b"), state.designs, (d) => d.design_id, (d) => `CPU version ${d.design_id}`);
  if (state.designs.length > 1) $("design-b").selectedIndex = 1;
}

async function compareDesigns() {
  const target = $("comparison-output"); target.textContent = "Comparing saved simulator results…";
  const a = $("design-a").value, b = $("design-b").value;
  const result = await api(`/api/compare?designs=${encodeURIComponent(`${a},${b}`)}`);
  target.replaceChildren();
  const title = document.createElement("h3"); title.textContent = `CPU version ${a} compared with CPU version ${b}`; target.appendChild(title);
  const rows = result.benchmark_comparisons || [];
  if (!rows.length) {
    addParagraph(target, "These versions have no shared test programs in the saved results.");
    return;
  }
  const allVerified = rows.every((item) => item.outputs_verified);
  const allMatch = rows.every((item) => item.outputs_match);
  const sameCycles = rows.every((item) => item.cycle_delta === 0);
  let summaryText = `${rows.length} shared test programs. `;
  summaryText += allVerified ? (allMatch ? "All saved outputs are verified and match. " : "At least one verified output differs. ") : "Not every output is verified in the saved data. ";
  summaryText += sameCycles ? "The cycle counts are equal on these tests." : "Some cycle counts differ; this is a difference in simulated steps only.";
  addParagraph(target, summaryText, "comparison-summary");
  const table = document.createElement("table");
  const head = document.createElement("tr");
  ["Test program", `CPU ${a} · measured steps`, `CPU ${b} · measured steps`, "Saved output check"].forEach((x) => cell(head, x, "th"));
  const thead = document.createElement("thead"); thead.appendChild(head); table.appendChild(thead);
  const tbody = document.createElement("tbody");
  for (const item of rows) {
    const row = document.createElement("tr"); cell(row, item.benchmark_id);
    cell(row, Number(item.design_a_avg_cycles).toFixed(1)); cell(row, Number(item.design_b_avg_cycles).toFixed(1));
    cell(row, !item.outputs_verified ? "Not verified" : (item.outputs_match ? "Verified · match" : "Verified · mismatch"));
    tbody.appendChild(row);
  }
  table.appendChild(tbody); target.appendChild(table);
}

function explainModelStatus(status) {
  const box = $("model-status"); box.replaceChildren();
  const heading = document.createElement("h3");
  if (!status.ready) {
    heading.textContent = "No trained model is available";
    box.appendChild(heading);
    addParagraph(box, status.message || "Collect programs, generate simulator labels, then train the model.");
    return;
  }

  heading.textContent = "Yes — one model has been trained";
  box.appendChild(heading);
  const algorithm = status.algorithm || "Extra Trees regression model";
  addParagraph(box, `Model type: ${algorithm}. It is one regression model, not a collection of neural networks.`);
  addParagraph(box, `Training data: ${status.training_samples} simulator-measured examples from ${status.program_group_count} different programs, collected from ${status.source_repository_count} public GitHub repositories.`);
  addParagraph(box, "What it predicts: simulator cycles until a repeated terminal loop is seen in the program-counter trace. It does not predict physical CPU speed.");

  const cv = status.validation?.group_cross_validation;
  if (cv) {
    const lowerMae = cv.mae_cycles < cv.median_baseline_mae_cycles;
    const p90 = status.cv_absolute_error_p90_cycles;
    const validation = document.createElement("div"); validation.className = "validation-box";
    const label = document.createElement("b"); label.textContent = "How well did it validate on held-out programs?";
    validation.appendChild(label);
    addParagraph(validation, `Average absolute prediction miss: ${cv.mae_cycles} cycles. A simple median guess misses by ${cv.median_baseline_mae_cycles} cycles on average. The model is ${lowerMae ? "better on this error measure" : "not better on this error measure"}, but the errors remain large.`);
    addParagraph(validation, `R² score: ${cv.r2}. A negative score means the model explains these unseen program results poorly. 90th-percentile absolute error: about ${p90} cycles (9 out of 10 validation errors were no larger than this).`, "warning-text");
    box.appendChild(validation);
  }
}

async function loadModelInfo() {
  const [status, workloads] = await Promise.all([api("/api/ml/status"), api("/api/ml/workloads")]);
  explainModelStatus(status);
  const overview = $("model-overview"); overview.replaceChildren();
  if (status.ready) {
    const answer = document.createElement("p"); answer.className = "answer"; answer.textContent = "Yes. One Extra Trees regression model was trained.";
    const metric = status.validation?.group_cross_validation;
    addParagraph(overview, `It learned from ${status.training_samples} simulator-measured examples across ${status.program_group_count} programs. Its grouped validation average error was ${metric?.mae_cycles ?? "—"} cycles, versus ${metric?.median_baseline_mae_cycles ?? "—"} for a simple baseline.`);
    addParagraph(overview, `Its R² is ${metric?.r2 ?? "—"}, so treat estimates as experimental. The ML estimate tab explains the target, data, and validation in more detail.`);
    overview.prepend(answer);
  } else {
    addParagraph(overview, status.message || "No trained model is currently available.");
  }
  state.programs = workloads.programs || [];
  fillSelect($("program-select"), state.programs, (p) => p.program_id, (p) => `${p.program_id} · ${p.label_count} measured cases`);
  if (!state.programs.length) $("program-select").disabled = true;
}

async function predictCycles(event) {
  event.preventDefault();
  const target = $("prediction-output"); target.textContent = "Calculating an estimate…";
  const result = await api("/api/predict", { method: "POST", body: JSON.stringify({
    program_id: $("program-select").value,
    ram0: Number($("ram0-input").value), ram1: Number($("ram1-input").value),
  }) });
  target.replaceChildren();
  const title = document.createElement("h3"); title.textContent = `Model estimate: ${result.predicted_cycles_to_terminal_loop} simulator cycles`; target.appendChild(title);
  addParagraph(target, `Approximate validation-error range: ${result.cross_validation_90pct_error_band.lower} to ${result.cross_validation_90pct_error_band.upper} cycles. This range summarizes past validation errors; it is not a guarantee for this program.`, "warning-text");
  addParagraph(target, "A simulation run is needed to get the measured cycle count for this exact input.");
}

async function loadReview() {
  const runId = $("review-run-select").value;
  if (!runId) return;
  const report = await api(`/api/recommendations/${encodeURIComponent(runId)}`);
  const target = $("review-output"); target.replaceChildren();
  const recommendations = report.recommendations || [];
  if (!recommendations.length) {
    addParagraph(target, "No review notes were generated for this trace.");
    return;
  }
  for (const item of recommendations) {
    const section = document.createElement("section"); section.className = "signal";
    const heading = document.createElement("h3"); heading.textContent = item.title;
    const tag = document.createElement("span"); tag.className = "tag"; tag.textContent = `${item.category} · ${item.severity}`;
    const evidence = document.createElement("p"); evidence.textContent = `What the trace showed: ${JSON.stringify(item.evidence)}`;
    const meaning = document.createElement("p"); meaning.textContent = `What it may mean: ${item.interpretation}`;
    const action = document.createElement("p"); action.textContent = `What to check next: ${item.suggested_action}`;
    const verify = document.createElement("p"); verify.textContent = `How to check a change: ${item.validation_step}`;
    section.append(heading, tag, evidence, meaning, action, verify); target.appendChild(section);
  }
}

function showError(promise) { return promise.catch((e) => { error(e.message); }); }

document.querySelectorAll(".nav-button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".nav-button").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${button.dataset.view}`));
  error();
}));
$("refresh-btn").addEventListener("click", () => showError(initialize()));
$("run-select").addEventListener("change", () => showError(loadRun()));
$("review-run-select").addEventListener("change", () => showError(loadReview()));
$("compare-btn").addEventListener("click", () => showError(compareDesigns()));
$("predict-form").addEventListener("submit", (event) => showError(predictCycles(event)));

async function initialize() {
  error();
  try {
    await Promise.all([loadOverview(), loadRuns(), loadDesigns(), loadModelInfo()]);
    $("service-status").className = "status-dot ok";
    $("status-text").textContent = "Connected to local simulator data";
  } catch (e) {
    $("service-status").className = "status-dot bad";
    $("status-text").textContent = "Local data unavailable";
    error(`${e.message}. Start the dashboard with: python3 app.py --port 8080`);
  }
}
initialize();
