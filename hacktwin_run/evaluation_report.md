# HackTwin Evaluation and Evidence Report

This report summarizes the artifacts currently in the project. Cycle counts are simulator clock counts, not physical delay or area. It distinguishes simulator measurements from ML estimates.

## Project data

- Stored designs: **3**
- Stored simulation runs: **18**
- Trace channels: **18**

## Design ranking

Ranking aggregates the stored cycle counts for each benchmark; differences require matching inputs and verified outputs to support a correctness claim.

| Rank | Design | Mean cycles total | Benchmarks | HDL hash |
|---:|---|---:|---:|---|
| 1 | 1 | 104.0 | 3 | `6275e41f5e5c` |
| 2 | 2 | 104.0 | 3 | `4342f702f470` |
| 3 | 3 | 104.0 | 3 | `68f4a25fe667` |

## Matched design comparison

Design 1 vs design 2 has 3 shared benchmarks.

| Benchmark | A cycles | B cycles | Cycle delta (B−A) | Outputs verified | Outputs match |
|---|---:|---:|---:|---|---|
| ComputerAdd | 14.0 | 14.0 | 0.0 | yes | yes |
| ComputerMax | 26.0 | 26.0 | 0.0 | yes | yes |
| ComputerRect | 64.0 | 64.0 | 0.0 | yes | yes |

The comparison reports cycle-count deltas. The Hardware Simulator does not establish clock frequency, critical path, area, or real-time speedup.

## GitHub workload collection and simulator labels

- Pinned, licensed source repositories: **6**
- Unique machine-code programs collected: **50**
- Repeatable simulator labels: **103** across **21** program groups
- Programs excluded from target labeling: **27**; rejected measurements: **12**

## ML cycle predictor validation

- Model: **scikit-learn ExtraTreesRegressor on log1p(cycles)**
- Samples / unique programs: **103 / 21**
- Held-out-program MAE: **391.067 cycles**; median baseline: **518.69 cycles**
- Grouped cross-validation MAE: **439.507 cycles**; median baseline: **513.194 cycles**
- Lower CV MAE than the median baseline: **True**
- The split groups by program hash. Estimates apply to workloads like this dataset and remain estimates until checked by simulation.

## Phase 6 review guidance

### Design 1
- **Review A-register update frequency** — observed: `{"a_register_modifications": 6, "share_pct": 42.86}`. Inspect the A-instructions in the trace and compare against the expected memory and jump behavior before changing HDL. Cycle savings are not estimated; measure before and after.
- **Review repeated writes to one RAM address** — observed: `{"ram_address": 0, "share_pct": 100.0, "write_count": 3}`. Confirm that writes to RAM[0] match the benchmark specification and final expected output. Cycle savings are not estimated; measure before and after.

### Design 2
- **Review A-register update frequency** — observed: `{"a_register_modifications": 6, "share_pct": 42.86}`. Inspect the A-instructions in the trace and compare against the expected memory and jump behavior before changing HDL. Cycle savings are not estimated; measure before and after.
- **Review repeated writes to one RAM address** — observed: `{"ram_address": 0, "share_pct": 100.0, "write_count": 3}`. Confirm that writes to RAM[0] match the benchmark specification and final expected output. Cycle savings are not estimated; measure before and after.

### Design 3
- **Review A-register update frequency** — observed: `{"a_register_modifications": 6, "share_pct": 42.86}`. Inspect the A-instructions in the trace and compare against the expected memory and jump behavior before changing HDL. Cycle savings are not estimated; measure before and after.
- **Review repeated writes to one RAM address** — observed: `{"ram_address": 0, "share_pct": 100.0, "write_count": 3}`. Confirm that writes to RAM[0] match the benchmark specification and final expected output. Cycle savings are not estimated; measure before and after.

## Evaluation scope

This report confirms the evidence available in the local store, workload dataset, and model validation file. It does not claim that architectural HDL edits improve cycle count unless matched, output-verified simulator runs show that result.
