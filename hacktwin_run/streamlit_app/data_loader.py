"""
streamlit_app/data_loader.py

Data Loader module connecting Streamlit to the SQLite TwinStore and formatting
pandas DataFrames for chart visualizations.
"""

from typing import Any, Dict, List, Optional
import pandas as pd
from twin_store import TwinStore


class StreamlitDataLoader:
    """Helper class to query TwinStore and prepare pandas DataFrames for Streamlit views."""

    def __init__(self, db_path: str = "data/hacktwin.db"):
        self.db_path = db_path
        self.store = TwinStore(db_path)

    def close(self):
        self.store.close()

    def get_summary_df(self) -> pd.DataFrame:
        """Fetch design summary leaderboard as a pandas DataFrame."""
        designs = self.store.get_all_designs()
        if not designs:
            return pd.DataFrame()
        return pd.DataFrame(designs)

    def get_runs_df(self, design_id: Optional[str] = None, benchmark_id: Optional[str] = None) -> pd.DataFrame:
        """Fetch simulation runs as a pandas DataFrame."""
        runs = self.store.query_runs(design_id=design_id, benchmark_id=benchmark_id)
        if not runs:
            return pd.DataFrame()
        df = pd.DataFrame(runs)
        return df

    def get_run_trace_df(self, run_id: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Fetch parsed instruction trace, register snapshot, and memory access log DataFrames."""
        telem = self.store.get_run_telemetry(run_id)
        if not telem:
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        inst_df = pd.DataFrame(telem.get("instruction_trace", []))
        if not inst_df.empty:
            inst_df["cycle"] = range(1, len(inst_df) + 1)

        reg_df = pd.DataFrame(telem.get("register_snapshot", []))
        if not reg_df.empty:
            reg_df["cycle"] = range(1, len(reg_df) + 1)

        mem_df = pd.DataFrame(telem.get("memory_access_log", []))
        if not mem_df.empty and "cycle" not in mem_df.columns:
            mem_df["cycle"] = range(1, len(mem_df) + 1)

        return inst_df, reg_df, mem_df

    def get_memory_heatmap_matrix(self, run_id: str) -> pd.DataFrame:
        """Construct a 2D matrix (RAM Address vs Cycle Window) for memory heatmap visualization."""
        telem = self.store.get_run_telemetry(run_id)
        if not telem:
            return pd.DataFrame()

        mem_log = telem.get("memory_access_log", [])
        if not mem_log:
            return pd.DataFrame()

        records = []
        for idx, item in enumerate(mem_log, 1):
            records.append({
                "cycle": idx,
                "address": f"RAM[{item.get('address', 0)}]",
                "write_val": item.get("val", 1)
            })

        df = pd.DataFrame(records)
        pivot = df.pivot_table(index="address", columns="cycle", values="write_val", fill_value=0)
        return pivot
