# HackTwin — a digital twin for Hack CPU designs

HackTwin runs Hack CPU implementations written in nand2tetris HDL through the
**official nand2tetris Hardware Simulator**, records what happens on every clock
cycle, checks the results against an independent ISA reference model, and
compares designs on identical workloads. It answers questions such as:

* Does this CPU compute the right answers, and does it pass the official tests?
* How many clock cycles does it need for each workload, and where do they go?
* Is this "new" design actually different hardware, or the same netlist with
  different comments?
* Which change reduced the cycle count — and by how much, measured, on which cases?

Every number in the UI and in `reports/` comes from stored simulator runs.
Missing or failed measurements are shown as missing or failed, never as zero.
Nothing here measures clock period, area or power: the simulator cannot.

---

## Quick start

```sh
cd hacktwin_run
python3 -m pip install -r requirements.txt     # numpy, scikit-learn, joblib
python3 app.py --port 8080                      # then open http://127.0.0.1:8080
```

The repository ships with a populated store (`data/hacktwin.db`). To explore
without Java or the simulator, use the bundled, clearly labelled example:

```sh
HACKTWIN_DB=examples/example_twin.db python3 app.py --port 8080
```

To reproduce everything from an empty database (≈30 s for the core suite):

```sh
python3 demo.py                                  # writes data/demo.db and reports/demo/
HACKTWIN_DB=data/demo.db python3 app.py --port 8080
```

## Requirements and simulator setup

* Python 3.10+ (developed on 3.14), packages in `requirements.txt`.
* Java 8+ (`java -version`) for the Hardware Simulator.
* The nand2tetris desktop software suite. This repository already contains it
  at `../nand2tetris/tools/`. To use another copy, set
  `HACKTWIN_N2T_DIR=/path/to/nand2tetris` or
  `HARDWARE_SIMULATOR_CMD=/path/to/tools/HardwareSimulator.sh` (`.bat` on Windows).
* Optional: `GITHUB_TOKEN` for GitHub discovery (5,000 instead of 60 requests/hour).
  It is passed to curl on stdin and never logged, cached or stored.
* `curl` (used for HTTPS because many Python installs lack a CA bundle).

Other settings: `HACKTWIN_DB` (store path), `HACKTWIN_SIM_TIMEOUT_S` (default 120).

## Guided workflow (UI)

The sidebar follows the analysis workflow:

1. **CPU designs** — every design, its kind (baseline, HackTwin variant, GitHub
   import), provenance, HDL files, source/normalized/netlist hashes, structural
   diff against the baseline, official-test results, benchmark coverage and run history.
2. **Simulation workbench** — choose a design, a benchmark and input cases, set
   repeats and launch real simulations as a background job; inspect status,
   errors, cycles to halt, CPI, expected-vs-actual values, ISA write equivalence
   and repeat consistency for each run.
3. **Trace explorer** — PC over time, execution frequency per address, and a
   windowed cycle table (A, D, PC, instruction, memory bus, RAM probes) with
   filters (writes, reads, jumps, PC, address, text) and the raw simulator output.
   Observed signals and HackTwin-inferred values are labelled separately.
4. **Bottleneck analysis** — where cycles go (instruction classes, hot PCs,
   loops), register and memory activity, and typed findings: *observation*,
   *heuristic*, *confirmed problem*, *trace-quality warning*, each with
   evidence, why it matters, its limits and a next step.
5. **Design comparison** — baseline vs up to three candidates per case, with
   speedup, cycle reduction, CPI, memory and register statistics, repeat
   status, failed/missing cases and an explicit conclusion.
6. **Recommendations** — evidence-linked suggestions; each is marked verified
   or refuted only when the suggested design has actually been simulated on the
   same cases.

Also: **Overview** (headline metrics from the store), **Performance
predictor** (evaluation and what-if predictions), **Datasets & experiments**
(GitHub ingestion funnel, provenance, licences, experiment and job history)
and a **Glossary** (terms also appear as hover explanations throughout).

## Definitions used everywhere

| Term | Definition |
|---|---|
| cycles to halt | Simulated clock cycles until the PC first reaches the program's halt address (its `@k; 0;JMP` terminal loop, or the end of the program). |
| CPI | cycles to halt ÷ instructions retired by the ISA reference emulator on the same inputs. |
| Speedup | baseline cycles ÷ candidate cycles on the same case. > 1 = fewer cycles. |
| Cycle reduction | (baseline − candidate) ÷ baseline × 100 %. |
| Aggregate | Geometric mean of per-case speedups over cases where both designs have valid, correct runs. |
| Valid run | Simulator completed, the program halted within the budget, current HDL revision, current case configuration. |
| Correct run | Valid, and every expected RAM value matched. |
| Ranking | Geometric-mean speedup vs baseline over every core case; designs missing or wrong on any case are not ranked; equal values share a rank. No composite weights. |

Fewer simulated cycles is **not** evidence of faster hardware: clock period is not measured.

## Architecture

```
designs/<id>/*.hdl + design.json      HackTwin designs (baseline + variants)
data/external/designs/<id>/           designs imported from GitHub (generated)
benchmarks/suite.json, programs/      run-to-halt workloads + official .tst references
harness/TwinComputer.hdl              instrumented Computer exposing the CPU bus

hack_isa.py            assembler (byte-identical to the official one on all workloads), disassembler, ISA reference emulator
hdl_inspector.py       HDL parser; source / normalized / netlist (WL graph) hashes; interface, stub, BUILTIN and dependency checks
design_catalog.py      discovers designs, reads manifests, registers them
benchmark_suite.py     loads cases; reference results; design-independent cycle budget; config hashes
sim_runner.py          runs the Hardware Simulator (timeouts, process-group kill); conformance tests; run-to-halt cases
experiment_runner.py   designs × cases × repeats, parallel, idempotent run identities
twin_store.py          SQLite store with versioned migrations (v1 → v3)
bottleneck_analysis.py trace analysis (legacy Phase 3 + run-to-halt analysis)
comparison_engine.py   comparison and ranking (legacy + valid-run-only v2)
recommendation_generator.py  evidence-linked recommendations
cycle_predictor.py     predictors, leave-programs-out evaluation, saved model + report
github_hdl_dataset.py  GitHub discovery, staged ingestion, dedup, licences, validation
hacktwin_service.py    view models for the UI (no HTTP)
api_server.py / app.py threaded JSON API + background job worker + static UI
ui/                    the web application (vanilla ES modules, hand-built SVG charts)
generate_report.py     reports/EXPERIMENT_REPORT.md, experiment_results.json, charts/
demo.py                reproducible end-to-end demo / example dataset builder
```

**Why a vanilla-JS front end:** the project already used a dependency-free
static dashboard served by Python's standard library. Keeping that approach
means one command starts everything, nothing needs a build step or Node, and
the UI works offline. Charts are small hand-written SVG modules with hover
tooltips; all text is inserted with `textContent`, so data from external
repositories cannot inject markup.

### How a run is measured

1. The design's `CPU.hdl` and `Memory.hdl` are wrapped in `harness/TwinComputer.hdl`,
   which exposes `pc`, `instruction`, `writeM`, `addressM`, `outM` and `inM`.
2. A generated `.tst` loads the program, sets the inputs and records every cycle
   (plus `ARegister[]`, `DRegister[]` and up to 11 RAM probes — the simulator
   accepts at most 20 output columns).
3. `cycles_to_halt` is the first row at which `pc` reaches the halt address.
   The budget is `4 × reference instructions + 64` cycles for every design, so
   a slow design is reported as "did not halt", never truncated silently.
4. Results are checked against the specification (probe values, or the last
   observed write for unprobed/screen addresses — labelled as reconstructed),
   and the sequence of memory writes is compared with the ISA reference model
   ("architectural equivalence").
5. Every repeat is stored with the SHA-256 of its simulator output; repeats must match.

Official conformance tests (`CPU.tst`, `CPU-external.tst`, `ALU.tst`,
`ComputerAdd/Max/Rect.tst`) run separately against each design's own chips.
`Memory.tst` is excluded because it waits for interactive keyboard input.

### The store

`twin_store.py` applies ordered migrations recorded in `schema_migrations`. A
database written by the original Phase 2 code is adopted as v1 and upgraded in
place (its 18 historical runs are preserved; legacy runs without a simulator
return code are marked failed, not successful). Version 2 adds run
status/correctness/mode, design metadata, benchmarks and cases, experiments,
conformance results, compressed traces (zlib JSON + raw output), jobs, the
GitHub dataset tables and model reports; version 3 adds `store_meta`.
Multi-statement writes are transactions; reloading the legacy JSONL is atomic
and idempotent. A backup of the pre-upgrade database is in `data/backups/`.

## CPU designs

| Design | What changed | Measured result (core suite, 31 cases) |
|---|---|---|
| `1` baseline | Pre-existing single-cycle Hack computer | reference; CPI 1.000 |
| `2` | Pre-existing jump-condition rewrite | identical cycles on 31/31 |
| `3` | Mutation-engine output | **same netlist as `2`** (comments/whitespace only); identical cycles |
| `alu-xor-negate` | ALU: AND-mask zeroing, XOR conditional inversion, `Zero16` chip | identical cycles on 31/31 |
| `branch-table` | Branch unit as an 8-way table over the jump bits | identical cycles on 31/31 |
| `multicycle-2phase` | Phase register: decode cycle + commit cycle per instruction | exactly 2× cycles (speedup 0.5000); fails cycle-exact official tests by design |
| `jumpfold-8` | Dynamic jump folding with an 8-entry RAM8 table | fewer cycles on 16, ties on 15; geo-mean speedup **1.0324**, total −6.45 % cycles |
| `jumpfold-1` | Same with a single entry | geo-mean 1.0316; loses folds on nested loops (e.g. bubblesort 1191 vs 1179) |

All eight produce correct results on all 31 core cases, all repeats are
byte-identical, and every design's memory writes equal the ISA reference.
The structural variants change the netlist but not the measured metric — an
honest null result. No timing, area or power claim is made for any design.
Each folder's `design.json` records provenance, changes and limitations.

A pipelined Hack CPU was not attempted: it would need hazard handling and a
different instruction-memory interface, and the official tests assume
single-cycle timing. An ISA extension (post-increment addressing) was analysed
by hand and not built: with A as the only address register, the re-coded
ArrayFill loop got longer, not shorter (see `docs/AUDIT.md`; not a measurement).

## Running experiments

```sh
python3 experiment_runner.py                                   # all designs, core suite, 3 repeats, official tests
python3 experiment_runner.py --designs 1,jumpfold-8 --repeats 2
python3 experiment_runner.py --suite workloads --repeats 1     # the 19 GitHub workload programs
python3 experiment_runner.py --cases mult-25-40 --force        # rerun even successful runs
python3 comparison_engine.py --designs 1,2                     # legacy CLI (official .tst runs)
python3 cycle_predictor.py --train                             # rebuild + evaluate the predictor
python3 generate_report.py                                     # reports/
```

Reruns are idempotent: a run's identity is design + case + HDL revision +
case configuration + repeat. Unchanged successful runs are skipped; a changed
HDL file produces new runs and the old ones remain as history.

The original Phase 1 pipeline still works and now records failures correctly:

```sh
python3 run_simulator_batch.py --sim-cmd ../nand2tetris/tools/HardwareSimulator.sh --repeat 2
python3 parse_simulator_output.py && python3 load_to_store.py
```

Its cycle figure is the number of rows the `.tst` prints (`repeat N`), which is
the same for every design; the UI labels it accordingly and never compares it.

## GitHub HDL dataset

```sh
GITHUB_TOKEN=... python3 github_hdl_dataset.py discover --max-pages 1
GITHUB_TOKEN=... python3 github_hdl_dataset.py ingest --max-repos 300
python3 github_hdl_dataset.py validate --repeats 2
python3 github_hdl_dataset.py report
```

Discovery uses repository search (10 queries × 2 sort orders) and code search
for `CPU.hdl`. Ingestion fetches only `.hdl` files by exact commit SHA, caps
each at 256 KiB and each repository at 20 candidate folders, rejects `BUILTIN`
clauses and never runs repository code. Missing `Memory.hdl`/`Computer.hdl`
are supplied from the baseline and recorded as such. Stages: discovered →
retrieved → structurally valid → simulator-compatible → correct → complete.
Candidates that only HackTwin's parser rejects are re-checked with the real
simulator, and the report states which tool rejected them.

Results of the run on 2026-10-21 (see `reports/EXPERIMENT_REPORT.md`):
1,346 repositories discovered; 339 retrieved (1,005 remain pending: bounded
run, resumable); 234 contain a CPU.hdl; 344 candidate folders; 296
structurally valid; 60 duplicates; 236 unique; 213 simulator-compatible;
**208 correct and complete**. All 208 have exactly the baseline's cycle counts
(CPI 1): different netlists, identical timing. Licences: 189 repositories have
none (files cached locally only, not redistributed), 31 MIT, 8 GPL-3.0, others
listed in the report. Imported designs are third-party work and are labelled
"GitHub import" with repository, commit, path and licence evidence.

## Performance predictor

`cycle_predictor.py` predicts measured cycles from features available before
HDL simulation (static program counts and ISA-reference dynamic counts), with
one row per (design, case), repeats collapsed, and evaluation by GroupKFold
grouped by program hash, per design. Macro-average MAE over the 8 HackTwin designs:

| Predictor | MAE (cycles) |
|---|---|
| calibrated ISA model (cycles = a·instructions + b·foldable jumps, per design) | 0.60 |
| ExtraTrees, static + dynamic features | 53.7 |
| ISA instruction count, CPI = 1 assumed | 60.1 |
| ExtraTrees, static features only | 403.5 |
| median baseline | 490.0 |
| ridge, static features only | 1966 |

Interpretation: once a design's CPI has been calibrated from its own runs,
the ISA reference model predicts its cycle counts almost exactly; models that
only see the program text do not, and ridge is worse than the median.
Calibration does **not** transfer to an unseen architecture (leave-one-design-out
shows large errors for `multicycle-2phase`). Five designs share identical
timing, so their rows are not independent evidence. Imported GitHub designs
are excluded from training by default for the same reason. The earlier
static-feature model (`ml_performance_predictor.py`) is kept for reference.

## Tests

```sh
python3 -m unittest discover                               # main suite: 136 tests, ~3 s, no simulator or network
HACKTWIN_INTEGRATION=1 python3 -m unittest test_integration_simulator   # real Hardware Simulator, ~10 s
HACKTWIN_NETWORK=1 python3 -m unittest test_integration_simulator       # adds a live GitHub API query
```

Coverage: assembler vs official output, ISA emulator semantics, HDL parsing and
hash invariants, migrations from a v1 database, atomic loading, trace storage,
comparison/ranking edge cases (ties, failures, missing, stale revisions and
configurations, repeat disagreement), trace analysis (single- and multi-cycle,
folding, confirmed problems), recommendation evidence status, predictor
evaluation, mocked GitHub ingestion (stages, dedup, BUILTIN rejection, rate
limits, retries, token hygiene, idempotence), the HTTP API (including
concurrency and path traversal), and simulator integration with pinned
regression cycle counts. Results on 2026-10-21: 136 passed (8 skipped:
integration/network tests); integration: 7 passed, 1 skipped (network).

## Known limitations

* No clock period, critical path, area or power: only simulated cycles.
* The harness observes RAM through at most 11 probes; other addresses are
  verified from the observed write bus (labelled "reconstructed").
* A and D are observable only for CPUs that instantiate the `ARegister`/`DRegister`
  built-ins; official tests cannot probe CPUs that don't (reported as not applicable).
* Netlist hashes are Weisfeiler-Lehman fingerprints: strong evidence, not proof, of identity.
* GitHub ingestion covered a prioritised quarter of the discovered repositories.
* `streamlit_app/` and `dashboard_server.py` + `dashboard/` are the earlier
  prototypes, kept working (`python3 dashboard_server.py --port 8081`) but superseded.

## Documents

* `reports/EXPERIMENT_REPORT.md` — generated results, comparisons, failures, dataset, predictor.
* `docs/AUDIT.md` — what the audit found in the original code and how each issue was fixed.
* `HACKTWIN_REVIEW_AND_OPERATING_GUIDE.md` — the earlier operating guide (historical).
