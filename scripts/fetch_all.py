#!/usr/bin/env python3

# /// script
# dependencies = [
#   "requests<3",
#   "networkx",
# ]
# ///

"""
Fetches everything fresh from the APIs and builds the graph.

  1. VVO open data (VVO_STOPS.JSON) → updates vvo/data/stations.json
  2. Departure monitor + trip detail API → stop sequences per line
  3. Saves data/graph.json + data/graph.gexf

Run this whenever you want fully up-to-date data.
"""

import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(script_path, label):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    result = subprocess.run([sys.executable, script_path], cwd=ROOT)
    if result.returncode != 0:
        print(f"\nERROR: {script_path} failed (exit {result.returncode})", file=sys.stderr)
        sys.exit(result.returncode)


if __name__ == "__main__":
    run(
        os.path.join(ROOT, "scripts", "fetch_stations.py"),
        "Step 1/2 — Fetching stations from VVO open data API",
    )
    run(
        os.path.join(ROOT, "scripts", "build_graph.py"),
        "Step 2/2 — Building graph (fetching live stop sequences)",
    )
    print("\nDone. Output files:")
    print("  data/stations.json   — refreshed station list")
    print("  data/graph.json      — graph (nodes + edges)")
    print("  data/graph.gexf      — graph (Gephi format)")
    print("\nRun visualize_graph.py to regenerate the HTML visualization.")
