// Builds HackTwin_Project_Explained.docx and .html from the .md source.
const fs = require("fs");
const D = require("docx");
const { Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType, ShadingType,
  BorderStyle, AlignmentType, LevelFormat, Footer, PageNumber, TableOfContents, PageBreak } = D;

const src = fs.readFileSync("HackTwin_Project_Explained.md", "utf8").split("\n");
// ---- parse into blocks
const blocks = []; let i = 0;
while (i < src.length) {
  const l = src[i];
  if (l.startsWith("```")) { const code = []; i++; while (!src[i].startsWith("```")) code.push(src[i++]); i++; blocks.push({ t: "code", lines: code }); continue; }
  if (/^#{1,3} /.test(l)) { const n = l.match(/^#+/)[0].length; blocks.push({ t: "h", n, text: l.slice(n + 1) }); i++; continue; }
  if (l.startsWith("|")) { const rows = []; while (i < src.length && src[i].startsWith("|")) { if (!/^\|[-| ]+\|$/.test(src[i])) rows.push(src[i].slice(1, -1).split("|").map(c => c.trim())); i++; } blocks.push({ t: "table", rows }); continue; }
  if (l.startsWith("> ")) { blocks.push({ t: "note", text: l.slice(2) }); i++; continue; }
  if (/^- /.test(l)) { const items = []; while (i < src.length && /^- /.test(src[i])) items.push(src[i++].slice(2)); blocks.push({ t: "ul", items }); continue; }
  if (/^\d+\. /.test(l)) { const items = []; while (i < src.length && (/^\d+\. /.test(src[i]) || /^   \S/.test(src[i]))) { if (/^\d+\. /.test(src[i])) items.push(src[i].replace(/^\d+\. /, "")); else items[items.length - 1] += " " + src[i].trim(); i++; } blocks.push({ t: "ol", items }); continue; }
  if (l.trim()) { blocks.push({ t: "p", text: l }); }
  i++;
}
// ---- inline: **bold**, *italic*, `code`
function spans(text) {
  const out = []; const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g; let last = 0, m;
  while ((m = re.exec(text))) { if (m.index > last) out.push({ s: text.slice(last, m.index) }); const tok = m[0];
    if (tok.startsWith("**")) out.push({ s: tok.slice(2, -2), b: 1 }); else if (tok.startsWith("`")) out.push({ s: tok.slice(1, -1), c: 1 }); else out.push({ s: tok.slice(1, -1), i: 1 });
    last = re.lastIndex; }
  if (last < text.length) out.push({ s: text.slice(last) });
  return out;
}
const TEAL = "0F4C5C";
function runs(text, base = {}) { return spans(text).map(p => new TextRun({ text: p.s, bold: !!p.b || base.bold, italics: !!p.i || base.italics, font: p.c ? "Consolas" : undefined, size: p.c ? 19 : base.size, color: base.color, shading: p.c ? { type: ShadingType.CLEAR, fill: "EEF3F4", color: "auto" } : undefined })); }
const border = { style: BorderStyle.SINGLE, size: 4, color: "C9D4D7" };
const borders = { top: border, bottom: border, left: border, right: border };
const PAGE_W = 9026; // A4 width minus 1" margins (DXA)

const children = [];
// title page
const title = blocks.shift(); const sub = blocks.shift(); const meta = blocks.shift();
children.push(new Paragraph({ spacing: { before: 2400, after: 300 }, children: [new TextRun({ text: title.text, bold: true, size: 56, color: TEAL, font: "Cambria" })] }));
children.push(new Paragraph({ spacing: { after: 600 }, children: runs(sub.text, { size: 28, color: "55636A" }) }));
children.push(new Paragraph({ spacing: { after: 300 }, children: runs(meta.text, { size: 22 }) }));
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Contents")] }));
children.push(new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }));
children.push(new Paragraph({ children: [runs("(In Word: right-click the table of contents and choose Update Field if page numbers are missing.)", { italics: true, color: "55636A", size: 18 })[0]] }));
children.push(new Paragraph({ children: [new PageBreak()] }));

let firstH1 = true;
for (const b of blocks) {
  if (b.t === "h") {
    const lvl = [null, HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3][b.n];
    children.push(new Paragraph({ heading: lvl, pageBreakBefore: b.n === 1 && !firstH1, children: runs(b.text) }));
    if (b.n === 1) firstH1 = false;
  } else if (b.t === "p") children.push(new Paragraph({ spacing: { after: 140 }, children: runs(b.text) }));
  else if (b.t === "note") children.push(new Paragraph({ spacing: { before: 120, after: 200 }, shading: { type: ShadingType.CLEAR, fill: "EEF3F4", color: "auto" }, border: { left: { style: BorderStyle.SINGLE, size: 18, color: TEAL, space: 8 } }, indent: { left: 200, right: 200 }, children: runs(b.text) }));
  else if (b.t === "ul" || b.t === "ol") b.items.forEach(it => children.push(new Paragraph({ numbering: { reference: b.t === "ul" ? "bul" : "num" + children.length, level: 0 }, spacing: { after: 80 }, children: runs(it) })));
  else if (b.t === "code") b.lines.forEach((ln, k) => children.push(new Paragraph({ spacing: { before: k ? 0 : 80, after: k === b.lines.length - 1 ? 160 : 0 }, shading: { type: ShadingType.CLEAR, fill: "F3F5F6", color: "auto" }, indent: { left: 200 }, children: [new TextRun({ text: ln || " ", font: "Consolas", size: 17 })] })));
  else if (b.t === "table") {
    const n = b.rows[0].length;
    const lens = b.rows[0].map((_, c) => Math.max(...b.rows.map(r => (r[c] || "").length)));
    const tot = lens.reduce((a, x) => a + Math.max(8, Math.min(x, 70)), 0);
    let widths = lens.map(x => Math.floor(PAGE_W * Math.max(8, Math.min(x, 70)) / tot)); widths[n - 1] += PAGE_W - widths.reduce((a, x) => a + x, 0);
    children.push(new Table({ width: { size: PAGE_W, type: WidthType.DXA }, columnWidths: widths,
      rows: b.rows.map((r, ri) => new TableRow({ tableHeader: ri === 0, children: r.map((c, ci) => new TableCell({ borders, width: { size: widths[ci], type: WidthType.DXA },
        shading: ri === 0 ? { type: ShadingType.CLEAR, fill: TEAL, color: "auto" } : (ri % 2 ? undefined : { type: ShadingType.CLEAR, fill: "F7F9FA", color: "auto" }),
        margins: { top: 60, bottom: 60, left: 100, right: 100 },
        children: [new Paragraph({ children: runs(c, ri === 0 ? { bold: true, color: "FFFFFF", size: 19 } : { size: 19 }) })] })) })) }));
    children.push(new Paragraph({ spacing: { after: 120 }, children: [] }));
  }
}
const numConfigs = [{ reference: "bul", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] }];
children.forEach(p => { const ref = p?.properties?.numbering?.reference; });
// one numbering instance per ordered list so each restarts at 1
const olRefs = new Set(); for (const c of children) { const r = c.options?.numbering?.reference; }
const doc0 = []; // collect references used
const refs = new Set(JSON.stringify(children).match(/num\d+/g) || []);
for (const r of refs) numConfigs.push({ reference: r, levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] });

const doc = new Document({
  creator: "HackTwin TEAM-14", title: "HackTwin: The Complete Beginner's Guide",
  styles: { default: { document: { run: { font: "Calibri", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 34, bold: true, font: "Cambria", color: TEAL }, paragraph: { spacing: { before: 240, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 27, bold: true, font: "Cambria", color: TEAL }, paragraph: { spacing: { before: 260, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 23, bold: true, font: "Calibri", color: "1E2A30" }, paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 } }] },
  numbering: { config: numConfigs },
  features: { updateFields: true },
  sections: [{ properties: { page: { margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "HackTwin: The Complete Beginner's Guide  ·  page ", size: 16, color: "55636A" }), new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "55636A" })] })] }) },
    children }],
});
Packer.toBuffer(doc).then(buf => { fs.writeFileSync("HackTwin_Project_Explained.docx", buf); console.log("docx ok"); });

// ---- HTML for PDF
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const inl = t => spans(t).map(p => p.b ? `<strong>${esc(p.s)}</strong>` : p.c ? `<code>${esc(p.s)}</code>` : p.i ? `<em>${esc(p.s)}</em>` : esc(p.s)).join("");
let html = `<!doctype html><html><head><meta charset="utf-8"><title>${esc(title.text)}</title><style>
@page{size:A4;margin:22mm 20mm 20mm 20mm}
body{font:10.5pt/1.5 Calibri,"Helvetica Neue",Arial,sans-serif;color:#1E2A30}
h1{font:bold 19pt Cambria,Georgia,serif;color:#0F4C5C;page-break-before:always;margin:0 0 10pt}
h2{font:bold 14pt Cambria,Georgia,serif;color:#0F4C5C;margin:16pt 0 6pt;page-break-after:avoid}
h3{font-size:11.5pt;margin:12pt 0 4pt;page-break-after:avoid}
.title h1{page-break-before:avoid;font-size:30pt;margin-top:80mm}.title p.sub{font-size:14pt;color:#55636A}
p{margin:0 0 7pt} table{border-collapse:collapse;width:100%;margin:4pt 0 10pt;font-size:9.5pt;page-break-inside:auto}
tr{page-break-inside:avoid} th{background:#0F4C5C;color:#fff;text-align:left}th,td{border:1px solid #C9D4D7;padding:3pt 5pt;vertical-align:top}
tr:nth-child(even) td{background:#F7F9FA} code{font:8.8pt Menlo,Consolas,monospace;background:#EEF3F4;padding:0 2px;overflow-wrap:anywhere}
pre{font:8.6pt/1.4 Menlo,Consolas,monospace;background:#F3F5F6;padding:6pt 8pt;white-space:pre-wrap;overflow-wrap:anywhere;page-break-inside:avoid}
.note{background:#EEF3F4;border-left:4px solid #0F4C5C;padding:6pt 10pt;margin:8pt 0}
.toc a{color:#0F4C5C;text-decoration:none}.toc li{margin:2pt 0} li{margin:0 0 3pt}
</style></head><body><div class="title"><h1>${esc(title.text)}</h1><p class="sub">${inl(sub.text)}</p><p>${inl(meta.text)}</p></div>`;
let hid = 0; const toc = []; const body = [];
for (const b of blocks) {
  if (b.t === "h") { const id = "s" + (hid++); if (b.n <= 2) toc.push([b.n, id, b.text]); body.push(`<h${b.n} id="${id}">${inl(b.text)}</h${b.n}>`); }
  else if (b.t === "p") body.push(`<p>${inl(b.text)}</p>`);
  else if (b.t === "note") body.push(`<div class="note">${inl(b.text)}</div>`);
  else if (b.t === "ul") body.push("<ul>" + b.items.map(x => `<li>${inl(x)}</li>`).join("") + "</ul>");
  else if (b.t === "ol") body.push("<ol>" + b.items.map(x => `<li>${inl(x)}</li>`).join("") + "</ol>");
  else if (b.t === "code") body.push(`<pre>${esc(b.lines.join("\n"))}</pre>`);
  else if (b.t === "table") body.push("<table>" + b.rows.map((r, ri) => "<tr>" + r.map(c => ri ? `<td>${inl(c)}</td>` : `<th>${inl(c)}</th>`).join("") + "</tr>").join("") + "</table>");
}
html += `<h1>Contents</h1><ul class="toc" style="list-style:none;padding:0">` + toc.map(([n, id, t]) => `<li style="margin-left:${(n - 1) * 16}pt"><a href="#${id}">${inl(t)}</a></li>`).join("") + "</ul>" + body.join("\n") + "</body></html>";
fs.writeFileSync("HackTwin_Project_Explained.html", html); console.log("html ok");
