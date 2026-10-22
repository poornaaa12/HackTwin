#!/usr/bin/env python3
"""
twin_store.py

Digital Twin Store for HackTwin (SQLite).

Persists designs, benchmarks, simulator runs, traces, official conformance
results, experiments, background jobs and the external (GitHub) HDL dataset.

Schema versions are applied by ordered migrations recorded in
``schema_migrations``. A database created by the original Phase 2 code (no
migrations table) is adopted as version 1 and upgraded in place; existing
rows are preserved.

Run semantics (version 2+)
--------------------------
Every run row carries ``mode``:
  official_tst  a nand2tetris .tst script run. Its ``cycle_count`` is the
                number of settled trace rows, which the script fixes with
                ``repeat N`` -- it is identical for every design and is NOT a
                performance measurement (``cycle_metric = 'tst_rows'``).
  program       a run-to-halt program run through HackTwin's instrumented
                harness. ``cycles_to_halt`` is the measured number of clock
                cycles until the program first reaches its terminal loop
                (``cycle_metric = 'cycles_to_halt'``).
and ``status`` (succeeded | failed | invalid) plus ``correctness``
(pass | fail | unknown). Failed runs are stored with their diagnostics and
are never counted as measurements; missing values stay NULL, never 0.

Usage as a library:
    from twin_store import TwinStore
    store = TwinStore("data/hacktwin.db")
    store.load_jsonl("data/normalized_runs.jsonl")
"""

import json
import re
import sqlite3
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Schema v1 (original Phase 2 schema, kept verbatim for adoption/upgrade)
# ---------------------------------------------------------------------------

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS designs (
    design_id        TEXT PRIMARY KEY,
    hdl_source_hash  TEXT NOT NULL,
    parent_design_id TEXT REFERENCES designs(design_id),
    description      TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
    run_id             TEXT PRIMARY KEY,
    design_id          TEXT NOT NULL REFERENCES designs(design_id),
    benchmark_id       TEXT NOT NULL,
    repeat_index       INTEGER NOT NULL,
    cycle_count        INTEGER,
    program_output     TEXT,
    elapsed_s          REAL,
    returncode         INTEGER,
    final_row          TEXT,
    instruction_trace  TEXT,
    register_snapshot  TEXT,
    memory_access_log  TEXT,
    channels           TEXT NOT NULL,
    loaded_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS channels (
    run_id             TEXT NOT NULL REFERENCES runs(run_id),
    channel_name       TEXT NOT NULL,
    cycle_count        INTEGER,
    final_row          TEXT,
    instruction_trace  TEXT,
    register_snapshot  TEXT,
    memory_access_log  TEXT,
    PRIMARY KEY (run_id, channel_name)
);

CREATE TABLE IF NOT EXISTS mutations (
    mutation_id         TEXT PRIMARY KEY,
    parent_design_id    TEXT NOT NULL REFERENCES designs(design_id),
    child_design_id     TEXT NOT NULL REFERENCES designs(design_id),
    mutation_type       TEXT NOT NULL,
    description         TEXT,
    speedup_pct         REAL,
    correctness_status  TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);
"""

_RUN_SUMMARY_VIEW = """
DROP VIEW IF EXISTS run_summary;
CREATE VIEW run_summary AS
SELECT
    r.run_id,
    r.design_id,
    d.hdl_source_hash,
    d.parent_design_id,
    r.benchmark_id,
    r.repeat_index,
    r.cycle_count,
    r.program_output,
    r.elapsed_s,
    r.mode,
    r.status,
    r.correctness,
    r.case_id,
    r.cycles_to_halt,
    r.cycle_metric,
    r.experiment_id,
    r.run_hdl_hash,
    r.started_at
FROM runs r
JOIN designs d ON r.design_id = d.design_id;
"""

_V2_DESIGN_COLUMNS = [
    ("display_name", "TEXT"), ("kind", "TEXT"), ("summary", "TEXT"),
    ("characteristics", "TEXT"), ("limitations", "TEXT"), ("provenance", "TEXT"),
    ("inspection", "TEXT"), ("normalized_hash", "TEXT"), ("netlist_hash", "TEXT"),
    ("design_dir", "TEXT"), ("updated_at", "TEXT"),
]
_V2_RUN_COLUMNS = [
    ("mode", "TEXT DEFAULT 'official_tst'"), ("status", "TEXT"), ("failure_reason", "TEXT"),
    ("correctness", "TEXT"), ("case_id", "TEXT"), ("experiment_id", "TEXT"),
    ("run_hdl_hash", "TEXT"), ("cycle_metric", "TEXT"), ("cycles_to_halt", "INTEGER"),
    ("reference_instructions", "INTEGER"), ("halted", "INTEGER"), ("trace_sha256", "TEXT"),
    ("started_at", "REAL"), ("simulator_cmd", "TEXT"), ("config_hash", "TEXT"), ("details", "TEXT"),
]

_V2_TABLES = """
CREATE TABLE IF NOT EXISTS benchmarks (
    benchmark_id     TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    description      TEXT,
    origin           TEXT,
    suite            TEXT,
    task_id          TEXT,
    program_file     TEXT,
    program_sha256   TEXT,
    instruction_count INTEGER,
    spec             TEXT,
    updated_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS benchmark_cases (
    case_id          TEXT PRIMARY KEY,
    benchmark_id     TEXT NOT NULL REFERENCES benchmarks(benchmark_id) ON DELETE CASCADE,
    inputs           TEXT NOT NULL,
    expected         TEXT,
    max_cycles       INTEGER NOT NULL,
    config_hash      TEXT NOT NULL,
    reference        TEXT,
    updated_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id    TEXT PRIMARY KEY,
    title            TEXT,
    created_at       REAL NOT NULL,
    finished_at      REAL,
    status           TEXT NOT NULL,
    config           TEXT,
    summary          TEXT
);

CREATE TABLE IF NOT EXISTS conformance_results (
    design_id        TEXT NOT NULL REFERENCES designs(design_id) ON DELETE CASCADE,
    test_name        TEXT NOT NULL,
    run_hdl_hash     TEXT NOT NULL,
    test_sha256      TEXT,
    outcome          TEXT NOT NULL,
    message          TEXT,
    elapsed_s        REAL,
    experiment_id    TEXT,
    created_at       REAL,
    PRIMARY KEY (design_id, test_name, run_hdl_hash)
);

CREATE TABLE IF NOT EXISTS traces (
    run_id           TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    columns          TEXT NOT NULL,
    row_count        INTEGER NOT NULL,
    total_rows       INTEGER NOT NULL,
    truncated_after_halt INTEGER NOT NULL DEFAULT 0,
    sha256           TEXT,
    rows_blob        BLOB NOT NULL,
    raw_blob         BLOB
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    params           TEXT,
    status           TEXT NOT NULL,
    created_at       REAL NOT NULL,
    started_at       REAL,
    finished_at      REAL,
    progress         TEXT,
    result           TEXT,
    error            TEXT
);

CREATE TABLE IF NOT EXISTS gh_repositories (
    full_name        TEXT PRIMARY KEY,
    url              TEXT NOT NULL,
    owner            TEXT NOT NULL,
    stars            INTEGER,
    pushed_at        TEXT,
    default_branch   TEXT,
    commit_sha       TEXT,
    license_spdx     TEXT,
    license_evidence TEXT,
    discovered_via   TEXT,
    discovered_at    REAL,
    retrieved_at     REAL,
    stage            TEXT NOT NULL,
    status           TEXT NOT NULL,
    error            TEXT,
    metadata         TEXT
);

CREATE TABLE IF NOT EXISTS gh_candidates (
    candidate_id     TEXT PRIMARY KEY,
    full_name        TEXT NOT NULL REFERENCES gh_repositories(full_name) ON DELETE CASCADE,
    commit_sha       TEXT NOT NULL,
    base_path        TEXT NOT NULL,
    files            TEXT NOT NULL,
    source_hash      TEXT,
    normalized_hash  TEXT,
    netlist_hash     TEXT,
    stage            TEXT NOT NULL,
    status           TEXT NOT NULL,
    errors           TEXT,
    duplicate_of     TEXT,
    design_id        TEXT,
    compatibility    TEXT,
    created_at       REAL,
    updated_at       REAL
);

CREATE TABLE IF NOT EXISTS model_reports (
    model_id         TEXT PRIMARY KEY,
    created_at       REAL NOT NULL,
    report           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_design_bench ON runs(design_id, benchmark_id);
CREATE INDEX IF NOT EXISTS idx_runs_case ON runs(case_id, design_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_experiment ON runs(experiment_id);
CREATE INDEX IF NOT EXISTS idx_cases_benchmark ON benchmark_cases(benchmark_id);
CREATE INDEX IF NOT EXISTS idx_conf_design ON conformance_results(design_id);
CREATE INDEX IF NOT EXISTS idx_candidates_hash ON gh_candidates(source_hash);
CREATE INDEX IF NOT EXISTS idx_candidates_repo ON gh_candidates(full_name);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
"""

SCHEMA_VERSION = 3


def _migrate_v2(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "designs", _V2_DESIGN_COLUMNS)
    _add_columns(conn, "runs", _V2_RUN_COLUMNS)
    _run_script(conn, _V2_TABLES)
    # Classify rows that predate status tracking. A legacy row with no
    # returncode never reached the simulator; it must not look successful.
    conn.execute("UPDATE runs SET mode = 'official_tst' WHERE mode IS NULL")
    conn.execute(
        """UPDATE runs SET
               status = CASE WHEN returncode = 0 THEN 'succeeded' ELSE 'failed' END,
               failure_reason = CASE WHEN returncode = 0 THEN NULL
                                     WHEN returncode IS NULL THEN 'legacy run has no simulator return code'
                                     ELSE 'simulator returned ' || returncode END,
               cycle_metric = 'tst_rows'
           WHERE status IS NULL"""
    )
    conn.execute("UPDATE designs SET kind = COALESCE(kind, 'legacy')")
    _run_script(conn, _RUN_SUMMARY_VIEW)


def _migrate_v3(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


MIGRATIONS: List[Tuple[int, str, Any]] = [
    (1, "Phase 2 schema: designs, runs, channels, mutations", lambda c: _run_script(c, _SCHEMA_V1)),
    (2, "HackTwin 2: run status/mode, design metadata, benchmarks, experiments, "
        "conformance, compressed traces, jobs, GitHub dataset, model reports", _migrate_v2),
    (3, "store_meta key/value table (dataset label, e.g. bundled example data)", _migrate_v3),
]


def _run_script(conn: sqlite3.Connection, script: str) -> None:
    """Execute a multi-statement script inside the caller's transaction.

    sqlite3's executescript() commits first, which would make migrations
    non-atomic; the schema scripts contain no literal semicolons, so a plain
    split is safe.
    """
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _add_columns(conn: sqlite3.Connection, table: str, columns: Sequence[Tuple[str, str]]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPEAT_RE = re.compile(r"__r(\d+)$")


def _extract_repeat_index(run_id: str) -> int:
    """Parse the trailing __r<N> from a run_id like '1__ComputerAdd__r0'."""
    m = _REPEAT_RE.search(run_id)
    return int(m.group(1)) if m else 0


def _dumps(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, sort_keys=True)


def _loads(value: Optional[str], default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def compress_json(value: Any) -> bytes:
    return zlib.compress(json.dumps(value, separators=(",", ":")).encode("utf-8"), 6)


def decompress_json(blob: Optional[bytes]) -> Any:
    return None if blob is None else json.loads(zlib.decompress(blob).decode("utf-8"))


# ---------------------------------------------------------------------------
# TwinStore
# ---------------------------------------------------------------------------

class TwinStore:
    """SQLite-backed digital twin store for Hack CPU simulation runs.

    One instance owns one connection; it is not shared between threads. The
    API server opens one store per worker thread (see ``thread_local_store``).
    """

    def __init__(self, db_path: str = "data/hacktwin.db", check_same_thread: bool = True):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False is only for pooled use, where one thread at a
        # time borrows the store (see api_server.StorePool).
        self.conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=check_same_thread)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._init_schema()

    # -- schema / migrations -------------------------------------------------

    def _init_schema(self):
        conn = self.conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        has_designs = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='designs'"
        ).fetchone()
        if not applied and has_designs:
            # A database written by the original Phase 2 code: adopt as v1.
            conn.execute(
                "INSERT INTO schema_migrations VALUES (1, ?, datetime('now'))",
                ("adopted pre-existing Phase 2 schema",),
            )
            applied.add(1)
            conn.commit()
        for version, description, apply in MIGRATIONS:
            if version in applied:
                continue
            try:
                conn.execute("BEGIN")
                apply(conn)
                conn.execute(
                    "INSERT INTO schema_migrations VALUES (?, ?, datetime('now'))",
                    (version, description),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        has_view = conn.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name='run_summary'").fetchone()
        if not has_view:
            conn.execute("BEGIN")
            _run_script(conn, _RUN_SUMMARY_VIEW)
            conn.commit()

    def schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)

    def migrations(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM schema_migrations ORDER BY version")]

    def close(self):
        self.conn.close()

    def set_meta(self, key: str, value: Any) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO store_meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM store_meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    # -- Design management --------------------------------------------------

    def register_design(
        self,
        design_id: str,
        hdl_source_hash: str,
        parent_design_id: Optional[str] = None,
        description: Optional[str] = None,
    ):
        """Insert or update a design's identity. Existing metadata is kept."""
        self.conn.execute(
            """
            INSERT INTO designs (design_id, hdl_source_hash, parent_design_id, description)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(design_id) DO UPDATE SET
                hdl_source_hash  = excluded.hdl_source_hash,
                parent_design_id = COALESCE(excluded.parent_design_id, designs.parent_design_id),
                description      = COALESCE(excluded.description, designs.description)
            """,
            (design_id, hdl_source_hash, parent_design_id, description),
        )
        self.conn.commit()

    def upsert_design_record(self, record: Dict[str, Any]) -> None:
        """Insert/update a design with its full catalog metadata."""
        inspection = record.get("inspection") or {}
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO designs (design_id, hdl_source_hash, parent_design_id, description,
                    display_name, kind, summary, characteristics, limitations, provenance,
                    inspection, normalized_hash, netlist_hash, design_dir, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(design_id) DO UPDATE SET
                    hdl_source_hash = excluded.hdl_source_hash,
                    parent_design_id = excluded.parent_design_id,
                    description = excluded.description,
                    display_name = excluded.display_name,
                    kind = excluded.kind,
                    summary = excluded.summary,
                    characteristics = excluded.characteristics,
                    limitations = excluded.limitations,
                    provenance = excluded.provenance,
                    inspection = excluded.inspection,
                    normalized_hash = excluded.normalized_hash,
                    netlist_hash = excluded.netlist_hash,
                    design_dir = excluded.design_dir,
                    updated_at = datetime('now')
                """,
                (
                    record["design_id"], inspection.get("source_hash") or record.get("hdl_source_hash", "unknown"),
                    record.get("parent_design_id"), record.get("description") or record.get("summary"),
                    record.get("display_name"), record.get("kind"), record.get("summary"),
                    _dumps(record.get("characteristics")), _dumps(record.get("limitations")),
                    _dumps(record.get("provenance")), _dumps(inspection),
                    inspection.get("normalized_hash"), inspection.get("netlist_hash"),
                    record.get("design_dir"),
                ),
            )

    def get_design(self, design_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM designs WHERE design_id = ?", (design_id,)).fetchone()
        return self._design_row(row) if row else None

    @staticmethod
    def _design_row(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for key in ("characteristics", "limitations", "provenance", "inspection"):
            if key in d:
                d[key] = _loads(d[key])
        return d

    def list_designs(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM designs ORDER BY design_id").fetchall()
        return [self._design_row(r) for r in rows]

    # -- Legacy JSONL loading (official .tst runs) ---------------------------

    def load_jsonl(self, jsonl_path: str, *, clear_first: bool = True):
        """Load normalized_runs.jsonl (official .tst runs) into the store.

        clear_first=True replaces previously loaded *official_tst* runs (so
        repeated loads are idempotent); program runs, design metadata and
        mutation history are untouched. The load is one transaction: a
        malformed record rolls back the whole file.
        """
        path = Path(jsonl_path)
        if not path.exists():
            raise FileNotFoundError(f"JSONL file not found: {path}")

        records = []
        for number, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{number}: invalid JSON ({exc})") from None

        if not records:
            print(f"[warn] {path} is empty — nothing to load.")
            return 0

        conn = self.conn
        try:
            conn.execute("BEGIN")
            if clear_first:
                conn.execute(
                    "DELETE FROM channels WHERE run_id IN (SELECT run_id FROM runs WHERE mode = 'official_tst')"
                )
                conn.execute("DELETE FROM runs WHERE mode = 'official_tst'")

            for rec in records:
                did = rec.get("design_id")
                hsh = rec.get("metadata", {}).get("hdl_source_hash") or "unknown"
                if not did or not rec.get("run_id"):
                    raise ValueError(f"record without design_id/run_id: {str(rec)[:80]}")
                conn.execute(
                    """INSERT INTO designs (design_id, hdl_source_hash, kind) VALUES (?, ?, 'legacy')
                       ON CONFLICT(design_id) DO NOTHING""",
                    (did, hsh),
                )

            for rec in records:
                run_id = rec["run_id"]
                meta = rec.get("metadata", {})
                channels_data = rec.get("channels", {})
                top_final_row = None
                if len(channels_data) == 1:
                    top_final_row = next(iter(channels_data.values())).get("final_row")
                status, reason = _classify_legacy(meta, channels_data)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO runs
                        (run_id, design_id, benchmark_id, repeat_index,
                         cycle_count, program_output, elapsed_s, returncode,
                         final_row, instruction_trace, register_snapshot,
                         memory_access_log, channels, mode, status, failure_reason,
                         correctness, cycle_metric, run_hdl_hash, started_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'official_tst', ?, ?, ?, 'tst_rows', ?, ?)
                    """,
                    (
                        run_id, rec["design_id"], meta.get("benchmark_id"),
                        _extract_repeat_index(run_id),
                        rec.get("cycle_count") if status == "succeeded" else None,
                        json.dumps(rec.get("program_output")),
                        meta.get("elapsed_s"), meta.get("returncode"),
                        json.dumps(top_final_row),
                        json.dumps(rec.get("instruction_trace", [])),
                        json.dumps(rec.get("register_snapshot", [])),
                        json.dumps(rec.get("memory_access_log", [])),
                        json.dumps(channels_data),
                        status, reason,
                        {True: "pass", False: "fail"}.get(meta.get("comparison_ok"), "unknown"),
                        meta.get("hdl_source_hash"), meta.get("started_at"),
                    ),
                )
                for ch_name, ch_data in channels_data.items():
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO channels
                            (run_id, channel_name, cycle_count, final_row,
                             instruction_trace, register_snapshot, memory_access_log)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id, ch_name, ch_data.get("cycle_count"),
                            json.dumps(ch_data.get("final_row")),
                            json.dumps(ch_data.get("instruction_trace", [])),
                            json.dumps(ch_data.get("register_snapshot", [])),
                            json.dumps(ch_data.get("memory_access_log", [])),
                        ),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print(f"Loaded {len(records)} runs into {self.db_path}")
        return len(records)

    # -- Legacy query helpers -------------------------------------------------

    def query_runs(
        self,
        design_id: Optional[str] = None,
        benchmark_id: Optional[str] = None,
        mode: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return run summaries, optionally filtered."""
        sql = "SELECT * FROM run_summary WHERE 1=1"
        params: list = []
        for column, value in (("design_id", design_id), ("benchmark_id", benchmark_id),
                              ("mode", mode), ("status", status)):
            if value is not None:
                sql += f" AND {column} = ?"
                params.append(value)
        sql += " ORDER BY design_id, benchmark_id, case_id, repeat_index"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_channels(self, run_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM channels WHERE run_id = ?", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_lineage(self, design_id: str) -> List[str]:
        """Walk the parent_design_id chain: [design, parent, grandparent, ...]."""
        chain: List[str] = []
        current = design_id
        visited = set()
        while current and current not in visited:
            row = self.conn.execute(
                "SELECT design_id, parent_design_id FROM designs WHERE design_id = ?", (current,)
            ).fetchone()
            if row is None:
                break
            chain.append(row["design_id"])
            visited.add(current)
            current = row["parent_design_id"]
        return chain

    def compare_designs(self, design_id_a: str, design_id_b: str) -> List[Dict[str, Any]]:
        """Legacy side-by-side of official .tst runs (successful runs only).

        Note: for official .tst runs the cycle figure is the script's row
        count, not a performance measurement.
        """
        sql = """
            SELECT benchmark_id, design_id,
                   AVG(cycle_count) AS avg_cycles, MIN(cycle_count) AS min_cycles,
                   MAX(cycle_count) AS max_cycles, AVG(elapsed_s) AS avg_elapsed_s,
                   MIN(program_output) AS program_output
            FROM run_summary
            WHERE design_id IN (?, ?) AND mode = 'official_tst' AND status = 'succeeded'
            GROUP BY benchmark_id, design_id
            ORDER BY benchmark_id, design_id
        """
        by_bench: Dict[str, Dict] = {}
        for r in self.conn.execute(sql, (design_id_a, design_id_b)).fetchall():
            entry = by_bench.setdefault(r["benchmark_id"], {"benchmark_id": r["benchmark_id"]})
            entry[r["design_id"]] = {
                "avg_cycles": r["avg_cycles"],
                "min_cycles": r["min_cycles"],
                "max_cycles": r["max_cycles"],
                "avg_elapsed_s": round(r["avg_elapsed_s"], 6) if r["avg_elapsed_s"] is not None else None,
                "program_output": r["program_output"],
            }
        return list(by_bench.values())

    def get_all_designs(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT design_id, hdl_source_hash, parent_design_id, description, created_at FROM designs ORDER BY design_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_run_telemetry(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Parsed legacy traces for a run (official_tst runs)."""
        run = self.get_run(run_id)
        if not run:
            return None
        return {
            "run_id": run["run_id"],
            "design_id": run["design_id"],
            "benchmark_id": run["benchmark_id"],
            "cycle_count": run["cycle_count"],
            "mode": run.get("mode"),
            "status": run.get("status"),
            "program_output": _loads(run["program_output"]),
            "instruction_trace": _loads(run["instruction_trace"], []),
            "register_snapshot": _loads(run["register_snapshot"], []),
            "memory_access_log": _loads(run["memory_access_log"], []),
            "channels": _loads(run["channels"], {}),
        }

    def record_mutation(
        self,
        mutation_id: str,
        parent_design_id: str,
        child_design_id: str,
        mutation_type: str,
        description: Optional[str] = None,
        speedup_pct: Optional[float] = None,
        correctness_status: Optional[str] = None,
    ):
        self.conn.execute(
            """
            INSERT INTO mutations
                (mutation_id, parent_design_id, child_design_id, mutation_type, description, speedup_pct, correctness_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mutation_id) DO UPDATE SET
                speedup_pct = COALESCE(excluded.speedup_pct, mutations.speedup_pct),
                correctness_status = COALESCE(excluded.correctness_status, mutations.correctness_status),
                description = COALESCE(excluded.description, mutations.description)
            """,
            (mutation_id, parent_design_id, child_design_id, mutation_type, description, speedup_pct, correctness_status),
        )
        self.conn.commit()

    def get_mutations(self, parent_design_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM mutations"
        params = []
        if parent_design_id:
            sql += " WHERE parent_design_id = ?"
            params.append(parent_design_id)
        sql += " ORDER BY created_at DESC"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def design_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM designs").fetchone()[0]

    def run_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def channel_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]

    # -- Benchmarks ---------------------------------------------------------

    def upsert_benchmark(self, bench: Dict[str, Any], cases: Iterable[Dict[str, Any]]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO benchmarks (benchmark_id, title, description, origin, suite, task_id,
                       program_file, program_sha256, instruction_count, spec, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(benchmark_id) DO UPDATE SET
                       title=excluded.title, description=excluded.description, origin=excluded.origin,
                       suite=excluded.suite, task_id=excluded.task_id, program_file=excluded.program_file,
                       program_sha256=excluded.program_sha256, instruction_count=excluded.instruction_count,
                       spec=excluded.spec, updated_at=datetime('now')""",
                (
                    bench["benchmark_id"], bench["title"], bench.get("description"), bench.get("origin"),
                    bench.get("suite"), bench.get("task_id"), bench.get("program"),
                    bench.get("program_sha256"), bench.get("instruction_count"), _dumps(bench),
                ),
            )
            for case in cases:
                self.conn.execute(
                    """INSERT INTO benchmark_cases (case_id, benchmark_id, inputs, expected, max_cycles,
                           config_hash, reference, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                       ON CONFLICT(case_id) DO UPDATE SET
                           benchmark_id=excluded.benchmark_id, inputs=excluded.inputs,
                           expected=excluded.expected, max_cycles=excluded.max_cycles,
                           config_hash=excluded.config_hash, reference=excluded.reference,
                           updated_at=datetime('now')""",
                    (
                        case["case_id"], bench["benchmark_id"], _dumps(case.get("inputs", {})),
                        _dumps(case.get("expected")), case["max_cycles"], case["config_hash"],
                        _dumps(case.get("reference")),
                    ),
                )

    def list_benchmarks(self) -> List[Dict[str, Any]]:
        benches = []
        for row in self.conn.execute("SELECT * FROM benchmarks ORDER BY suite, benchmark_id"):
            b = dict(row)
            b["spec"] = _loads(b["spec"], {})
            b["cases"] = self.list_cases(b["benchmark_id"])
            benches.append(b)
        return benches

    def list_cases(self, benchmark_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql, params = "SELECT * FROM benchmark_cases", []
        if benchmark_id:
            sql += " WHERE benchmark_id = ?"
            params.append(benchmark_id)
        sql += " ORDER BY benchmark_id, case_id"
        out = []
        for row in self.conn.execute(sql, params):
            c = dict(row)
            for key in ("inputs", "expected", "reference"):
                c[key] = _loads(c[key])
            out.append(c)
        return out

    def get_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        rows = [c for c in self.list_cases() if c["case_id"] == case_id]
        return rows[0] if rows else None

    # -- Experiments --------------------------------------------------------

    def create_experiment(self, experiment_id: str, title: str, config: Dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO experiments (experiment_id, title, created_at, status, config) VALUES (?, ?, ?, 'running', ?)",
                (experiment_id, title, time.time(), _dumps(config)),
            )

    def finish_experiment(self, experiment_id: str, status: str, summary: Dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE experiments SET status = ?, finished_at = ?, summary = ? WHERE experiment_id = ?",
                (status, time.time(), _dumps(summary), experiment_id),
            )

    def list_experiments(self, limit: int = 50) -> List[Dict[str, Any]]:
        out = []
        for row in self.conn.execute("SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?", (limit,)):
            e = dict(row)
            e["config"] = _loads(e["config"], {})
            e["summary"] = _loads(e["summary"], {})
            out.append(e)
        return out

    # -- Conformance --------------------------------------------------------

    def record_conformance(self, result: Dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO conformance_results
                   (design_id, test_name, run_hdl_hash, test_sha256, outcome, message, elapsed_s, experiment_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result["design_id"], result["test_name"], result["run_hdl_hash"], result.get("test_sha256"),
                    result["outcome"], result.get("message"), result.get("elapsed_s"),
                    result.get("experiment_id"), result.get("created_at", time.time()),
                ),
            )

    def list_conformance(self, design_id: Optional[str] = None, current_only: bool = True) -> List[Dict[str, Any]]:
        sql = """SELECT c.* FROM conformance_results c JOIN designs d ON d.design_id = c.design_id WHERE 1=1"""
        params: list = []
        if current_only:
            sql += " AND c.run_hdl_hash = d.hdl_source_hash"
        if design_id:
            sql += " AND c.design_id = ?"
            params.append(design_id)
        sql += " ORDER BY c.design_id, c.test_name"
        return [dict(r) for r in self.conn.execute(sql, params)]

    # -- Program runs and traces -------------------------------------------

    def record_program_run(self, run: Dict[str, Any], trace: Optional[Dict[str, Any]] = None) -> None:
        """Insert or replace one run-to-halt run and its trace atomically."""
        with self.conn:
            self.conn.execute("DELETE FROM traces WHERE run_id = ?", (run["run_id"],))
            self.conn.execute(
                """INSERT OR REPLACE INTO runs
                   (run_id, design_id, benchmark_id, repeat_index, cycle_count, program_output,
                    elapsed_s, returncode, final_row, instruction_trace, register_snapshot,
                    memory_access_log, channels, mode, status, failure_reason, correctness, case_id,
                    experiment_id, run_hdl_hash, cycle_metric, cycles_to_halt, reference_instructions,
                    halted, trace_sha256, started_at, simulator_cmd, config_hash, details, loaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, '[]', '[]', '[]', '{}', 'program', ?, ?, ?, ?,
                           ?, ?, 'cycles_to_halt', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    run["run_id"], run["design_id"], run["benchmark_id"], run["repeat_index"],
                    run.get("cycles_to_halt"), _dumps(run.get("program_output")), run.get("elapsed_s"),
                    run.get("returncode"), run["status"], run.get("failure_reason"),
                    run.get("correctness", "unknown"), run["case_id"], run.get("experiment_id"),
                    run.get("run_hdl_hash"), run.get("cycles_to_halt"), run.get("reference_instructions"),
                    None if run.get("halted") is None else int(bool(run["halted"])),
                    run.get("trace_sha256"), run.get("started_at"), run.get("simulator_cmd"),
                    run.get("config_hash"), _dumps(run.get("details")),
                ),
            )
            if trace is not None:
                self.conn.execute(
                    """INSERT INTO traces (run_id, columns, row_count, total_rows, truncated_after_halt,
                           sha256, rows_blob, raw_blob) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run["run_id"], json.dumps(trace["columns"]), len(trace["rows"]),
                        trace.get("total_rows", len(trace["rows"])), int(trace.get("truncated", False)),
                        trace.get("sha256"), compress_json(trace["rows"]),
                        zlib.compress(trace["raw"].encode("utf-8"), 6) if trace.get("raw") else None,
                    ),
                )

    def list_program_runs(
        self,
        design_id: Optional[str] = None,
        benchmark_id: Optional[str] = None,
        case_id: Optional[str] = None,
        status: Optional[str] = None,
        current_revision_only: bool = False,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        sql = """SELECT r.run_id, r.design_id, r.benchmark_id, r.case_id, r.repeat_index, r.status,
                        r.failure_reason, r.correctness, r.cycles_to_halt, r.reference_instructions,
                        r.halted, r.elapsed_s, r.run_hdl_hash, r.trace_sha256, r.experiment_id,
                        r.started_at, r.config_hash, r.details, d.hdl_source_hash AS current_hdl_hash
                 FROM runs r JOIN designs d ON d.design_id = r.design_id
                 WHERE r.mode = 'program'"""
        params: list = []
        for column, value in (("r.design_id", design_id), ("r.benchmark_id", benchmark_id),
                              ("r.case_id", case_id), ("r.status", status)):
            if value is not None:
                sql += f" AND {column} = ?"
                params.append(value)
        if current_revision_only:
            sql += " AND r.run_hdl_hash = d.hdl_source_hash"
        sql += " ORDER BY r.design_id, r.case_id, r.repeat_index"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        out = []
        for row in self.conn.execute(sql, params):
            r = dict(row)
            r["details"] = _loads(r["details"], {})
            r["is_current_revision"] = r["run_hdl_hash"] == r.pop("current_hdl_hash")
            out.append(r)
        return out

    def get_program_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """SELECT r.*, d.hdl_source_hash AS current_hdl_hash FROM runs r
               JOIN designs d ON d.design_id = r.design_id WHERE r.run_id = ?""",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        r = dict(row)
        r["details"] = _loads(r.get("details"), {})
        r["program_output"] = _loads(r.get("program_output"))
        r["is_current_revision"] = r.get("run_hdl_hash") == r.pop("current_hdl_hash")
        for key in ("instruction_trace", "register_snapshot", "memory_access_log", "channels", "final_row"):
            r.pop(key, None)
        meta = self.conn.execute(
            "SELECT columns, row_count, total_rows, truncated_after_halt, sha256, raw_blob IS NOT NULL AS has_raw FROM traces WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        r["trace"] = None if meta is None else {
            "columns": json.loads(meta["columns"]), "row_count": meta["row_count"],
            "total_rows": meta["total_rows"], "truncated_after_halt": bool(meta["truncated_after_halt"]),
            "sha256": meta["sha256"], "has_raw": bool(meta["has_raw"]),
        }
        return r

    def get_trace(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM traces WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        return {
            "columns": json.loads(row["columns"]),
            "rows": decompress_json(row["rows_blob"]),
            "row_count": row["row_count"],
            "total_rows": row["total_rows"],
            "truncated_after_halt": bool(row["truncated_after_halt"]),
            "sha256": row["sha256"],
        }

    def get_raw_trace(self, run_id: str) -> Optional[str]:
        row = self.conn.execute("SELECT raw_blob FROM traces WHERE run_id = ?", (run_id,)).fetchone()
        if not row or row["raw_blob"] is None:
            return None
        return zlib.decompress(row["raw_blob"]).decode("utf-8")

    # -- Jobs -------------------------------------------------------------------

    def create_job(self, job_id: str, kind: str, params: Dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO jobs (job_id, kind, params, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
                (job_id, kind, _dumps(params), time.time()),
            )

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"status", "started_at", "finished_at", "progress", "result", "error"}
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown job field {key}")
            sets.append(f"{key} = ?")
            params.append(_dumps(value) if key in ("progress", "result") else value)
        params.append(job_id)
        with self.conn:
            self.conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", params)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_row(row) if row else None

    def list_jobs(self, limit: int = 30) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._job_row(r) for r in rows]

    @staticmethod
    def _job_row(row: sqlite3.Row) -> Dict[str, Any]:
        j = dict(row)
        for key in ("params", "progress", "result"):
            j[key] = _loads(j[key])
        return j

    def fail_interrupted_jobs(self) -> int:
        """Mark jobs left 'running'/'queued' by a previous process as failed."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE jobs SET status = 'failed', error = 'interrupted: server stopped before the job finished', finished_at = ? WHERE status IN ('running', 'queued')",
                (time.time(),),
            )
        return cur.rowcount

    # -- GitHub dataset -------------------------------------------------------

    def upsert_repository(self, repo: Dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO gh_repositories (full_name, url, owner, stars, pushed_at, default_branch,
                       commit_sha, license_spdx, license_evidence, discovered_via, discovered_at,
                       retrieved_at, stage, status, error, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(full_name) DO UPDATE SET
                       url=excluded.url, owner=excluded.owner, stars=excluded.stars,
                       pushed_at=excluded.pushed_at, default_branch=excluded.default_branch,
                       commit_sha=COALESCE(excluded.commit_sha, gh_repositories.commit_sha),
                       license_spdx=excluded.license_spdx, license_evidence=excluded.license_evidence,
                       discovered_via=COALESCE(gh_repositories.discovered_via, excluded.discovered_via),
                       discovered_at=COALESCE(gh_repositories.discovered_at, excluded.discovered_at),
                       retrieved_at=COALESCE(excluded.retrieved_at, gh_repositories.retrieved_at),
                       stage=excluded.stage, status=excluded.status, error=excluded.error,
                       metadata=excluded.metadata""",
                (
                    repo["full_name"], repo["url"], repo["owner"], repo.get("stars"), repo.get("pushed_at"),
                    repo.get("default_branch"), repo.get("commit_sha"), repo.get("license_spdx"),
                    repo.get("license_evidence"), repo.get("discovered_via"), repo.get("discovered_at"),
                    repo.get("retrieved_at"), repo["stage"], repo["status"], repo.get("error"),
                    _dumps(repo.get("metadata")),
                ),
            )

    def list_repositories(self) -> List[Dict[str, Any]]:
        out = []
        for row in self.conn.execute("SELECT * FROM gh_repositories ORDER BY stars DESC, full_name"):
            r = dict(row)
            r["metadata"] = _loads(r["metadata"], {})
            out.append(r)
        return out

    def get_repository(self, full_name: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM gh_repositories WHERE full_name = ?", (full_name,)).fetchone()
        if not row:
            return None
        r = dict(row)
        r["metadata"] = _loads(r["metadata"], {})
        return r

    def upsert_candidate(self, cand: Dict[str, Any]) -> None:
        now = time.time()
        with self.conn:
            self.conn.execute(
                """INSERT INTO gh_candidates (candidate_id, full_name, commit_sha, base_path, files,
                       source_hash, normalized_hash, netlist_hash, stage, status, errors, duplicate_of,
                       design_id, compatibility, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(candidate_id) DO UPDATE SET
                       files=excluded.files, source_hash=excluded.source_hash,
                       normalized_hash=excluded.normalized_hash, netlist_hash=excluded.netlist_hash,
                       stage=excluded.stage, status=excluded.status, errors=excluded.errors,
                       duplicate_of=excluded.duplicate_of, design_id=excluded.design_id,
                       compatibility=excluded.compatibility, updated_at=excluded.updated_at""",
                (
                    cand["candidate_id"], cand["full_name"], cand["commit_sha"], cand["base_path"],
                    _dumps(cand.get("files", {})), cand.get("source_hash"), cand.get("normalized_hash"),
                    cand.get("netlist_hash"), cand["stage"], cand["status"], _dumps(cand.get("errors", [])),
                    cand.get("duplicate_of"), cand.get("design_id"), _dumps(cand.get("compatibility")),
                    cand.get("created_at", now), now,
                ),
            )

    def list_candidates(self, full_name: Optional[str] = None) -> List[Dict[str, Any]]:
        sql, params = "SELECT * FROM gh_candidates", []
        if full_name:
            sql += " WHERE full_name = ?"
            params.append(full_name)
        sql += " ORDER BY full_name, base_path"
        out = []
        for row in self.conn.execute(sql, params):
            c = dict(row)
            for key in ("files", "errors", "compatibility"):
                c[key] = _loads(c[key])
            out.append(c)
        return out

    # -- Model reports ------------------------------------------------------

    def save_model_report(self, model_id: str, report: Dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO model_reports (model_id, created_at, report) VALUES (?, ?, ?)",
                (model_id, time.time(), _dumps(report)),
            )

    def latest_model_report(self) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM model_reports ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        return {"model_id": row["model_id"], "created_at": row["created_at"], "report": _loads(row["report"], {})}


def _classify_legacy(meta: Dict[str, Any], channels: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Status for an official .tst record from parse_simulator_output.py."""
    if meta.get("error"):
        return "failed", str(meta["error"])
    if meta.get("returncode") not in (0, None):
        return "failed", f"simulator returned {meta.get('returncode')}"
    if meta.get("returncode") is None:
        return "failed", "simulator did not run (no return code recorded)"
    if not channels:
        return "failed", "simulator produced no .out trace"
    if meta.get("comparison_ok") is False:
        return "succeeded", None  # ran to completion; correctness recorded separately as 'fail'
    return "succeeded", None


_thread_stores = threading.local()


def thread_local_store(db_path: str) -> TwinStore:
    """Return a TwinStore bound to the calling thread (one connection per thread)."""
    stores = getattr(_thread_stores, "stores", None)
    if stores is None:
        stores = _thread_stores.stores = {}
    store = stores.get(db_path)
    if store is None:
        store = stores[db_path] = TwinStore(db_path)
    return store
