# HackTwin — Hack CPU digital twin (EOC-2)

The project lives in [`hacktwin_run/`](hacktwin_run/README.md). Start there:

```sh
cd hacktwin_run && python3 app.py --port 8080
```

- `hacktwin_run/README.md` — setup, architecture, workflow, results, tests, limitations
- `hacktwin_run/reports/EXPERIMENT_REPORT.md` — generated experiment report
- `hacktwin_run/docs/AUDIT.md` — audit of the original implementation and fixes
- `nand2tetris/` — the nand2tetris software suite (Hardware Simulator, Assembler) and course files

The text below is the original Phase 1-4 README, kept for history. Its claim
that comparisons were meaningful only after a second variant existed was right;
its cycle counts were `.tst` row counts (see the audit).

---

# HackTwin

A digital twin for Hack CPU/HDL designs: runs benchmark programs through
one or more Hack computer implementations via the nand2tetris Hardware
Simulator, collects per-run telemetry (cycles, register/memory state,
resolved program output), and normalizes it into a schema meant to feed a
versioned twin store, a bottleneck/root-cause analysis engine, a
design-comparison ranker, a performance predictor, and a dashboard.

## Status: Phase 4 complete (Comparison Engine & Design Ranker)

| Phase | Status |
|---|---|
| 0 — Scoping | Done informally: benchmark set = Add, Max, Rect (from the official nand2tetris Project 5 suite); storage format = flat JSON/JSONL → SQLite |
| **1 — Simulator integration & data extraction** | **Done and verified end-to-end on real hardware runs** |
| **2 — Digital Twin Store** | **Done — SQLite store with schema, JSONL loader, lineage tracking, and query CLI** |
| **3 — Bottleneck / root-cause analysis engine** | **Done — Hotspot detection, loop analysis, register churn rates, RAM write bottlenecks, diagnostic attribution** |
| **4 — Comparison engine & ranker** | **Done — Comparative cycle/telemetry delta analysis, composite efficiency scoring & ranker CLI** |
| 5 — Performance predictor | Not started |
| 6 — Recommendation generator | Not started |
| 7 — Dashboard / UI | Not started |
| 8 — Evaluation case study | Not started |

Phase 1 has been run against real, working `CPU.hdl`/`ALU.hdl`/`Memory.hdl`/
`Computer.hdl` on Windows, using the official `ComputerAdd.tst`,
`ComputerMax.tst`, and `ComputerRect.tst` scripts from nand2tetris Project 5,
with `--repeat 2` determinism checks passing and `program_output` correctly
resolved for Add (`5`) and Max (`23456`).

## What's actually in this repo

```
hacktwin_run/
├── run_simulator_batch.py       # Phase 1: drives the simulator headlessly
├── parse_simulator_output.py    # Phase 1: normalizes raw traces to JSON (populated PC/Reg traces)
├── twin_store.py                # Phase 2: SQLite-backed digital twin store module
├── load_to_store.py             # Phase 2: CLI — load data and query the store
├── test_twin_store.py           # Phase 2: unit tests
├── bottleneck_analysis.py      # Phase 3: Bottleneck & root-cause analysis engine
├── test_bottleneck_analysis.py # Phase 3: unit tests
├── comparison_engine.py         # Phase 4: Design comparison & ranking engine
├── test_comparison_engine.py   # Phase 4: unit tests
├── designs/
│   ├── 1/    ALU.hdl, CPU.hdl, Memory.hdl, Computer.hdl
│   └── 2/    ALU.hdl, CPU.hdl, Memory.hdl, Computer.hdl
├── benchmarks/
│   ├── Add.hack,  ComputerAdd.tst,  ComputerAdd.cmp,  ComputerAdd.result.json
│   ├── Max.hack,  ComputerMax.tst,  ComputerMax.cmp,  ComputerMax.result.json
│   └── Rect.hack, ComputerRect.tst, ComputerRect.cmp   (no .result.json -- see Known Limitations)
└── data/
    ├── hacktwin.db                              # Phase 2: SQLite twin store
    ├── raw_runs/<design>__<benchmark>__r<n>/     # per-run .out + run_meta.json
    │   └── manifest.json                        # aggregate of all runs
    └── normalized_runs.jsonl                    # Phase 1 output → Phase 2 input
```

> **Known issue:** `designs/1/` and `designs/2/` currently have identical
> `hdl_source_hash` values -- they're byte-for-byte the same design. The
> pipeline and twin store work correctly with them, but Phase 4
> (comparison/ranking) and the Phase 8 case study need `2` to be an actual
> different variant (pipelined, different ALU strategy, etc.) before those
> phases produce meaningful results. Resolve this before starting Phase 4,
> or at least before generating any comparison output.

## Setup

1. **Java** must be installed (`java -version` to check).
2. **nand2tetris Software Suite** — desktop package (not the browser IDE,
   which can't be scripted), from `nand2tetris.org/software`. Extract
   anywhere; you need the `tools/` folder.
3. Point the pipeline at the simulator (resets per terminal session):
   ```powershell
   $env:HARDWARE_SIMULATOR_CMD = "C:\path\to\nand2tetris\tools\HardwareSimulator.bat"
   ```

## Running it

```powershell
cd hacktwin_run
python run_simulator_batch.py --repeat 2
python parse_simulator_output.py
```

`--repeat 2` runs every design×benchmark pair twice, so you can diff `r0`
vs `r1` in the normalized output to catch nondeterminism.

### Checking the output

```powershell
# Any run that failed to compare cleanly, or hit an error:
Get-Content data\raw_runs\manifest.json | python -m json.tool | Select-String "comparison_ok.*false|error"

# A full normalized record:
Get-Content data\normalized_runs.jsonl | Select-Object -First 1 | python -m json.tool
```

Watch the `[SUMMARY]` line printed at the end of the parse step -- it
tells you how many runs have `program_output: null` because they're
missing a `<benchmark_id>.result.json` spec.

## Data schema (per run, in `normalized_runs.jsonl`)

```
design_id        -- folder name under designs/
run_id            -- "<design_id>__<benchmark_id>__r<repeat_index>"
channels          -- one entry per .out file the .tst produced, each with
                     its own cycle_count / instruction_trace /
                     register_snapshot / memory_access_log / final_row
program_output    -- resolved per <benchmark_id>.result.json, or null with
                     a warning if no spec exists
metadata          -- hdl_source_hash, benchmark_id, returncode, elapsed_s
cycle_count, instruction_trace, register_snapshot, memory_access_log
                  -- convenience mirror of the single channel, ONLY
                     populated when a run has exactly one .out file
```

### `<benchmark_id>.result.json` spec format

Declares what "the answer" is for a benchmark, since that's not something
that can be guessed from a raw trace:
```json
{"output_file": "ComputerAdd.out", "memory_address": 0}
```
or
```json
{"output_file": "ComputerAdd.out", "field": "outM"}
```
`memory_address` resolution checks direct RAM-cell probe columns first
(e.g. `RAM16K[0]`, as the official nand2tetris `.tst` scripts produce),
falling back to reconstructing writes from a `writeM`/`addressM` bus trace
for scripts that probe the bus instead of a specific cell.

## Known limitations (real, not yet fixed)

1. **`instruction_trace` and `register_snapshot` are fully populated.** Field-matching in `build_channel()` was extended to recognize `PC[]`, `ARegister`, `DRegister`, and RAM cell state changes produced by official nand2tetris `.tst` scripts.
2. **`Rect` has no `.result.json`.** It draws to screen memory rather than
   producing one scalar result, so there's no honest single value to point
   at. `cycle_count` and `memory_access_log` still capture correctly for
   it; only `program_output` is intentionally left null.
3. **`designs/1/` and `designs/2/` are identical** (see box above) --
   pipeline-valid, comparison-meaningless until a real second variant
   exists.
4. **Multi-channel runs** (a `.tst` with more than one `output-file`
   directive) are supported without collision, but the convenience
   top-level mirror fields are left empty/0 in that case -- consumers must
   read `record["channels"]` directly rather than the flat fields.

## Bugs found and fixed during Phase 1 development

For the record, since these cost real debugging time and matter if this
pipeline gets extended:
- Binary-vs-decimal field misdetection in the parser (guessed from string
  content instead of field name) -- fixed.
- Cycle count double-counted tick+tock half-cycles as full cycles -- fixed.
- Multi-`.out`-file runs silently collided/overwrote each other -- fixed
  via per-channel storage.
- `program_output` originally dumped the entire last raw row instead of a
  declared result -- replaced with explicit `.result.json` specs.
- `run_simulator_batch.py` invoked the simulator with a `-script` flag that
  doesn't exist in the real tool -- fixed to match actual batch syntax.
- Windows `subprocess.run()` doesn't reliably execute `.bat` files without
  going through `cmd.exe` -- fixed by routing `.bat`/`.cmd` calls through
  `cmd /c`.
- Relative scratch-directory path was resolved twice (once by cwd, once as
  an argument), doubling the path and causing false "file not found"
  errors -- fixed by resolving to an absolute path upfront.
- `.hack`/`.cmp` files sitting next to a `.tst` weren't copied into the
  sandboxed run directory, breaking any `ROM32K load X.hack` directive --
  fixed.
- `Computer.hdl`/`Memory.hdl` are not built-in chips (only their internal
  parts like `RAM16K`/`Screen`/`Keyboard` are) -- they must be supplied per
  design, same as `CPU.hdl`/`ALU.hdl`.
- `memory_address` result resolution only checked a reconstructed
  `writeM`/`addressM` bus trace, which the official `.tst` scripts don't
  expose -- fixed to check direct `RAM16K[N]`-style columns first.
- `run_id` was attached to the in-memory manifest after `run_meta.json` was
  already written to disk, so individual run files never had it -- fixed
  by passing `run_id` into `run_one()` before the file is written.

## Phase 2: Digital Twin Store (SQLite)

The twin store persists normalized run data in SQLite (`data/hacktwin.db`)
with a 3-table schema (`designs`, `runs`, `channels`) and a `run_summary`
convenience view. Design-revision lineage is tracked via a
`parent_design_id` chain in the `designs` table.

### Usage

```powershell
cd hacktwin_run

# Load (or rebuild) the store from normalized_runs.jsonl
python load_to_store.py

# Query: run summary table
python load_to_store.py --query summary

# Query: filter by design and/or benchmark
python load_to_store.py --query summary --design 1 --benchmark ComputerAdd

# Query: design ancestry chain
python load_to_store.py --query lineage --design 2

# Query: side-by-side comparison of two designs
python load_to_store.py --query compare --designs 1,2
```

### Programmatic access

```python
from twin_store import TwinStore

store = TwinStore("data/hacktwin.db")
for run in store.query_runs(benchmark_id="ComputerAdd"):
    print(run["run_id"], run["cycle_count"], run["program_output"])
print(store.get_lineage("2"))            # ["2", "1"] if 1 is parent
print(store.compare_designs("1", "2"))   # per-benchmark comparison
store.close()
```

## Phase 3: Bottleneck & Root-Cause Analysis Engine

The bottleneck engine (`bottleneck_analysis.py`) analyzes execution traces stored in the twin database to identify PC execution hotspots, loop iterations, register churn rates, and memory access hotspots.

### Usage

```powershell
cd hacktwin_run

# Analyze a specific simulation run
python bottleneck_analysis.py --run-id 1__ComputerAdd__r0

# Analyze all runs for a given design and benchmark
python bottleneck_analysis.py --design 1 --benchmark ComputerMax

# Adjust top hotspot limit
python bottleneck_analysis.py --run-id 1__ComputerRect__r0 --top-hotspots 10

# Output analysis as JSON
python bottleneck_analysis.py --run-id 1__ComputerAdd__r0 --json
```

---

## Phase 4: Design Comparison Engine & Ranker

The comparison engine (`comparison_engine.py`) performs side-by-side comparative diagnostics across CPU design implementations, evaluating cycle speedups, register churn efficiency, program output agreement, and ranking designs.

### Usage

```powershell
cd hacktwin_run

# Side-by-side comparison of two designs
python comparison_engine.py --designs 1,2

# Rank all designs stored in the twin database
python comparison_engine.py --rank

# Output ranking or comparison as JSON
python comparison_engine.py --designs 1,2 --json
```

---

## Next step: Phase 5 (Performance Predictor)

Build an ML/analytical predictor that projects execution cycle counts and performance scaling for unseen benchmarks or scaled HDL parameters based on historical twin store telemetry.
