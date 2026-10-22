import { api, post } from "../api.js";
import { badge, card, clear, empty, fmt, h, notice, table, term } from "../dom.js";
import { hbar } from "../charts.js";
import { benchmarksList, caseOptions, designOptions, designsList } from "./common.js";

export async function render() {
  const [p, designs, benches] = await Promise.all([api("/api/predictor"), designsList(), benchmarksList()]);
  const page = h("div", {}, h("div", { class: "page-head" }, h("h1", {}, "Performance predictor"),
    h("p", { class: "lede" }, "Predicts a design's measured cycles to halt without running the HDL simulator, and reports how well each predictor does on programs it has not seen. ",
      term("prediction_error", "Errors"), " are in simulated cycles.")));
  if (!p.trained) { page.append(empty("No trained predictor. Run: python3 cycle_predictor.py --train")); return page; }
  const rep = p.report, ev = rep.evaluation;
  page.append(notice(`Trained ${fmt.time(p.created_at)} on ${fmt.int(rep.dataset.rows)} (design, case) rows: ${rep.dataset.designs.length} designs × ${rep.dataset.cases} cases from ${rep.dataset.programs} distinct programs. ${ev.protocol}`));

  const macro = Object.entries(ev.macro_average).map(([k, v]) => ({ k, ...v })).sort((a, b) => a.mae - b.mae);
  page.append(h("div", { class: "grid g2" },
    card("Average error across designs (lower is better)",
      hbar(macro.map((m) => ({ label: m.k, value: m.mae, valueLabel: `${fmt.num(m.mae, 1)} cycles`, color: m.k === "median_baseline" ? "var(--neutral)" : "var(--s1)" })), { width: 560, valueFmt: (v) => fmt.int(v) }),
      table([{ label: "Predictor", key: "k" }, { label: "MAE", value: (m) => fmt.num(m.mae, 2), num: true }, { label: "RMSE", value: (m) => fmt.num(m.rmse, 2), num: true },
        { label: "MAPE", value: (m) => fmt.pctAbs(m.mape_pct, 2), num: true }, { label: "p90 |err|", value: (m) => fmt.num(m.p90_ae, 1), num: true }], macro)),
    card("Predictors", h("dl", { class: "kv" }, Object.entries(rep.predictors).flatMap(([k, v]) => [h("dt", { class: "mono" }, k), h("dd", {}, v)])))));

  const designsEv = Object.entries(ev.per_design);
  const names = Object.keys(rep.predictors);
  page.append(card("Per-design leave-programs-out error (MAE, cycles)",
    table([{ label: "Design", key: "d" }, { label: "Rows", key: "rows", num: true }, { label: "Programs", key: "programs", num: true },
      ...names.map((n) => ({ label: n, num: true, value: (r) => h("span", { style: r.best === n ? "font-weight:700" : r.models[n].mae >= r.models.median_baseline.mae ? "color:var(--bad)" : "" }, fmt.num(r.models[n].mae, 1)) }))],
    designsEv.map(([d, v]) => ({ d, best: v.best_model_by_mae, ...v })), { maxHeight: "420px" }),
    h("p", { class: "small muted" }, "Bold = best predictor for that design; red = no better than the median baseline. Designs with identical timing: ",
      (rep.dataset.designs_with_identical_timing || []).map((g) => `{${g.join(", ")}}`).join(" "), " — their rows are not independent evidence.")));

  const lodo = Object.entries(ev.leave_one_design_out_calibrated_isa || {});
  page.append(card("Does calibration transfer to an unseen design?",
    h("p", {}, "calibrated_isa fitted on all other designs, then applied to the held-out design. Where the held-out design has a different CPI, the error is large: the predictor cannot be used for an architecture it has not measured."),
    table([{ label: "Held-out design", key: "d" }, { label: "MAE", value: (r) => fmt.num(r.mae, 1), num: true }, { label: "MAPE", value: (r) => fmt.pctAbs(r.mape_pct, 1), num: true }],
      lodo.map(([d, v]) => ({ d, ...v })), { maxHeight: "320px" })));

  const dSel = h("select", {}, designOptions(designs, "jumpfold-8"));
  const cSel = h("select", {}, caseOptions(benches, "bubblesort-8-rev"));
  const out = h("div");
  const run = async () => {
    clear(out).append(h("p", { class: "muted" }, "Predicting…"));
    try {
      const r = await post("/api/predictor/predict", { design_id: dSel.value, case_id: cSel.value });
      clear(out).append(predictionView(r));
    } catch (e) { clear(out).append(notice(e.message, "bad")); }
  };
  page.append(card("Try a prediction", h("div", { class: "controls" }, h("label", { class: "field" }, "Design", dSel), h("label", { class: "field" }, "Case", cSel),
    h("button", { class: "primary", onclick: run }, "Predict")), out));
  page.append(card("Limitations", h("ul", {}, rep.limitations.map((l) => h("li", {}, l)))));
  page.append(card("Features", table([{ label: "Feature", value: (r) => h("span", { class: "mono" }, r.k) }, { label: "Kind", key: "kind" }, { label: "Definition", key: "v" }],
    [...rep.features.static.map((k) => ({ k, kind: "static" })), ...rep.features.dynamic.map((k) => ({ k, kind: "ISA reference" }))].map((r) => ({ ...r, v: rep.features.definitions[r.k] })), { maxHeight: "360px" })));
  if (p.legacy_model_report) page.append(card("Earlier model (kept for reference)", h("p", { class: "small" }, `${p.legacy_model_report.algorithm}: grouped CV MAE ${p.legacy_model_report.group_cross_validation.mae_cycles} vs median baseline ${p.legacy_model_report.group_cross_validation.median_baseline_mae_cycles} on ${p.legacy_model_report.sample_count} labels; R² ${p.legacy_model_report.group_cross_validation.r2}. Static features only, design 1 only.`)));
  return page;
}

function predictionView(r) {
  const rows = Object.entries(r.predictions).map(([k, v]) => ({ k, ...v }));
  return h("div", {},
    r.measured ? notice(`Measured by simulation: ${fmt.int(r.measured.cycles)} cycles (${r.measured.correct ? "correct" : "incorrect"} run).`, "good") : notice("This case has not been simulated on this design, so there is no measurement to compare with.", "warn"),
    table([{ label: "Predictor", key: "k" }, { label: "Predicted cycles", value: (x) => fmt.num(x.cycles, 1), num: true },
      { label: "Band (p90 error)", value: (x) => x.band ? `${fmt.int(x.band[0])} – ${fmt.int(x.band[1])}` : "—", num: true },
      { label: "Error vs measured", value: (x) => x.error_vs_measured == null ? "—" : fmt.num(x.error_vs_measured, 1), num: true }], rows),
    h("p", { class: "small muted" }, r.note));
}
