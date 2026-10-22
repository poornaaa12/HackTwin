import { meta } from "../api.js";
import { card, h } from "../dom.js";
export async function render() {
  const m = await meta();
  return h("div", {}, h("div", { class: "page-head" }, h("h1", {}, "Glossary"), h("p", { class: "lede" }, "Terms used throughout HackTwin, and what each measurement can and cannot show.")),
    card("Terms", h("dl", { class: "kv" }, Object.values(m.glossary).flatMap((g) => [h("dt", {}, g.term), h("dd", {}, g.text)]))),
    card("Metric definitions", h("dl", { class: "kv" }, Object.entries(m.metric_definitions).flatMap(([k, v]) => [h("dt", { class: "mono" }, k), h("dd", {}, v)]))));
}
