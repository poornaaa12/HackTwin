import { api } from "../api.js";
import { badge, card, empty, fmt, h, kv, notice, runLink, table, term } from "../dom.js";
import { hbar } from "../charts.js";
import { runPicker, workflow } from "./common.js";

const KIND_TEXT = {
  observation: "Observation — a fact measured from the trace.",
  heuristic: "Heuristic — a rule suggests something worth testing; not a confirmed cause.",
  confirmed_problem: "Confirmed problem — a correctness, completion or determinism failure.",
  quality_warning: "Trace-quality warning — limits what can be concluded from this run.",
};

export async function render(route) {
  const runId = route.parts[0];
  const page = h("div", {}, h("div", { class: "page-head" }, h("h1", {}, "Bottleneck analysis"),
    h("p", { class: "lede" }, "Where the cycles of one run go, computed from its trace by bottleneck_analysis.py. Only cycles before the program reached its halt address are counted. Frequent register updates or memory writes are normal in Hack and are not labelled defects."),
    workflow("bottlenecks")));
  const picker = await runPicker({ onPick: (run) => run && (location.hash = `#/bottlenecks/${encodeURIComponent(run.run_id)}`), target: "Analyse" });
  page.append(card(null, picker.bar));
  if (!runId) { page.append(empty("Choose a design and benchmark case to analyse.")); return page; }
  const a = await api(`/api/runs/${encodeURIComponent(runId)}/analysis`);
  if (a.legacy) { page.append(notice("Legacy official-test runs only support the original Phase 3 report.", "warn")); return page; }
  picker.dSel.value = a.design_id; picker.cSel.value = a.case_id;
  const s = a.summary;
  page.append(h("p", {}, runLink(runId, `${a.case_id} on ${a.design_id}`), " ", badge(a.status), " ", a.status === "succeeded" ? badge(a.correctness) : null,
    " · ", h("a", { href: `#/trace/${encodeURIComponent(runId)}` }, "open trace"), " · ", h("a", { href: `#/recommendations/${encodeURIComponent(a.design_id)}?run=${encodeURIComponent(runId)}` }, "recommendations")));
  if (a.no_trace) { page.append(findingsList(a.findings)); return page; }

  page.append(h("div", { class: "grid g4" },
    card(null, h("div", { class: "stat" }, h("span", { class: "label" }, "Cycles to halt"), h("span", { class: "value" }, fmt.int(s.cycles_to_halt)), h("span", { class: "sub" }, "measured"))),
    card(null, h("div", { class: "stat" }, h("span", { class: "label" }, term("cpi", "CPI")), h("span", { class: "value" }, fmt.num(s.cpi, 3)), h("span", { class: "sub" }, `${fmt.int(s.reference_instructions)} ISA instructions`))),
    card(null, h("div", { class: "stat" }, h("span", { class: "label" }, "Foldable jumps"), h("span", { class: "value" }, fmt.int(s.foldable_jumps)), h("span", { class: "sub" }, `${fmt.pctAbs(s.foldable_jump_share_pct)} of executed instructions (inferred)`))),
    card(null, h("div", { class: "stat" }, h("span", { class: "label" }, "Jumps folded by hardware"), h("span", { class: "value" }, fmt.int(s.folded_jumps_observed)), h("span", { class: "sub" }, "observed non-sequential PC after an A-instruction")))));

  page.append(h("h2", { style: "margin-top:18px" }, "Findings and evidence"), findingsList(a.findings));

  const mix = a.instruction_mix.filter((m) => m.cycles);
  page.append(h("div", { class: "grid g2", style: "margin-top:14px" },
    card("Where cycles go: instruction classes",
      h("p", { class: "small muted" }, "Inferred by decoding each observed instruction word; share of cycles before halt."),
      hbar(mix.map((m) => ({ label: m.label.split(" (")[0], value: m.share_pct, valueLabel: `${fmt.pctAbs(m.share_pct)} (${fmt.int(m.cycles)})`, tip: `${m.label}: ${m.cycles} cycles` })), { valueFmt: (v) => `${Math.round(v)}%`, width: 560 })),
    card("Hottest program addresses",
      table([
        { label: "PC", key: "pc", num: true }, { label: "Instruction", value: (p) => h("span", { class: "mono" }, p.disassembly) },
        { label: "Cycles", value: (p) => fmt.int(p.cycles), num: true }, { label: "Share", value: (p) => fmt.pctAbs(p.share_pct), num: true },
        { label: "Entries", value: (p) => fmt.int(p.entries), num: true, tip: "How many times execution arrived at this PC." },
      ], a.pc_hotspots, { onRow: (p) => (location.hash = `#/trace/${encodeURIComponent(runId)}`) }))));

  page.append(h("div", { class: "grid g2", style: "margin-top:14px" },
    card("Loops (backward jumps)",
      h("p", { class: "small muted" }, "From observed PC transfers to a lower or equal address. Body extent is inferred from the jump target and source."),
      a.loops.length ? table([
        { label: "Body (PCs)", key: "body_pcs" }, { label: "Back-edge taken", value: (l) => fmt.int(l.taken), num: true },
        { label: "Static size", value: (l) => `${l.static_body_instructions} instr`, num: true },
        { label: "Cycles in body", value: (l) => fmt.pctAbs(l.cycles_in_body_pct), num: true, tip: "Nested loops overlap, so shares can add up to more than 100%." },
      ], a.loops) : empty("No backward jumps: straight-line program.")),
    card("Registers and memory",
      a.registers ? kv([
        ["A changes", `${fmt.int(a.registers.a_changes)} (${fmt.pctAbs(a.registers.a_change_rate_pct)} of cycles, ${a.registers.a_distinct_values} distinct values)`],
        ["D changes", `${fmt.int(a.registers.d_changes)} (${fmt.pctAbs(a.registers.d_change_rate_pct)} of cycles, ${a.registers.d_distinct_values} distinct values)`],
      ]) : notice("A/D not observable for this CPU (no ARegister/DRegister built-ins).", "warn"),
      h("p", { class: "small muted" }, a.registers?.provenance || ""),
      kv([["Memory writes", `${fmt.int(a.memory.writes)} (${fmt.num(a.memory.write_rate_per_cycle, 3)} per cycle, ${a.memory.distinct_addresses_written} addresses)`],
          ["Memory reads", `${fmt.int(a.memory.reads)} (${fmt.num(a.memory.read_rate_per_cycle, 3)} per cycle, ${a.memory.distinct_addresses_read} addresses)`]]),
      table([
        { label: "Address", value: (m) => `RAM[${m.address}]` }, { label: "Region", key: "region" },
        { label: "Writes", key: "writes", num: true }, { label: "Reads", key: "reads", num: true },
        { label: "Share", value: (m) => fmt.pctAbs(m.share_of_accesses_pct), num: true },
      ], a.memory.hot_addresses, { maxHeight: "240px" }),
      h("p", { class: "small muted" }, a.memory.provenance))));
  page.append(notice(a.scope_note));
  return page;
}

export function findingsList(findings) {
  if (!findings.length) return empty("No findings.");
  const order = { confirmed_problem: 0, quality_warning: 1, heuristic: 2, observation: 3 };
  return h("div", {}, findings.slice().sort((x, y) => order[x.kind] - order[y.kind]).map((f) => h("article", { class: "finding" },
    h("div", { class: "meta" }, badge(f.kind), h("span", { class: "small muted", "data-tip": KIND_TEXT[f.kind] }, f.id)),
    h("h4", {}, f.title),
    h("dl", { class: "kv" },
      h("dt", {}, "Evidence"), h("dd", { class: "mono small" }, JSON.stringify(f.evidence)),
      h("dt", {}, "Why it matters"), h("dd", {}, f.why_it_matters),
      h("dt", {}, "Limits"), h("dd", {}, f.limits),
      h("dt", {}, "Next step"), h("dd", {}, f.next_step)))));
}
