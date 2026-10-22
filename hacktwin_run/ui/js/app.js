import { api, meta } from "./api.js";
import { clear, errorBox, h, loading, setGlossary } from "./dom.js";

const routes = {
  overview: () => import("./views/overview.js"),
  designs: () => import("./views/designs.js"),
  diff: () => import("./views/designs.js"),
  workbench: () => import("./views/workbench.js"),
  trace: () => import("./views/trace.js"),
  bottlenecks: () => import("./views/bottlenecks.js"),
  compare: () => import("./views/compare.js"),
  recommendations: () => import("./views/recommendations.js"),
  predictor: () => import("./views/predictor.js"),
  dataset: () => import("./views/dataset.js"),
  glossary: () => import("./views/glossary.js"),
};

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "") || "overview";
  const [path, qs] = raw.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  return { section: parts[0] || "overview", parts: parts.slice(1), query: Object.fromEntries(new URLSearchParams(qs || "")) };
}
export function go(hash) { location.hash = hash; }

let renderToken = 0;
async function render() {
  const route = parseHash();
  const view = document.getElementById("view");
  const token = ++renderToken;
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === (route.section === "diff" ? "designs" : route.section)));
  clear(view).append(loading());
  try {
    const loader = routes[route.section] || routes.overview;
    const mod = await loader();
    const node = await (route.section === "diff" ? mod.renderDiff(route) : mod.render(route));
    if (token !== renderToken) return;
    clear(view).append(node);
    view.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  } catch (err) {
    if (token === renderToken) clear(view).append(errorBox(err));
    console.error(err);
  }
}

async function boot() {
  try {
    const m = await meta();
    setGlossary(m.glossary);
    const st = document.getElementById("status");
    st.textContent = `Store schema v${m.schema_version} · simulator ${m.simulator.available ? "found" : "NOT found"}`;
    st.title = `${m.simulator.command}\n${m.database}`;
    if (m.dataset_label) {
      const banner = document.createElement("div");
      banner.className = "notice warn";
      banner.style.margin = "0";
      banner.style.borderRadius = "0";
      banner.textContent = m.dataset_label.text;
      document.querySelector(".topbar").after(banner);
    }
  } catch (err) {
    document.getElementById("status").textContent = "API unavailable";
  }
  window.addEventListener("hashchange", render);
  render();
}
boot();
export { api };
