#!/usr/bin/env python3
"""Print real-time counts from the HackTwin SQLite store."""

import argparse
from pathlib import Path

from twin_store import TwinStore


def main() -> None:
    base_dir = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(base_dir / "data" / "hacktwin.db"))
    args = parser.parse_args()
    store = TwinStore(args.db)
    try:
        print("=== HACKTWIN DIGITAL TWIN STORE SUMMARY ===")
        print(f"Designs Tracked  : {store.design_count()}")
        print(f"Runs Processed   : {store.run_count()}")
        print(f"Channels Logged  : {store.channel_count()}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
