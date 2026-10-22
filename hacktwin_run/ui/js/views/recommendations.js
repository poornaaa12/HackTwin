import { api } from "../api.js";
import { badge, card, designLink, empty, fmt, h, kv, notice, runLink } from "../dom.js";
import { designOptions, designsList, workflow } from "./common.js";

const STATUS_TEXT = {
  confirmed_problem: "A failure observed in this design's runs. Fix it before measuring performance.",
  experimentally_verified: "The suggested design was simulated on the same case, stayed correct, and needed fewer cycles.",
  experimentally_refuted: "The suggested design was simulated on the same case and did not need fewer cycles.",
  heuristic: "A rule fired on the trace; the suggested change has not been measured yet.",
  software_suggestion: "A program-level change; HackTwin cannot test it automatically.",
};

export async function render(route) {
  const designs = await designsList();
  const id = route.parts[0] || "1";
  const sel = h("select", { onchange: (e) => (location.hash = `#/recommendations/${encodeURIComponent(e.target.value)}`) }, designOptions(designs, id));
  const page = h("div", {}, h("div", { class: "page-head" }, h("h1", {}, "Recommendations"),
    h("p", { class: "lede" }, "Suggestions derived from bottleneck evidence across this design's core benchmark cases. Each one names the signal, the rule, the proposed experiment, a bound on the possible benefit, trade-offs, and how to verify it. A suggestion is only marked verified when the suggested design has actually been simulated on the same cases."),
    workflow("recommendations")),
    h("div", { class: "controls" }, h("label", { class: "field" }, "Design", sel)));
  if (route.query.run) {
    const one = await api(`/api/runs/${encodeURIComponent(route.query.run)}/recommendations`);
    page.append(card(h("div", { class: "card-head" }, h("h2", {}, "For the selected run"), runLink(route.query.run)), ...(one.recommendations || []).map((r) => recCard(r, null))));
  }
  const data = await api(`/api/designs/${encodeURIComponent(id)}/recommendations`);
  if (!data.groups.length) { page.append(empty("No analysed runs for this design.")); return page; }
  page.append(h("p", { class: "small muted" }, `Aggregated over ${data.cases_analysed} core cases of `, designLink(id), "."));
  for (const g of data.groups) page.append(recCard(g.example, g));
  return page;
}

function recCard(r, group) {
  const statuses = group ? Object.entries(group.statuses) : [[r.evidence_status, 1]];
  const m = group?.measured_summary;
  return h("article", { class: "finding" },
    h("div", { class: "meta" }, statuses.map(([s, n]) => h("span", { "data-tip": STATUS_TEXT[s] || "" }, badge(s, `${s.replaceAll("_", " ")}${group ? ` · ${n} case${n > 1 ? "s" : ""}` : ""}`))),
      h("span", { class: "small muted" }, r.rule_id)),
    h("h4", {}, r.title),
    kv([
      ["Observed signal", h("span", { class: "mono small" }, JSON.stringify(r.observed_signal))],
      ["Rule", r.rule], ["Why it may matter", r.why_it_may_matter], ["Next experiment", r.next_experiment],
      ["Expected benefit", r.expected_benefit], ["Trade-offs", r.tradeoffs], ["How to verify", r.how_to_verify],
      group ? ["Cases", group.cases.join(", ")] : undefined,
    ]),
    m ? notice(`Measured on ${m.cases_measured} case(s) against ${m.other_design}: cycle reduction ${fmt.pct(m.min_cycle_reduction_pct, 2)} to ${fmt.pct(m.max_cycle_reduction_pct, 2)} (mean ${fmt.pct(m.mean_cycle_reduction_pct, 2)}); ${m.cases_improved} case(s) improved. Simulated cycles only.`, m.cases_improved ? "good" : "warn")
      : r.measurement && r.measurement.available === false ? h("p", { class: "small muted" }, `Not verified: ${r.measurement.reason}.`) : null);
}
