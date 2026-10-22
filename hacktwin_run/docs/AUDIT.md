# HackTwin audit (2026-10-21)

What the original repository contained, what was wrong with it, and what was
done. The code was treated as the source of truth; claims in the earlier
README and operating guide were checked against it.

## Starting state

* Python pipeline: `run_simulator_batch.py` → `parse_simulator_output.py` →
  `load_to_store.py`/`twin_store.py` (SQLite) → `bottleneck_analysis.py`,
  `comparison_engine.py`, `recommendation_generator.py`, `mutation_engine.py`,
  `performance_predictor.py` (heuristic), `ml_performance_predictor.py`
  (ExtraTrees on 103 GitHub-program labels), `evaluation_study.py`.
* UI: `dashboard_server.py` (single-threaded `http.server`) + a small static
  dashboard; an unused Streamlit prototype.
* Designs `1`, `2`, `3`; benchmarks: the three official Project 5 Computer
  tests (Add, Max, Rect). 18 stored runs. 57 unit tests, all passing.

## Defects found and fixed

| # | Finding | Consequence | Fix |
|---|---|---|---|
| 1 | The "cycle count" of every run was the number of rows printed by the official `.tst`, which fixes the length with `repeat N`. | Every design always tied; comparisons and rankings could not detect any real difference. | New run-to-halt harness (`harness/TwinComputer.hdl`, `sim_runner.py`) that measures cycles until the program reaches its halt address, with a design-independent budget. Legacy runs are labelled `cycle_metric = tst_rows` and never compared. |
| 2 | Design `3` differs from design `2` only in comments and whitespace (same token stream, same netlist). Design `2` vs `1` is a one-gate rewrite of the jump condition. | "Three designs" were effectively two netlists with identical behaviour. | `hdl_inspector.py` reports source, normalized and netlist hashes; the UI and comparisons state the difference level. Five genuinely different designs were added. |
| 3 | Three inconsistent design-hash functions (bytes only truncated to 16 hex; bytes only full; names + bytes). | The same revision had different identities in different tables. | One function (`hdl_inspector.source_hash`) used everywhere. |
| 4 | Failed simulator runs were stored like successful ones (no status); `cycle_count` became 0. | Failures could be averaged into comparisons as "0 cycles". | Runs carry `status` / `correctness` / `failure_reason`; only valid, correct runs are compared; legacy rows without a return code are migrated as failed. |
| 5 | `canonical_rows_by_cycle` keyed rows by the formatted time column, which wraps (`%S1.4.1` prints 1000 as `000`). | Traces longer than 999 cycles silently lost rows. | Order-based collapsing; regression test with 1,200 rows. |
| 6 | `load_jsonl(clear_first=True)` deleted all designs and lineage, then relied on later code to re-register them; not transactional. | Metadata loss; partially loaded stores on malformed input. | Replaces only legacy runs, keeps design metadata, single transaction with rollback. |
| 7 | `compare_designs` grouped without aggregating `program_output`; ranking sorted by total cycles + churn with no tie handling and ranked unverified designs. | Arbitrary ordering of tied, unverified designs. | `rank_program_designs`: one documented metric, ties share a rank, unranked designs listed with reasons. |
| 8 | Hotspot and loop statistics included the terminal halt loop. | For short programs the halt loop dominated every "hotspot". | Analysis window ends at the halt cycle. |
| 9 | Single-threaded HTTP server; no way to run a simulation from the UI. | UI blocked on slow requests; no workbench. | `api_server.py`: threaded server, store pool, persisted background job queue. |
| 10 | `Memory.tst` hangs in batch mode (waits for keyboard input). | Any automation that runs it never finishes. | Excluded from conformance with the reason recorded. |

Defects found while building the new system (also fixed, with tests):
the simulator's 20-column output limit (48 runs failed and are kept as
history); header names are truncated to column width (parse by position);
HDL wire names may contain `-` and `.`, and text before `CHIP` is ignored by
the simulator (the parser now matches; candidates only HackTwin rejects are
re-checked with the simulator); re-ingesting a repository marked its own
designs as duplicates of themselves; parallel fetching made duplicate
attribution depend on network timing; CPUs without the `ARegister`/
`DRegister`/`PC` built-ins cannot be probed by the official tests (now
"not applicable", not "failed"); ridge regression on a log target exploded
when extrapolating.

## Honest negative results

* Combinational rewrites (`2`, `alu-xor-negate`, `branch-table`) change the
  netlist but not a single cycle count, as expected for a single-cycle CPU.
* All 208 correct GitHub implementations have exactly the baseline's timing.
* A post-increment ISA extension was analysed by hand and dropped before any HDL was written: re-coding the ArrayFill loop with it gave 16 instructions per element instead of 15, because A is the only address register. This is an analysis, not a measurement.
* Static-feature ML predictors are weak (MAE 400 cycles vs 490 for the median);
  the useful predictor is the ISA reference model with per-design CPI calibration.
