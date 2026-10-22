import { api } from "../api.js";
import { badge, card, clear, designLink, empty, fmt, h, notice, table, tabs } from "../dom.js";
import { hbar } from "../charts.js";

export async function render() {
  const [d, ex] = await Promise.all([api("/api/dataset"), api("/api/experiments")]);
  const f = d.funnel;
  const page = h("div", {}, h("div", { class: "page-head" }, h("h1", {}, "Datasets & experiments"),
    h("p", { class: "lede" }, "Where every design and measurement came from. External HDL is fetched from public GitHub repositories by exact commit, treated as untrusted data (only .hdl files, no scripts executed), and admitted stage by stage. Stars and recency are discovery signals only.")));
  const stages = [["Repositories discovered", f.repositories.discovered], ["Repositories retrieved", f.repositories.retrieved], ["…with a CPU.hdl", f.repositories.with_cpu_hdl],
    ["Candidate designs", f.candidates.retrieved], ["Structurally valid", f.candidates.structurally_valid], ["Unique (not duplicates)", f.candidates.unique_structurally_valid],
    ["Simulator-compatible", f.candidates.simulator_compatible], ["Correct on all tests", f.candidates.correct], ["Complete & comparable", f.candidates.complete]];
  page.append(h("div", { class: "grid g2" },
    card("GitHub HDL ingestion funnel", hbar(stages.map(([l, v]) => ({ label: l, value: v || 0 })), { width: 560 }),
      d.pending_repositories ? h("p", { class: "small muted" }, `${fmt.int(d.pending_repositories)} discovered repositories have not been retrieved yet (ingestion is capped per run and resumable).`) : null),
    card("Why candidates stopped", Object.keys(f.rejections).length ? table([{ label: "Reason", key: "r" }, { label: "Count", key: "n", num: true }],
      Object.entries(f.rejections).slice(0, 14).map(([r, n]) => ({ r, n })), { maxHeight: "300px" }) : empty("No rejections."),
      h("h3", {}, "Licenses (repositories with a CPU.hdl)"),
      table([{ label: "License", key: "l" }, { label: "Repos", key: "n", num: true }], Object.entries(f.licenses).map(([l, n]) => ({ l, n }))),
      h("p", { class: "small muted" }, "Without a license, files are cached locally for analysis only and are not redistributed."))));
  const body = h("div");
  page.append(card(null, tabs([["candidates", "Candidate designs"], ["repos", "Repositories"], ["experiments", "Experiments"], ["jobs", "Jobs"]], (t) => {
    clear(body);
    if (t === "candidates") body.append(candidates(d.candidates));
    if (t === "repos") body.append(repos(d.repositories));
    if (t === "experiments") body.append(experiments(ex.experiments));
    if (t === "jobs") body.append(jobs(ex.jobs));
  }), body));
  return page;
}

function candidates(rows) {
  const sel = h("select", {}, h("option", { value: "" }, "all statuses"), [...new Set(rows.map((r) => r.status))].map((s) => h("option", { value: s }, s)));
  const box = h("div");
  const draw = () => clear(box).append(table([
    { label: "Repository @ commit", value: (r) => h("span", {}, h("a", { href: `https://github.com/${r.full_name}/tree/${r.commit_sha}/${r.base_path}`, target: "_blank", rel: "noopener" }, r.full_name), h("span", { class: "hash" }, ` @${r.commit_sha.slice(0, 7)}`), h("div", { class: "small mono muted" }, r.base_path)) },
    { label: "Stage", value: (r) => r.stage.replaceAll("_", " ") },
    { label: "Status", value: (r) => badge(r.status) },
    { label: "Design", value: (r) => r.design_id ? designLink(r.design_id, "open") : r.duplicate_of ? h("span", { class: "small" }, "dup of ", designLink(r.duplicate_of)) : "—" },
    { label: "Composition", value: (r) => h("span", { class: "small" }, Object.entries(r.compatibility?.composition || {}).filter(([, o]) => !o.startsWith("repository")).map(([n]) => `${n} from baseline`).join(", ") || "all from repository") },
    { label: "Details", value: (r) => h("span", { class: "small" }, (r.errors || []).join("; ") || (r.compatibility?.correct_cases != null ? `${r.compatibility.correct_cases}/${r.compatibility.total_cases} core cases correct` : "")) },
  ], rows.filter((r) => !sel.value || r.status === sel.value), { maxHeight: "560px" }));
  sel.addEventListener("change", draw); draw();
  return h("div", {}, h("div", { class: "controls" }, h("label", { class: "field" }, "Status", sel)), box);
}
function repos(rows) {
  return table([
    { label: "Repository", value: (r) => h("a", { href: r.url, target: "_blank", rel: "noopener" }, r.full_name) },
    { label: "Stars", value: (r) => fmt.int(r.stars), num: true },
    { label: "Commit", value: (r) => h("span", { class: "hash" }, fmt.hash(r.commit_sha, 7)) },
    { label: "License", value: (r) => h("span", { "data-tip": `${r.license_evidence}. ${r.redistribution}` }, r.license_spdx || "none") },
    { label: "Status", value: (r) => badge(r.status) }, { label: "Candidates", key: "candidates", num: true },
    { label: "Found via", value: (r) => h("span", { class: "small muted" }, r.discovered_via) },
    { label: "Retrieved", value: (r) => fmt.time(r.retrieved_at) },
  ], rows, { maxHeight: "560px" });
}
function experiments(rows) {
  return rows.length ? table([
    { label: "Experiment", value: (e) => h("div", {}, e.title, h("div", { class: "small mono muted" }, e.experiment_id)) },
    { label: "Started", value: (e) => fmt.time(e.created_at) }, { label: "Duration", value: (e) => e.finished_at ? `${fmt.num(e.finished_at - e.created_at, 1)} s` : "—", num: true },
    { label: "Status", value: (e) => badge(e.status) },
    { label: "Runs", value: (e) => fmt.int(e.summary?.runs_attempted), num: true },
    { label: "Outcomes", value: (e) => h("span", { class: "small" }, Object.entries(e.summary?.by_status || {}).map(([k, v]) => `${v} ${k}`).join(", "), e.summary?.conformance && Object.keys(e.summary.conformance).length ? ` · tests: ${Object.entries(e.summary.conformance).map(([k, v]) => `${v} ${k}`).join(", ")}` : "") },
    { label: "Config", value: (e) => h("span", { class: "small mono" }, `${(e.config?.designs || []).length} designs · repeats ${e.config?.repeats} · ${(e.config?.suites || []).join("+")}`) },
  ], rows, { maxHeight: "560px" }) : empty("No experiments.");
}
function jobs(rows) {
  return rows.length ? table([{ label: "Job", key: "job_id" }, { label: "Kind", key: "kind" }, { label: "Status", value: (j) => badge(j.status) },
    { label: "Created", value: (j) => fmt.time(j.created_at) }, { label: "Error", value: (j) => j.error || "" }], rows) : empty("No jobs submitted from the UI yet.");
}
