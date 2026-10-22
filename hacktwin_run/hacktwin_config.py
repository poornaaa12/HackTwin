#!/usr/bin/env python3
"""
hacktwin_config.py

Central path and environment configuration for HackTwin. Nothing here is
machine-specific: every location is derived from this file's directory or
from an environment variable.

Environment variables
  HACKTWIN_DB               SQLite twin store (default: data/hacktwin.db)
  HACKTWIN_N2T_DIR          nand2tetris distribution root (default: ../nand2tetris)
  HARDWARE_SIMULATOR_CMD    Hardware Simulator launcher (default: <n2t>/tools/HardwareSimulator.sh|.bat)
  HACKTWIN_SIM_TIMEOUT_S    per-simulation timeout in seconds (default: 120)
  GITHUB_TOKEN              optional token for GitHub API discovery (never logged)
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DESIGNS_DIR = BASE_DIR / "designs"
EXTERNAL_DIR = DATA_DIR / "external"
EXTERNAL_DESIGNS_DIR = EXTERNAL_DIR / "designs"
HARNESS_DIR = BASE_DIR / "harness"
BENCHMARKS_DIR = BASE_DIR / "benchmarks"
REPORTS_DIR = BASE_DIR / "reports"
MODELS_DIR = DATA_DIR / "models"


def n2t_dir() -> Path:
    return Path(os.environ.get("HACKTWIN_N2T_DIR", BASE_DIR.parent / "nand2tetris")).expanduser().resolve()


def builtin_chips_dir() -> Path:
    return n2t_dir() / "tools" / "builtInChips"


def db_path() -> Path:
    return Path(os.environ.get("HACKTWIN_DB", DATA_DIR / "hacktwin.db")).expanduser().resolve()


def simulator_cmd() -> str:
    """Resolved Hardware Simulator launcher (absolute path when path-like)."""
    configured = os.environ.get("HARDWARE_SIMULATOR_CMD")
    if configured:
        if "/" in configured or "\\" in configured or Path(configured).exists():
            return str(Path(configured).expanduser().resolve())
        return configured
    launcher = "HardwareSimulator.bat" if os.name == "nt" else "HardwareSimulator.sh"
    candidate = n2t_dir() / "tools" / launcher
    return str(candidate) if candidate.exists() else "HardwareSimulator"


def simulator_timeout_s() -> float:
    try:
        return float(os.environ.get("HACKTWIN_SIM_TIMEOUT_S", "120"))
    except ValueError:
        return 120.0
