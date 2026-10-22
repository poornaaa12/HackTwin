const pptxgen = require("pptxgenjs");
const { applyTheme } = require("/Users/cow/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/43ab2c4a-13ce-41e9-ad69-7665c8650b16/c4b3820c-6639-4622-bd74-51b911c5fa4c/skills/pptx/scripts/apply_theme.js");

const THEME = { name: "HackTwin Calm", headFontFace: "Cambria", bodyFontFace: "Calibri",
  colors: { dk1: "1E2A30", lt1: "FFFFFF", dk2: "0F4C5C", lt2: "EEF3F4", accent1: "0F4C5C", accent2: "C9822B",
    accent3: "5B8C85", accent4: "9AA5A9", accent5: "B5543C", accent6: "2F6E3B", hlink: "0F4C5C", folHlink: "5B8C85" } };
const HEX = { ink: "1E2A30", teal: "0F4C5C", ochre: "C9822B", sage: "5B8C85", grey: "9AA5A9", tint: "EEF3F4", red: "B5543C", green: "2F6E3B", mid: "55636A" };

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "HackTwin: a digital twin for Hack CPU HDL designs";
pres.author = "TEAM-14, 23AID113 Elements of Computing 2";
const C = pres.SchemeColor;

pres.defineSlideMaster({ title: "CONTENT", background: { color: "FFFFFF" },
  objects: [{ placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.35, w: 12.1, h: 0.8, fontFace: "Cambria", fontSize: 32, bold: true, color: C.text2, valign: "middle", margin: 0 }, text: "" } }],
  slideNumber: { x: 12.3, y: 7.0, w: 0.6, h: 0.3, fontSize: 10, color: HEX.mid } });
pres.defineSlideMaster({ title: "DARK", background: { color: HEX.teal }, objects: [] });

const W = 13.33;
function src(slide, text) { slide.addText(text, { x: 0.6, y: 6.95, w: 11.5, h: 0.35, fontSize: 10, color: HEX.mid, italic: true, isTextBox: true, margin: 0, objectName: "Source note" }); }
function box(slide, x, y, w, h, fill, opts = {}) { slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: fill }, line: { color: opts.line || fill, width: 1 }, rectRadius: 0.08, objectName: opts.name || "Card" }); }
function txt(slide, t, x, y, w, h, o = {}) { slide.addText(t, { x, y, w, h, fontSize: 15, color: HEX.ink, valign: "top", isTextBox: true, margin: 0.08, ...o }); }
function bullets(items, size = 15) { return items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < items.length - 1, paraSpaceAfter: 6, fontSize: size } })); }
function notes(slide, script, sources, q, a) { slide.addNotes(`SPEAKER SCRIPT\n${script}\n\nSOURCES / EVIDENCE\n${sources}\n\nLIKELY VIVA QUESTION\nQ: ${q}\nA: ${a}`); }

// ---------- 1 Title
pres.addSection({ title: "Introduction" });
let s = pres.addSlide({ masterName: "DARK", sectionTitle: "Introduction" });
s.addText("23AID113 · Elements of Computing 2 · TEAM-14", { x: 0.8, y: 0.7, w: 11.5, h: 0.4, fontSize: 16, color: "D7E4E6", isTextBox: true, margin: 0 });
s.addText("HackTwin", { x: 0.8, y: 1.5, w: 11.5, h: 1.2, fontFace: "Cambria", fontSize: 60, bold: true, color: "FFFFFF", isTextBox: true, margin: 0 });
s.addText("A digital twin framework for Hack CPU HDL designs", { x: 0.8, y: 2.7, w: 11.5, h: 0.6, fontFace: "Cambria", fontSize: 26, color: "FFFFFF", isTextBox: true, margin: 0 });
box(s, 0.8, 3.7, 11.7, 1.35, "1B6274", { name: "Summary card" });
s.addText("In one sentence: HackTwin runs Hack CPU designs through the official nand2tetris simulator, records what happens on every clock cycle, stores and analyses those records, compares designs, and shows the findings in a dashboard.",
  { x: 1.05, y: 3.8, w: 11.2, h: 1.15, fontSize: 18, color: "FFFFFF", valign: "middle", isTextBox: true, margin: 0 });
s.addText([{ text: "Boyilla Charan Teja Reddy (CB.AI.U4AAR25011)  ·  Poornashree P (CB.AI.U4AAR25036)", options: { breakLine: true } },
  { text: "Thulasiram Sudheeswar Reddy (CB.AI.U4AAR25053)  ·  Harshitha Tippireddy (CB.AI.U4AAR25054)" }],
  { x: 0.8, y: 5.45, w: 11.7, h: 0.8, fontSize: 14, color: "D7E4E6", isTextBox: true, margin: 0 });
s.addText("Team and course details: title slide of HackTwin_MidTerm_Review.pptx", { x: 0.8, y: 6.85, w: 11, h: 0.35, fontSize: 10, italic: true, color: "B8CDD1", isTextBox: true, margin: 0 });
notes(s, "Good morning. Our project is HackTwin. In the Elements of Computing course we build a small computer, the Hack computer, by describing its chips in a hardware description language. HackTwin is a tool that runs those computer designs automatically in the official simulator, keeps a record of everything that happened, and helps us compare designs and understand where time is spent. Keep this one sentence in mind: run, record, store, analyse, compare, show.",
  "Team names, roll numbers and course code from slide 1 of HackTwin_MidTerm_Review.pptx. One-sentence summary matches hacktwin_run/README.md.",
  "Is HackTwin a digital twin of a physical chip?",
  "No. It is a software record of simulated HDL designs. Nothing is measured on real hardware, so it says nothing about real speed, area or power.");

// ---------- 2 Problem
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Introduction" });
s.addText("The problem: raw simulator output is hard to use", { placeholder: "title" });
txt(s, bullets([
  "A Hack CPU is the simple 16-bit computer processor we build in nand2tetris.",
  "It is written in HDL (hardware description language): text files that describe how chips are wired.",
  "A benchmark is a small test program (e.g. add 2 + 3) run on every design so results are comparable.",
  "The simulator (nand2tetris Hardware Simulator) executes the HDL and prints one row per clock cycle, the computer's basic time step.",
  "Doing this by hand for many designs, programs and repeats means reading thousands of rows like the ones on the right.",
], 15), 0.6, 1.4, 6.3, 4.6);
box(s, 7.3, 1.4, 5.4, 4.5, HEX.tint, { name: "Trace example card" });
txt(s, "Real output: design 1 running Add (excerpt)", 7.5, 1.5, 5.0, 0.4, { bold: true, fontSize: 14, color: HEX.teal });
s.addText("time reset A  D  PC  RAM[0]\n 0    0    0  0   0    0\n 1    0    2  0   1    0\n 2    0    2  2   2    0\n 3    0    3  2   3    0\n 4    0    3  5   4    0\n 5    0    0  5   5    0\n 6    0    0  5   6    5   <- answer 5\n 7    1    0  5   0    0   <- reset",
  { x: 7.5, y: 1.95, w: 5.0, h: 2.9, fontFace: "Courier New", fontSize: 14, color: HEX.ink, isTextBox: true, margin: 0.05 });
txt(s, "Which row holds the answer? Was it correct? Was design B any quicker? The raw file does not say.", 7.5, 4.95, 5.0, 0.85, { fontSize: 14, italic: true, color: HEX.mid });
src(s, "Trace: hacktwin_run/data/raw_runs/1__ComputerAdd__r0/ComputerAdd.out (columns abbreviated). Problem framing: old deck slide 2.");
notes(s, "Let me start with the problem. Our CPU designs are text files in HDL. To test one, we run a small benchmark program in the simulator, and it prints one line per clock cycle. Here is real output for adding 2 and 3: the answer 5 appears in RAM zero at cycle 6. With several designs, ten programs and repeats, nobody can read all of this by hand, check every answer, and compare designs fairly. HackTwin automates that.",
  "ComputerAdd.out from the repository; terms defined per hacktwin_run/README.md.",
  "Why not just look at the simulator's pass/fail message?",
  "Pass/fail tells you only whether the output matched the expected file. It does not tell you how many cycles a program took, where they were spent, or how two designs differ.");

// ---------- 3 Literature
pres.addSection({ title: "Background" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Background" });
s.addText("Literature review", { placeholder: "title" });
const hdr = (t) => ({ text: t, options: { bold: true, color: "FFFFFF", fill: { color: HEX.teal }, fontSize: 13 } });
const cell = (t, b) => ({ text: t, options: { fontSize: 12, bold: !!b, color: HEX.ink } });
const lit = [
  ["Digital Twin in Manufacturing: A Categorical Literature Review and Classification", "W. Kritzinger, M. Karner, G. Traar, J. Henjes, W. Sihn", "2018", "Classifies digital-twin concepts; relates physical systems, virtual representations and data exchange."],
  ["A Survey on Digital Twin: Definitions, Characteristics, Applications, and Design Implications", "B. R. Barricelli, E. Casiraghi, D. Fogli", "2019", "Reviews digital-twin definitions, characteristics, applications and design considerations."],
  ["Digital Twin: Enabling Technologies, Challenges and Open Research", "A. Fuller, Z. Fan, C. Day, C. Barlow", "2020", "Enabling technologies and open challenges: simulation, data management, analytics, prediction."],
  ["Characterising the Digital Twin: A Systematic Literature Review", "D. Jones, C. Snider, A. Nassehi, J. Yon, B. Hicks", "2020", "Systematic review of digital-twin definitions, characteristics and implementations."],
  ["The gem5 Simulator", "N. Binkert et al.", "2011", "Configurable computer-system simulator for processor and architecture research."],
  ["McPAT: An Integrated Power, Area, and Timing Modeling Framework for Multicore and Manycore Architectures", "S. Li, J. H. Ahn, R. D. Strong, J. B. Brockman, D. M. Tullsen, N. P. Jouppi", "2009", "Quantitative modelling of processor power, area and timing."],
];
s.addTable([[hdr("Paper"), hdr("Authors"), hdr("Year"), hdr("Main contribution")], ...lit.map((r) => [cell(r[0], true), cell(r[1]), cell(r[2]), cell(r[3])])],
  { x: 0.6, y: 1.35, w: 12.1, colW: [3.9, 3.2, 0.7, 4.3], border: { type: "solid", color: "D5DEE0", pt: 0.75 }, fill: { color: "FFFFFF" }, rowH: 0.62, valign: "middle", objectName: "Literature table" });
txt(s, "How HackTwin relates: it borrows the digital-twin idea (a virtual record kept in sync with data from a system), but applies it to simulated HDL designs, not physical machines. Unlike gem5 and McPAT, it does not model power, area or timing.", 0.6, 5.85, 12.1, 0.9, { fontSize: 14, color: HEX.teal });
src(s, "Studies, authors, years and contributions reproduced from the literature-review image (slide 3) of HackTwin_MidTerm_Review.pptx; wording shortened.");
notes(s, "This is the same literature review as our mid-term deck. Four papers define and survey digital twins: Kritzinger and colleagues, Barricelli and colleagues, Fuller and colleagues, and Jones and colleagues. Two are from computer architecture: gem5, a full-system simulator, and McPAT, which models power, area and timing. We take the idea of a digital twin, a continuously updated virtual record of a system, and apply it to simulated CPU designs. We are honest that, unlike McPAT, we do not estimate power, area or timing.",
  "Old deck slide 3 (image media/image2.png): six studies with authors, years and contributions as listed.",
  "Your literature talks about twins of physical systems. Why call this a digital twin?",
  "We use the term loosely: the 'system' is the simulated HDL design, and the twin is the stored, analysable record of its behaviour. We state clearly that it is not a twin of a physical chip.");

// ---------- 4 What it does
pres.addSection({ title: "The system" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "The system" });
s.addText("What HackTwin does", { placeholder: "title" });
const cols = [
  ["Inputs", ["CPU designs: folders of HDL files (8 HackTwin designs + imported ones)", "Benchmark programs with input values and expected answers", "Official nand2tetris test scripts"]],
  ["What it does", ["Runs every design on every benchmark in the simulator, with repeats", "Records an execution trace: the step-by-step log of signals each cycle", "Checks answers, stores results, analyses and compares designs"]],
  ["Outputs", ["Cycle counts and correct/incorrect verdicts", "Bottleneck findings and design comparisons", "A web dashboard and a generated report"]],
];
cols.forEach(([h, items], i) => { const x = 0.6 + i * 4.1; box(s, x, 1.4, 3.85, 3.3, HEX.tint); txt(s, h, x + 0.2, 1.5, 3.5, 0.45, { bold: true, fontSize: 18, color: HEX.teal });
  txt(s, bullets(items, 14), x + 0.2, 2.0, 3.5, 2.65); if (i < 2) s.addShape(pres.shapes.RIGHT_ARROW, { x: x + 3.88, y: 2.9, w: 0.2, h: 0.3, fill: { color: HEX.ochre }, line: { color: HEX.ochre }, objectName: "Arrow" }); });
box(s, 0.6, 4.95, 6.0, 1.8, "FFFFFF", { line: HEX.sage });
txt(s, [{ text: "'Digital twin' here means: ", options: { bold: true } }, { text: "a stored, queryable record of how each simulated design behaved, kept up to date as designs and runs change. It is not a copy of a physical chip." }], 0.75, 5.05, 5.7, 1.6, { fontSize: 14 });
box(s, 6.75, 4.95, 5.95, 1.8, "FFFFFF", { line: HEX.red });
txt(s, [{ text: "It does not: ", options: { bold: true, color: HEX.red } }, { text: "measure real hardware, clock speed, chip area or power; or prove that a design would be faster as a real chip. Fewer simulated cycles is not the same as faster hardware." }], 6.9, 5.05, 5.65, 1.6, { fontSize: 14 });
src(s, "hacktwin_run/README.md (purpose, limitations); designs/ folder; benchmarks/suite.json.");
notes(s, "So what does HackTwin actually do? It takes three inputs: CPU designs, benchmark programs with known answers, and the official test scripts. It runs every combination in the simulator and records an execution trace, which is just the cycle-by-cycle log you saw. Then it checks the answers, stores everything, analyses it and compares designs. When we say digital twin, we mean that stored record of each design's simulated behaviour. And just as important is what it does not do: no real hardware, no clock speed, no area or power.",
  "README.md sections 'HackTwin' intro and 'Known limitations'.",
  "What is the difference between a correct design and a faster design?",
  "Correct means it produced the expected answers. Faster, in our tool, only means it needed fewer simulated clock cycles on the same program. A design can be correct and slower, as our two-phase design shows.");

// ---------- 5 Architecture
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "The system" });
s.addText("System architecture and data flow", { placeholder: "title" });
const stages = [
  ["HDL designs + benchmark programs", "designs/, benchmarks/"],
  ["Simulator runs", "sim_runner.py, experiment_runner.py"],
  ["Trace parsing & checking", "against ISA reference model"],
  ["SQLite twin store", "twin_store.py"],
  ["Analysis & comparison", "bottlenecks, ranking, recommendations"],
  ["Dashboard & report", "app.py, generate_report.py"],
];
const bw = 1.82, gap = 0.22, y0 = 1.7;
stages.forEach(([h, sub], i) => { const x = 0.6 + i * (bw + gap); box(s, x, y0, bw, 1.75, i === 3 ? HEX.teal : HEX.tint);
  txt(s, h, x + 0.08, y0 + 0.12, bw - 0.16, 0.95, { bold: true, fontSize: 15, color: i === 3 ? "FFFFFF" : HEX.teal, align: "center" });
  txt(s, sub, x + 0.08, y0 + 1.05, bw - 0.16, 0.65, { fontSize: 11, color: i === 3 ? "D7E4E6" : HEX.mid, align: "center" });
  if (i < stages.length - 1) s.addShape(pres.shapes.RIGHT_ARROW, { x: x + bw + 0.02, y: y0 + 0.75, w: 0.18, h: 0.28, fill: { color: HEX.ochre }, line: { color: HEX.ochre }, objectName: "Flow arrow" }); });
const px = 0.6 + 3 * (bw + gap);
s.addShape(pres.shapes.LINE, { x: px + bw / 2, y: y0 + 1.75, w: 0, h: 0.6, line: { color: HEX.ochre, width: 2, endArrowType: "triangle" }, objectName: "Down arrow" });
box(s, px - 0.6, y0 + 2.4, bw + 1.2, 1.0, "FFFFFF", { line: HEX.sage });
txt(s, [{ text: "Predictor (ML) ", options: { bold: true, color: HEX.teal } }, { text: "learns from stored runs; its output is an estimate, never a measurement" }], px - 0.5, y0 + 2.45, bw + 1.0, 0.95, { fontSize: 13, align: "center" });
box(s, 0.6, 5.35, 5.4, 1.4, "FFFFFF", { line: HEX.sage });
txt(s, [{ text: "GitHub dataset ", options: { bold: true, color: HEX.teal } }, { text: "(public student CPU designs, imported by exact commit) enters at the left as more HDL designs." }], 0.75, 5.45, 5.1, 1.2, { fontSize: 14 });
txt(s, bullets(["Every arrow is a Python module you can run on its own from the command line.", "The dashboard only reads from the store, so every number it shows comes from a recorded run."], 14), 6.3, 5.4, 6.4, 1.4);
src(s, "Module names verified in hacktwin_run/ (README.md 'Architecture').");
notes(s, "Here is the whole system as a left-to-right flow. Designs and programs go into the simulator runner. The output trace is parsed and checked against an independent reference model of the Hack instruction set. Results go into a SQLite database, our twin store. Analysis and comparison read from the store, and the dashboard and report present them. The predictor sits beside the store: it learns from stored runs, but its outputs are estimates and are labelled that way. The GitHub dataset simply supplies more designs on the left.",
  "File and module names exist in hacktwin_run/; flow described in README.md.",
  "Why SQLite instead of files?",
  "One file, no server, transactions so a failed load does not leave half the data, and versioned schema migrations so older databases are upgraded instead of lost.");

// ---------- 6 Simulation and storage
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "The system" });
s.addText("Simulation, traces and storage", { placeholder: "title" });
const steps = [["1", "Wrap the design", "The design's CPU and memory are placed in a harness chip that exposes the CPU's signals."],
  ["2", "Run to completion", "A generated test script runs the program; one row per cycle until it reaches its end loop."],
  ["3", "Record the trace", "Program counter, instruction, A and D registers, memory reads/writes, chosen RAM cells."],
  ["4", "Check the result", "Compare answers with expected values and with an independent model of the Hack instructions."]];
steps.forEach(([n, h, d], i) => { const y = 1.45 + i * 1.2; s.addShape(pres.shapes.OVAL, { x: 0.6, y, w: 0.6, h: 0.6, fill: { color: HEX.teal }, line: { color: HEX.teal }, objectName: "Step " + n });
  s.addText(n, { x: 0.6, y, w: 0.6, h: 0.6, align: "center", valign: "middle", fontSize: 18, bold: true, color: "FFFFFF", isTextBox: true, margin: 0 });
  txt(s, [{ text: h + ": ", options: { bold: true, color: HEX.teal } }, { text: d }], 1.35, y - 0.05, 5.6, 1.05, { fontSize: 14 }); });
box(s, 7.3, 1.4, 5.4, 4.95, HEX.tint);
txt(s, "What the SQLite twin store keeps", 7.5, 1.5, 5.0, 0.45, { bold: true, fontSize: 17, color: HEX.teal });
txt(s, bullets(["Designs with three identities: file hash, comment-free hash, wiring (netlist) hash", "Design history: which design each variant came from", "Every run, including failures and their error messages", "Compressed traces and the raw simulator output", "Official test results, experiments, background jobs, GitHub provenance", "Versioned schema (now v3); the old database was upgraded in place"], 14), 7.5, 2.0, 5.0, 4.3);
src(s, "sim_runner.py, harness/TwinComputer.hdl, twin_store.py (migrations v1–v3), hdl_inspector.py.");
notes(s, "This slide shows how one run works. We wrap the design in a small harness chip so that we can see the CPU's internal signals. The simulator runs the program until it reaches its final loop, writing one row per cycle. We record the program counter, which is the address of the current instruction, the registers and every memory write. Then we check the answer two ways: against the expected value, and against an independent model of the instruction set. Everything goes into the store, including failures, so nothing is silently dropped.",
  "Implementation in sim_runner.py and twin_store.py; schema version 3 confirmed by the app status bar.",
  "Why do you need three different hashes for a design?",
  "A file hash changes even when only a comment changes. The comment-free hash ignores formatting. The netlist hash ignores part order and wire names, so it tells us whether the hardware wiring actually differs. We found design 3 had the same wiring as design 2.");

// ---------- 7 Analysis
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "The system" });
s.addText("Analysis, comparison and recommendations", { placeholder: "title" });
const cards = [
  ["Bottleneck analysis", "A bottleneck is a part of the run that uses a large share of cycles. HackTwin shows the busiest instruction addresses, loops, register and memory activity. Findings are labelled observation, heuristic, confirmed problem or data-quality warning."],
  ["Comparison", "Same program, same inputs, only correct and completed runs. Speedup = baseline cycles ÷ candidate cycles. Failed or missing runs are listed, never counted as zero."],
  ["Ranking", "One documented metric: average (geometric mean) speedup over all core cases. Designs that are wrong on any case are not ranked. Ties are shown as ties."],
  ["Recommendations", "Each suggestion names the evidence and an experiment. It is marked 'verified' only when the suggested design was actually simulated and used fewer cycles."]];
cards.forEach(([h, d], i) => { const x = 0.6 + (i % 2) * 6.15, y = 1.4 + Math.floor(i / 2) * 2.6; box(s, x, y, 5.95, 2.4, HEX.tint);
  txt(s, h, x + 0.2, y + 0.12, 5.5, 0.45, { bold: true, fontSize: 17, color: HEX.teal }); txt(s, d, x + 0.2, y + 0.62, 5.6, 1.7, { fontSize: 14 }); });
src(s, "bottleneck_analysis.py, comparison_engine.py (rank_program_designs), recommendation_generator.py.");
notes(s, "Once runs are stored, we analyse them. Bottleneck analysis shows where cycles go, for example which loop takes most of the time. We are careful to label each finding: an observation is a fact from the trace, a heuristic is a suggestion worth testing, and a confirmed problem is something like a wrong answer. Comparison uses only correct runs of the same program and inputs, and speedup is simply baseline cycles divided by candidate cycles. Ranking uses that one number, with no hidden weights. Recommendations are only called verified when we actually simulated the suggested design.",
  "Function names and labels verified in the listed modules; recommendation statuses shown in the dashboard.",
  "Is frequent register change a bug?",
  "No. In Hack the A register holds both addresses and jump targets, so it changes often in normal code. We report it as an observation, not a defect.");

// ---------- 8 GitHub and ML
pres.addSection({ title: "Data, results and status" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Data, results and status" });
s.addText("GitHub data and machine learning", { placeholder: "title" });
box(s, 0.6, 1.4, 5.9, 5.35, HEX.tint);
txt(s, "Two GitHub datasets", 0.8, 1.5, 5.5, 0.45, { bold: true, fontSize: 17, color: HEX.teal });
txt(s, bullets(["Programs: 50 Hack programs from six pinned, MIT/Apache repositories (Gunasekare, ArhanChaudhary, y1hao, aalhour, appleweiping, AdamJelley).",
  "CPU designs: 1,346 repositories found by search; 339 retrieved; 344 CPU folders; 208 passed every test.",
  "Only .hdl files fetched, by exact commit; no repository code is run; licences recorded (189 repositories have none)."], 14), 0.8, 2.0, 5.5, 4.6);
box(s, 6.8, 1.4, 5.9, 2.5, "FFFFFF", { line: HEX.sage });
txt(s, [{ text: "Machine-learning model: ", options: { bold: true, color: HEX.teal } }, { text: "a program that learns a pattern from examples. Ours predicts a design's cycle count from features of the program. " },
  { text: "Prediction error ", options: { bold: true, color: HEX.teal } }, { text: "is predicted minus measured cycles; we report the mean absolute error (MAE), tested only on programs held out from training." }], 6.95, 1.5, 5.6, 2.35, { fontSize: 14 });
s.addTable([[hdr("Predictor (held-out programs)"), hdr("MAE, cycles")],
  [cell("Instruction model + per-design calibration"), cell("0.60")], [cell("ML (ExtraTrees), all features"), cell("53.7")],
  [cell("ML, program-text features only"), cell("403.5")], [cell("Median guess (baseline)"), cell("490.0")]],
  { x: 6.8, y: 4.05, w: 5.9, colW: [4.4, 1.5], border: { type: "solid", color: "D5DEE0", pt: 0.75 }, rowH: 0.36, objectName: "Predictor table" });
txt(s, "Estimates only; not valid for an unseen design type.", 6.8, 5.95, 5.9, 0.4, { fontSize: 13, italic: true, color: HEX.red });
src(s, "collect_github_workloads.py SOURCES; reports/EXPERIMENT_REPORT.md; data/models/cycle_predictor_report.json (8 designs, 704 rows, 28 programs).");
notes(s, "We used GitHub in two ways. First, 50 real Hack programs from six pinned, permissively licensed repositories, used as extra test workloads. Second, other students' CPU designs: we found 1,346 repositories, processed 339, and 208 imported designs passed every test. We never run code from those repositories and we record licences. For machine learning, a model learns from examples to predict a cycle count. Prediction error is the difference between predicted and measured cycles. The best predictor uses the instruction count and each design's measured cycles per instruction: error under one cycle. Models that only read the program text are barely better than guessing the median. These are estimates, not measurements, and they do not transfer to a new kind of design.",
  "Six repositories and licences: collect_github_workloads.py. Funnel and MAE numbers: reports/EXPERIMENT_REPORT.md and cycle_predictor_report.json. The guide's older model: MAE 439.5 vs 513.2 median, R² −0.0856 (hacktwin_cycle_model.validation.json).",
  "Is your ML model accurate?",
  "Only the calibrated instruction-count model is accurate, and only for designs it has measurements for. The text-only ML models are weak. The earlier model in the guide had R² below zero, so we treat it as exploratory.");

// ---------- 9 Results
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Data, results and status" });
s.addText("Results: measured in the simulator", { placeholder: "title" });
s.addChart(pres.charts.BAR, [{ name: "Total cycles, 31 core cases", labels: ["Baseline (1)", "alu-xor-negate", "jumpfold-8", "jumpfold-1", "multicycle-2phase"], values: [9766, 9766, 9136, 9156, 19532] }],
  { x: 0.6, y: 1.35, w: 6.6, h: 4.3, barDir: "bar", chartColors: [HEX.teal, HEX.grey, HEX.sage, HEX.sage, HEX.ochre], showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 12, dataLabelColor: HEX.ink,
    showTitle: true, title: "Total simulated cycles, 31 core cases (lower = fewer cycles)", titleFontSize: 13, titleColor: HEX.ink, titleFontFace: "+mn-lt",
    catAxisLabelFontSize: 12, valAxisLabelFontSize: 11, catAxisLabelColor: HEX.mid, valAxisLabelColor: HEX.mid, catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt", dataLabelFontFace: "+mn-lt",
    valGridLine: { color: "E3E8EA", size: 0.5 }, catGridLine: { style: "none" }, showLegend: false, valAxisMinVal: 0, objectName: "Cycles chart" });
s.addTable([[hdr("Verified fact"), hdr("Value")],
  [cell("CPU designs (HackTwin / imported)"), cell("8 / 215")], [cell("Benchmark programs (core / GitHub)"), cell("10 (31 input cases) / 19")],
  [cell("Run-to-halt simulation runs stored"), cell("14,682")], [cell("Correct answers, all 8 designs"), cell("31 of 31 cases")],
  [cell("jumpfold-8 vs baseline"), cell("1.032× speedup")], [cell("multicycle-2phase"), cell("exactly 2× cycles")]],
  { x: 7.5, y: 1.35, w: 5.2, colW: [3.2, 2.0], border: { type: "solid", color: "D5DEE0", pt: 0.75 }, rowH: 0.42, objectName: "Results table" });
txt(s, [{ text: "Shows: ", options: { bold: true, color: HEX.green } }, { text: "the pipeline detects real cycle differences; jump folding saves cycles on loops; rewiring logic alone did not change any count. " },
  { text: "Does not show: ", options: { bold: true, color: HEX.red } }, { text: "faster real chips. Clock speed, area and power were not measured." }], 7.5, 4.45, 5.2, 2.3, { fontSize: 13 });
src(s, "reports/EXPERIMENT_REPORT.md (generated 2026-10-21 from data/hacktwin.db); 2, 3 and branch-table also total 9,766 cycles.");
notes(s, "Here are the measured results. Every number comes from simulator runs stored in the database. All eight of our designs gave correct answers on all 31 test cases. Designs that only rearrange logic, like the XOR-based ALU, took exactly the same cycles as the baseline: an honest null result. Jump folding, which skips repeated jump instructions in loops, cut total cycles by about 6.5 percent. The two-phase design took exactly twice as many cycles, which shows our measurement really detects differences. All 208 correct GitHub designs had baseline timing. What this does not show is that any design is a faster real chip.",
  "All values from reports/EXPERIMENT_REPORT.md and reports/experiment_results.json. Note: the old guide's 14/26/64 'cycles' were test-script row counts, identical for every design by construction.",
  "Your old slides said designs 1 and 2 both took 14, 26 and 64 cycles. Why are the numbers different now?",
  "Those numbers were how many rows the official test script prints, which is fixed by the script, so every design always tied. We now measure cycles until the program actually finishes.");

// ---------- 10 Status
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Data, results and status" });
s.addText("Status, limitations and next steps", { placeholder: "title" });
const cols3 = [["Implemented and tested", HEX.green, ["Simulation, trace checking and storage", "Bottleneck analysis, comparison, ranking, recommendations", "Web dashboard with workbench and trace explorer", "136 unit tests + 7 simulator integration tests passing"]],
  ["Experimental or limited", HEX.ochre, ["Predictor: accurate only after calibrating each design", "GitHub ingestion covered 339 of 1,346 repositories", "At most 11 memory cells observed directly per run", "Memory.tst not run (needs a keyboard)"]],
  ["Next steps (planned)", HEX.teal, ["Process the remaining 1,005 repositories", "A genuinely pipelined Hack CPU", "Timing/area estimates with a synthesis tool", "More varied benchmark programs"]]];
cols3.forEach(([h, col, items], i) => { const x = 0.6 + i * 4.1; box(s, x, 1.4, 3.85, 3.85, HEX.tint); txt(s, h, x + 0.2, 1.5, 3.5, 0.45, { bold: true, fontSize: 17, color: col }); txt(s, bullets(items, 14), x + 0.2, 2.0, 3.5, 3.2); });
box(s, 0.6, 5.45, 12.1, 1.3, HEX.teal);
txt(s, "Conclusion: HackTwin turns raw nand2tetris simulator output into a checked, stored and comparable record of each Hack CPU design, so we can say with evidence which designs are correct and which use fewer simulated cycles, without claiming anything about real hardware.",
  0.85, 5.55, 11.6, 1.1, { fontSize: 15, color: "FFFFFF", valign: "middle" });
src(s, "README.md 'Tests' and 'Known limitations'; test results from the final run on 2026-10-21.");
notes(s, "To finish: what is done, what is limited, and what is next. The full pipeline, the analysis, the dashboard and the tests are implemented and passing. The predictor is only reliable once a design has been measured, the GitHub ingestion is partial, and we can only probe a few memory cells per run. Our next steps are processing the remaining repositories, attempting a genuinely pipelined CPU, and, if we want real timing or area numbers, using a synthesis tool, which the simulator cannot provide. In one sentence: HackTwin turns raw simulator output into checked, comparable evidence about Hack CPU designs. Thank you.",
  "README.md; final test run: 136 passed (8 skipped), integration 7 passed (1 network test skipped).",
  "What would it take to claim a design is really faster?",
  "Its clock period and area, from a synthesis or timing tool, combined with our cycle counts. Our simulator gives only cycles, so we make no speed claim.");

(async () => {
  await pres.writeFile({ fileName: "HackTwin_Review.pptx" });
  await applyTheme("HackTwin_Review.pptx", THEME);
  console.log("ok");
})();
