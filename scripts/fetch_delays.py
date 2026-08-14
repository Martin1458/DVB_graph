#!/usr/bin/env python3

# /// script
# dependencies = [
#   "requests<3",
# ]
# ///

"""
Fetches current departure monitor data for every Dresden tram/bus station
and computes delay, cancellation, and occupancy stats right now.

Output: data/delays.json (station-level snapshot, used by visualize_graph.py)
  {
    "fetched_at": "<ISO timestamp>",
    "stations": {
      "<numeric_id>": {
        "avg_delay_min": 2.3,   // null if no real-time data
        "num_departures": 8,
        "num_with_realtime": 5,
        "num_cancelled": 0
      }, ...
    }
  }

Output: data/delays.db, table "observations" (one row per station per line
per direction per fetch run - the full-resolution historical record):
  timestamp, station_id, line_name, direction, mot,
  avg_delay_min, num_departures, num_with_realtime, num_cancelled,
  occupancy_counts (JSON string, e.g. '{"ManySeats": 3, "Unknown": 1}')

At the first run of a new calendar month, the previous month's delays.db is
rotated into data/archive/delays-YYYY-MM.db.gz and a fresh delays.db is
started - keeps the live DB small and gives you discrete monthly files to
copy off to other storage.
"""

import gzip
import json
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
WEBAPI = "https://webapi.vvo-online.de"
HEADERS = {"Content-Type": "application/json;charset=UTF-8"}
MOT_FILTER = ["Tram", "CityBus"]
WORKERS = 20


def parse_ms_date(s):
    """Parse Microsoft /Date(ms+tz)/ format → milliseconds since epoch."""
    if not s:
        return None
    m = re.search(r"/Date\((\d+)[+\-]", s)
    return int(m.group(1)) if m else None


def is_cancelled(dep):
    return bool(dep.get("CancelReasons")) or dep.get("State") == "Cancelled"


def fetch_station_groups(station):
    """Return (numeric_id, list of per line+direction group dicts) for one station."""
    nid = station["numeric_id"]
    try:
        r = requests.post(
            f"{WEBAPI}/dm",
            json={"stopid": nid, "limit": 10, "mot": MOT_FILTER, "format": "json"},
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return nid, None

    if data.get("Status", {}).get("Code") != "Ok":
        return nid, None

    # Bucket departures by (line, direction, mot) so per-line delay patterns
    # aren't averaged away into a single station-wide number.
    buckets = defaultdict(list)
    for dep in data.get("Departures", []):
        key = (dep.get("LineName"), dep.get("Direction"), dep.get("Mot"))
        buckets[key].append(dep)

    groups = []
    for (line_name, direction, mot), deps in buckets.items():
        delays = []
        cancelled = 0
        occupancy = Counter()
        for dep in deps:
            if is_cancelled(dep):
                cancelled += 1
                continue
            sched = parse_ms_date(dep.get("ScheduledTime"))
            real = parse_ms_date(dep.get("RealTime"))
            if sched and real:
                delay_min = (real - sched) / 60000
                delays.append(max(delay_min, 0))  # ignore negative (early arrivals)
            occ = dep.get("Occupancy")
            if occ:
                occupancy[occ] += 1

        groups.append({
            "line_name": line_name,
            "direction": direction,
            "mot": mot,
            "avg_delay_min": round(sum(delays) / len(delays), 2) if delays else None,
            "num_departures": len(deps),
            "num_with_realtime": len(delays),
            "num_cancelled": cancelled,
            "occupancy_counts": dict(occupancy),
        })

    return nid, groups


def rotate_if_needed(db_path, archive_dir, current_month):
    """If db_path holds data from an earlier month, gzip it into archive_dir
    as delays-YYYY-MM.db.gz and remove it, so a fresh db starts for the new
    month. No-op if db_path doesn't exist, is empty, or is already current."""
    if not os.path.exists(db_path):
        return

    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT timestamp FROM observations LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        row = None  # table doesn't exist yet
    con.close()
    if row is None:
        return

    existing_month = row[0][:7]  # "YYYY-MM"
    if existing_month == current_month:
        return

    os.makedirs(archive_dir, exist_ok=True)
    archived_gz = os.path.join(archive_dir, f"delays-{existing_month}.db.gz")
    with open(db_path, "rb") as f_in, gzip.open(archived_gz, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(db_path)
    print(f"Rotated {existing_month} data -> {archived_gz}", file=sys.stderr)


def ensure_db(db_path):
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            station_id TEXT NOT NULL,
            line_name TEXT,
            direction TEXT,
            mot TEXT,
            avg_delay_min REAL,
            num_departures INTEGER,
            num_with_realtime INTEGER,
            num_cancelled INTEGER,
            occupancy_counts TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_observations_timestamp ON observations (timestamp)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_observations_station ON observations (station_id)")
    con.commit()
    return con


def insert_rows(con, timestamp, results):
    """Insert one row per (station, line, direction) group."""
    rows = [
        (
            timestamp,
            nid,
            g["line_name"],
            g["direction"],
            g["mot"],
            g["avg_delay_min"],
            g["num_departures"],
            g["num_with_realtime"],
            g["num_cancelled"],
            json.dumps(g["occupancy_counts"], ensure_ascii=False),
        )
        for nid, groups in results.items()
        if groups
        for g in groups
    ]
    con.executemany(
        """INSERT INTO observations
           (timestamp, station_id, line_name, direction, mot, avg_delay_min,
            num_departures, num_with_realtime, num_cancelled, occupancy_counts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    con.commit()
    return len(rows)


def station_summary(groups):
    """Collapse a station's per-line groups back into one aggregate (for delays.json)."""
    if not groups:
        return None
    delay_sum = 0.0
    delay_n = 0
    num_departures = 0
    num_cancelled = 0
    for g in groups:
        num_departures += g["num_departures"]
        num_cancelled += g["num_cancelled"]
        if g["avg_delay_min"] is not None:
            delay_sum += g["avg_delay_min"] * g["num_with_realtime"]
            delay_n += g["num_with_realtime"]
    return {
        "avg_delay_min": round(delay_sum / delay_n, 2) if delay_n else None,
        "num_departures": num_departures,
        "num_with_realtime": delay_n,
        "num_cancelled": num_cancelled,
    }


def main():
    stations_path = os.path.join(DATA_DIR, "stations.json")
    with open(stations_path, encoding="utf-8") as f:
        raw = json.load(f)

    vehicle_filter = {"Straßenbahn", "Stadtbus"}
    stations = [
        s for s in raw["stations"]
        if s["city"] == "Dresden" and any(vt in vehicle_filter for vt in s["lines"])
    ]

    print(f"Fetching delays for {len(stations)} Dresden stations with {WORKERS} workers...", file=sys.stderr)

    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_station_groups, s): s for s in stations}
        for future in as_completed(futures):
            nid, groups = future.result()
            results[nid] = groups
            done += 1
            if done % 50 == 0 or done == len(stations):
                print(f"  {done}/{len(stations)}", file=sys.stderr)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Write full-resolution rows (per station+line+direction) to SQLite
    db_path = os.path.join(DATA_DIR, "delays.db")
    rotate_if_needed(db_path, ARCHIVE_DIR, timestamp[:7])
    con = ensure_db(db_path)
    num_rows = insert_rows(con, timestamp, results)
    con.close()

    # Collapse to per-station aggregates for delays.json (used by visualize_graph.py)
    summaries = {nid: station_summary(groups) for nid, groups in results.items()}
    out = {
        "fetched_at": timestamp,
        "stations": summaries,
    }
    out_path = os.path.join(DATA_DIR, "delays.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    with_data = sum(1 for v in summaries.values() if v and v["avg_delay_min"] is not None)
    delayed = sum(1 for v in summaries.values() if v and v["avg_delay_min"] and v["avg_delay_min"] > 1)
    cancelled = sum(v["num_cancelled"] for v in summaries.values() if v)
    print(f"\nSaved {num_rows} rows to {db_path} and snapshot to {out_path}", file=sys.stderr)
    print(f"  Timestamp: {timestamp}", file=sys.stderr)
    print(f"  Stations with real-time data: {with_data}/{len(summaries)}", file=sys.stderr)
    print(f"  Stations with >1 min avg delay: {delayed}", file=sys.stderr)
    print(f"  Cancelled departures: {cancelled}", file=sys.stderr)


if __name__ == "__main__":
    main()
