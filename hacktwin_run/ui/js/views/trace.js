import { api } from "../api.js";
import { badge, card, clear, empty, fmt, h, notice, runLink, table, tabs, term } from "../dom.js";
import { columns, pcTimeline } from "../charts.js";
import { runPicker, workflow } from "./common.js";

const PAGE = 100;

export async function render(route) {
  const runId = route.parts[0];
  const page = h("div", {}, h("div", { class: "page-head" }, h("h1", {}, "Trace explorer"),
    h("p", { class: "lede" }, "Cycle-by-cycle signals recorded by the simulator through the TwinComputer harness. ",
      term("observed_inferred", "Observed"), " columns are simulator signals; inferred columns are decoded by HackTwin. Traces are loaded in windows of 100 cycles."),
    workflow("trace")));
  const picker = await runPicker({ onPick: (run) => run && (location.hash = `#/trace/${encodeURIComponent(run.run_id)}`), target: "Open trace" });
  page.append(card(null, picker.bar));
  if (!runId) { page.append(empty("Choose a design and a benchmark case to open its trace.")); return page; }
  const [detail, pcs, analysis] = await Promise.all([
    api(`/api/runs/${encodeURIComponent(runId)}`), api(`/api/runs/${encodeURIComponent(runId)}/pc`).catch(() => null),
    api(`/api/runs/${encodeURIComponent(runId)}/analysis`).catch(() => null)]);
  if (detail.legacy) { page.append(notice("Legacy .tst runs have no harness trace; open a program run instead.", "warn")); return page; }
  const r = detail.run;
  picker.dSel.value = r.design_id; picker.cSel.value = r.case_id;
  page.append(h("p", {}, runLink(runId, `${r.case_id} on ${r.design_id} (repeat ${r.repeat_index})`), " ", badge(r.status), " ",
    r.status === "succeeded" ? badge(r.correctness) : null, ` · ${fmt.int(r.cycles_to_halt)} cycles to halt`));
  if (!pcs) { page.append(empty(`No trace stored: ${r.failure_reason || "the simulation produced no output"}.`)); return page; }

  const tableArea = h("div");
  const state = { filter: "all", value: "", offset: 0, postHalt: false };
  const load = async () => {
    const q = new URLSearchParams({ offset: state.offset, limit: PAGE, filter: state.filter, value: state.value, post_halt: state.postHalt ? "1" : "0" });
    const w = await api(`/api/runs/${encodeURIComponent(runId)}/trace?${q}`);
    clear(tableArea).append(traceTable(w, state, load));
  };
  const jump = (cycle) => { state.filter = "all"; state.offset = Math.max(0, cycle - 5); fSel.value = "all"; load(); tableArea.scrollIntoView({ behavior: "smooth" }); };
  const hist = (analysis?.pc_histogram || []).map((b) => ({ x: b.pc, value: b.cycles, tip: `PC ${b.pc} · ${b.disassembly}: ${b.cycles} cycles, entered ${b.entries} times` }));

  page.append(card(h("div", { class: "card-head" }, h("h2", {}, "Program counter over time"), h("span", { class: "small muted" }, "observed · click to jump to that cycle")),
    pcTimeline(pcs.points.map(([cycle, pc]) => ({ cycle, pc })), { halt: pcs.cycles_to_halt, onPick: jump })));
  page.append(card(h("div", { class: "card-head" }, h("h2", {}, "Execution frequency by instruction address"), h("span", { class: "small muted" }, "cycles spent at each PC before halt · click a bar to filter")),
    hist.length ? columns(hist, { xLabel: "PC (ROM address)", onPick: (b) => { state.filter = "pc"; state.value = String(b.x); state.offset = 0; fSel.value = "pc"; vIn.value = state.value; load(); } }) : empty("No analysis available.")));

  const fSel = h("select", {}, [["all", "All cycles"], ["writes", "Memory writes (observed writeM)"], ["reads", "Memory reads (inferred)"], ["jumps", "Non-sequential PC (jumps / folds)"], ["pc", "At PC = value"], ["address", "Accessing RAM address = value"], ["text", "Disassembly contains text"]]
    .map(([v, l]) => h("option", { value: v }, l)));
  const vIn = h("input", { type: "text", placeholder: "value", style: "width:110px;min-width:0" });
  const post = h("input", { type: "checkbox" });
  const controls = h("div", { class: "controls" }, h("label", { class: "field" }, "Filter", fSel), h("label", { class: "field" }, "Value", vIn),
    h("label", { class: "field", style: "flex-direction:row;align-items:center;gap:6px" }, post, "Include cycles after halt"),
    h("button", { class: "primary", onclick: () => { state.filter = fSel.value; state.value = vIn.value; state.postHalt = post.checked; state.offset = 0; load(); } }, "Apply"));
  const body = h("div");
  page.append(card(null, tabs([["cycles", "Cycle table"], ["raw", "Raw simulator output"]], (t) => {
    clear(body);
    if (t === "cycles") { body.append(controls, tableArea); load(); }
    else body.append(rawView(runId));
  }), body));
  return page;
}

function traceTable(w, state, load) {
  const hasRegs = w.rows.some((r) => r.A != null);
  const probes = w.rows[0] ? Object.keys(w.rows[0].probes) : [];
  const nav = h("div", { class: "controls", style: "margin:8px 0" },
    h("button", { disabled: state.offset === 0, onclick: () => { state.offset = Math.max(0, state.offset - PAGE); load(); } }, "← Previous"),
    h("button", { disabled: state.offset + PAGE >= w.matches, onclick: () => { state.offset += PAGE; load(); } }, "Next →"),
    h("span", { class: "small muted" }, `${w.matches ? state.offset + 1 : 0}–${Math.min(w.matches, state.offset + PAGE)} of ${fmt.int(w.matches)} matching cycles · ${fmt.int(w.total_rows_stored)} stored of ${fmt.int(w.total_rows_simulated)} simulated`));
  if (!w.rows.length) return h("div", {}, nav, empty("No cycles match this filter."));
  const cols = [
    { label: "Cycle", key: "cycle", num: true },
    { label: "PC", key: "pc", num: true, tip: "Observed: address of the instruction presented this cycle." },
    { label: "Instruction", value: (r) => h("span", { class: "mono small" }, r.instruction), tip: "Observed: ROM32K[pc]." },
    { label: "Disassembly", value: (r) => h("span", { class: "mono" }, r.disassembly), tip: "Inferred by decoding the instruction word." },
  ];
  if (hasRegs) cols.push({ label: "A", key: "A", num: true, tip: "Observed: ARegister[] probe." }, { label: "D", key: "D", num: true, tip: "Observed: DRegister[] probe." });
  cols.push(
    { label: "writeM", key: "writeM", num: true, tip: "Observed: 1 when this instruction writes RAM at the next clock edge." },
    { label: "addressM", key: "addressM", num: true }, { label: "outM", key: "outM", num: true }, { label: "inM", key: "inM", num: true },
    { label: "Events", value: (r) => h("span", {}, r.events.map((e) => h("span", { class: `ev ${e}` }, e)), r.halt ? h("span", { class: "ev" }, "halt") : null), tip: "write: observed writeM. read: decoded M operand. jump/fold: next PC is not PC+1." },
    { label: "RAM probe changes", value: (r) => r.probe_changes.map((a) => `RAM[${a}]=${r.probes[a]}`).join(", "), tip: `Observed RAM probes: ${probes.join(", ")}` },
  );
  return h("div", {}, nav, table(cols, w.rows, { maxHeight: "620px", rowClass: (r) => r.halt ? "trace-row-halt" : r.post_halt ? "trace-row-post" : "" }), nav);
}

function rawView(runId) {
  const box = h("div");
  let offset = 0;
  const load = async () => {
    const w = await api(`/api/runs/${encodeURIComponent(runId)}/raw?offset=${offset}&limit=200`);
    clear(box).append(
      h("div", { class: "controls" }, h("button", { disabled: offset === 0, onclick: () => { offset = Math.max(0, offset - 200); load(); } }, "← Previous"),
        h("button", { disabled: offset + 200 >= w.total_lines, onclick: () => { offset += 200; load(); } }, "Next →"),
        h("span", { class: "small muted" }, `lines ${offset + 1}–${Math.min(w.total_lines, offset + 200)} of ${w.total_lines}. ${w.note}`)),
      h("pre", { class: "code" }, w.lines.join("\n")));
  };
  load().catch((e) => box.append(notice(e.message, "bad")));
  return box;
}
