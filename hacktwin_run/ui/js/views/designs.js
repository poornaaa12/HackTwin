import { api } from "../api.js";
import { badge, card, clear, designLink, empty, fmt, h, kv, notice, runLink, table, tabs, term } from "../dom.js";
import { designOptions, designsList, workflow } from "./common.js";

const KIND = { baseline: "Baseline", internal_variant: "HackTwin variant", mutation: "Mutation output", external: "GitHub import", legacy: "Legacy", unmanifested: "No manifest" };

export async function render(route) {
  if (route.parts[0]) return renderDetail(route.parts[0]);
  const designs = await designsList();
  const filter = route.query.kind || "all";
  const shown = designs.filter((d) => filter === "all" || (filter === "internal" ? d.kind !== "external" : d.kind === "external"));
  const chips = h("div", { class: "chips" }, [["all", "All"], ["internal", "HackTwin designs"], ["external", "GitHub imports"]].map(([k, l]) =>
    h("a", { class: `chip ${filter === k ? "on" : ""}`, href: `#/designs?kind=${k}` }, `${l} (${designs.filter((d) => k === "all" || (k === "internal" ? d.kind !== "external" : d.kind === "external")).length})`)));
  return h("div", {},
    h("div", { class: "page-head" }, h("h1", {}, "CPU design explorer"),
      h("p", { class: "lede" }, "Every design known to the twin store. A different ", term("source_hash"), " only means the files differ; the ", term("netlist_hash"),
        " shows whether the hardware wiring differs; whether behaviour or cycle counts differ is decided only by simulation."), workflow("designs")),
    h("div", { class: "controls" }, chips),
    table([
      { label: "Design", value: (d) => h("div", {}, designLink(d.design_id), h("div", { class: "small muted" }, d.display_name)) },
      { label: "Kind", value: (d) => KIND[d.kind] || d.kind },
      { label: "Parent", value: (d) => d.parent_design_id ? designLink(d.parent_design_id) : "—" },
      { label: "Execution model", value: (d) => d.execution_model || "—" },
      { label: "Netlist", value: (d) => h("span", {}, h("span", { class: "hash" }, fmt.hash(d.netlist_hash, 8)), d.same_netlist_as.length ? h("div", { class: "small muted" }, `same as ${d.same_netlist_as.slice(0, 3).join(", ")}${d.same_netlist_as.length > 3 ? "…" : ""}`) : null), tip: "Designs sharing a netlist hash have no structural hardware difference." },
      { label: "Official tests", value: (d) => d.conformance.total ? badge(d.conformance.passed + d.conformance.not_applicable === d.conformance.total ? "pass" : "fail", `${d.conformance.passed}/${d.conformance.total - d.conformance.not_applicable} passed`) : badge("unknown", "not run") },
      { label: "Core coverage", value: (d) => `${d.coverage.correct_cases}/${d.coverage.total_cases}`, num: true, tip: "Core benchmark cases with valid, correct runs of the current revision." },
      { label: "Speedup", value: (d) => d.ranking ? fmt.x(d.ranking.geomean_speedup) : h("span", { class: "muted" }, "not ranked"), num: true, tip: "Geometric-mean speedup vs baseline (design 1); only for designs correct on every core case." },
      { label: "Runs", value: (d) => fmt.int(d.runs), num: true },
    ], shown, { onRow: (d) => (location.hash = `#/designs/${encodeURIComponent(d.design_id)}`), maxHeight: "70vh" }));
}

async function renderDetail(id) {
  const data = await api(`/api/designs/${encodeURIComponent(id)}`);
  const d = data.design;
  const prov = d.provenance || {};
  const body = h("div");
  const head = h("div", { class: "page-head" },
    h("div", { class: "small" }, h("a", { href: "#/designs" }, "← All designs")),
    h("h1", {}, `${d.design_id}: ${d.display_name || ""}`),
    h("p", { class: "lede" }, d.summary || ""),
    h("div", { class: "chips" },
      badge("info", KIND[d.kind] || d.kind),
      data.inspection.structurally_valid ? badge("pass", "structurally valid") : badge("fail", "structural problems"),
      badge(data.core_correct_cases === data.core_total_cases ? "pass" : "invalid", `${data.core_correct_cases}/${data.core_total_cases} core cases correct`),
      h("a", { class: "chip", href: `#/workbench?design=${encodeURIComponent(id)}` }, "Run in workbench"),
      h("a", { class: "chip", href: `#/compare?candidates=${encodeURIComponent(id)}` }, "Compare with baseline"),
      id !== "1" ? h("a", { class: "chip", href: `#/diff/1/${encodeURIComponent(id)}` }, "HDL diff vs baseline") : null));
  const panes = {
    summary: () => summaryPane(data),
    files: () => filesPane(data),
    tests: () => testsPane(data),
    coverage: () => coveragePane(data),
    history: () => historyPane(data),
  };
  return h("div", {}, head,
    tabs([["summary", "Summary"], ["files", "HDL files & structure"], ["tests", "Official tests"], ["coverage", "Benchmark coverage"], ["history", `Simulation history (${data.history_total})`]],
      (t) => clear(body).append(panes[t]())), body);
}

function summaryPane(data) {
  const d = data.design, prov = d.provenance || {}, ch = d.characteristics || {};
  const idn = data.identity_vs_baseline;
  return h("div", { class: "grid g2" },
    card("Characteristics", kv([
      ["Execution model", ch.execution_model], ["ISA", ch.isa],
      ["Cycle-compatible with spec", ch.cycle_compatible_with_spec == null ? "—" : String(ch.cycle_compatible_with_spec)],
      ["Changed units", (ch.changed_units || []).join(", ") || "none"],
      ["Expected cycle effect", ch.expected_cycle_effect || "—"],
      ["CPU built-in parts (flattened)", fmt.int(data.inspection.cpu_flattened_builtin_total)],
    ]), h("h3", {}, "Changes relative to parent"),
      data.manifest_changes.length ? h("ul", {}, data.manifest_changes.map((c) => h("li", {}, c))) : h("p", { class: "muted" }, "None recorded."),
      h("h3", {}, "Limitations"), h("ul", {}, (d.limitations || []).map((l) => h("li", {}, l)))),
    h("div", {},
      card("Source-level vs architectural difference (vs baseline)",
        h("p", {}, badge(idn.level === "netlist_differs" ? "info" : "neutral", (idn.level || "").replaceAll("_", " ")), " ", idn.summary),
        idn.per_chip ? table([
          { label: "Chip", value: (r) => r.chip }, { label: "Status", value: (r) => r.status.replaceAll("_", " ") },
          { label: "Parts (base → this)", value: (r) => `${r.part_count_a ?? "—"} → ${r.part_count_b ?? "—"}` },
          { label: "Part-type changes", value: (r) => Object.entries(r.part_types_delta || {}).map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}`).join(", ") || "—" },
        ], Object.entries(idn.per_chip).map(([chip, v]) => ({ chip, ...v }))) : null,
        h("p", { class: "small muted" }, "Part counts are structural facts from the HDL text, not area or delay estimates.")),
      card("Identity and lineage", kv([
        ["Source hash", h("span", { class: "hash" }, d.hdl_source_hash)],
        ["Normalized hash", h("span", { class: "hash" }, d.normalized_hash || "—")],
        ["Netlist hash", h("span", { class: "hash" }, d.netlist_hash || "—")],
        ["Same netlist as", data.same_netlist_as.length ? data.same_netlist_as.map((x, i) => [i ? ", " : "", designLink(x)]) : "no other design"],
        ["Lineage", data.lineage.map((x, i) => [i ? " ← " : "", designLink(x)])],
        ["Derived designs", data.children.length ? data.children.map((x, i) => [i ? ", " : "", designLink(x)]) : "none"],
      ])),
      card("Provenance", kv(Object.entries(prov).filter(([k]) => k !== "files").map(([k, v]) => [k.replaceAll("_", " "), k === "url" ? h("a", { href: v, target: "_blank", rel: "noopener" }, v) : typeof v === "object" && v ? JSON.stringify(v) : (k === "retrieved_at" ? fmt.time(v) : String(v ?? "—"))])),
        prov.files ? table([{ label: "File", key: "f" }, { label: "Origin", key: "o" }, { label: "Path", value: (r) => h("span", { class: "mono" }, r.p) }],
          Object.entries(prov.files).map(([f, v]) => ({ f, o: v.origin, p: v.path }))) : null)));
}

function filesPane(data) {
  const files = Object.entries(data.inspection.files || {});
  const src = h("div");
  const id = data.design.design_id;
  const show = async (name) => {
    clear(src).append(h("p", { class: "muted" }, "Loading…"));
    const f = await api(`/api/designs/${encodeURIComponent(id)}/files/${encodeURIComponent(name)}`);
    clear(src).append(h("pre", { class: "code" }, f.text));
  };
  const rows = files.map(([name, f]) => ({ name, ...f }));
  queueMicrotask(() => rows[0] && show(rows.find((r) => r.name === "CPU.hdl")?.name || rows[0].name));
  return h("div", {},
    data.inspection.problems?.length ? notice(`Problems: ${data.inspection.problems.join("; ")}`, "bad") : null,
    data.inspection.warnings?.length ? notice(data.inspection.warnings.join("; "), "warn") : null,
    h("div", { class: "split" },
      table([
        { label: "File", value: (r) => h("a", { href: "javascript:void 0", onclick: () => show(r.name) }, r.name) },
        { label: "Parts", key: "part_count", num: true },
        { label: "SHA-256", value: (r) => h("span", { class: "hash" }, fmt.hash(r.sha256, 8)) },
      ], rows),
      src),
    card("CPU flattened to simulator built-ins",
      h("p", { class: "small muted" }, "Design-local chips expanded recursively; counts are built-in chip instances (not gates or area)."),
      table([{ label: "Built-in chip", key: "k" }, { label: "Instances", key: "v", num: true }],
        Object.entries(data.inspection.cpu_flattened_builtin_parts || {}).map(([k, v]) => ({ k, v })), { maxHeight: "300px" })));
}

function testsPane(data) {
  const rows = data.conformance;
  if (!rows.length) return empty("No official tests recorded for the current revision. Run an experiment with conformance tests.");
  return h("div", {},
    h("p", { class: "lede" }, "Official nand2tetris test scripts (", term("conformance"), ") run against this design's own chips. CPU.tst and Computer*.tst are cycle-exact: a design that changes timing on purpose fails them even if it computes correct results."),
    table([
      { label: "Test", key: "test_name" }, { label: "Outcome", value: (r) => badge(r.outcome) },
      { label: "Simulator message", value: (r) => h("span", { class: "small" }, r.message || "") },
      { label: "Elapsed (host s)", value: (r) => fmt.num(r.elapsed_s, 2), num: true },
      { label: "When", value: (r) => fmt.time(r.created_at) },
    ], rows));
}

function coveragePane(data) {
  return h("div", {},
    h("p", { class: "lede" }, "Benchmark cases with valid, correct runs of this design's current revision. Failed or missing cases are listed, never treated as zero."),
    table([
      { label: "Benchmark", value: (r) => h("span", {}, r.title, r.suite !== "hacktwin-core-v1" ? h("span", { class: "muted small" }, " (GitHub workload)") : null) },
      { label: "Cases", key: "cases", num: true },
      { label: "Correct", key: "correct", num: true },
      { label: "Failed / wrong", value: (r) => r.failed ? h("span", { style: "color:var(--bad)" }, r.failed) : "0", num: true },
      { label: "Missing", key: "missing", num: true },
      { label: "Mean CPI", value: (r) => fmt.num(r.mean_cpi, 3), num: true, tip: "Cycles per instruction: measured cycles / ISA-reference instructions." },
    ], data.coverage, { maxHeight: "65vh" }));
}

function historyPane(data) {
  return h("div", {},
    data.historical_runs ? notice(`${data.historical_runs} of these runs belong to an older HDL revision and are excluded from comparisons.`, "warn") : null,
    table([
      { label: "Run", value: (r) => runLink(r.run_id, `${r.case_id} · r${r.repeat_index}`) },
      { label: "Status", value: (r) => badge(r.status) },
      { label: "Result", value: (r) => r.status === "succeeded" ? badge(r.correctness) : "—" },
      { label: "Cycles", value: (r) => fmt.int(r.cycles_to_halt), num: true },
      { label: "Revision", value: (r) => r.is_current_revision ? "current" : h("span", { class: "muted" }, `old ${fmt.hash(r.run_hdl_hash, 8)}`) },
      { label: "When", value: (r) => fmt.time(r.started_at) },
    ], data.history, { maxHeight: "65vh" }),
    data.history_total > data.history.length ? h("p", { class: "small muted" }, `Showing the ${data.history.length} most recent of ${data.history_total} runs.`) : null);
}

export async function renderDiff(route) {
  const [a = "1", b = "2"] = route.parts;
  const designs = await designsList();
  const sa = h("select", {}, designOptions(designs, a)), sb = h("select", {}, designOptions(designs, b));
  const diff = await api(`/api/designs/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  return h("div", {},
    h("div", { class: "page-head" }, h("div", { class: "small" }, h("a", { href: "#/designs" }, "← All designs")), h("h1", {}, "HDL difference"),
      h("p", { class: "lede" }, "Text differences, then the structural classification. Comment or formatting edits change the source hash but not the hardware.")),
    h("div", { class: "controls" }, h("label", { class: "field" }, "Design A", sa), h("label", { class: "field" }, "Design B", sb),
      h("button", { class: "primary", onclick: () => (location.hash = `#/diff/${encodeURIComponent(sa.value)}/${encodeURIComponent(sb.value)}`) }, "Compare HDL")),
    notice(`${(diff.identity.level || "").replaceAll("_", " ")}: ${diff.identity.summary}`, diff.identity.level === "netlist_differs" ? "" : "warn"),
    ...diff.files.map((f) => card(h("div", { class: "card-head" }, h("h2", {}, f.file), badge(f.status === "identical" ? "neutral" : "info", f.status)),
      f.diff ? diffBlock(f.diff) : h("p", { class: "muted small" }, "Identical bytes."))));
}

function diffBlock(text) {
  const pre = h("pre", { class: "code" });
  for (const line of text.split("\n")) {
    const cls = line.startsWith("@@") ? "hunk" : line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : "";
    pre.append(h("span", { class: cls }, line + "\n"));
  }
  return pre;
}
