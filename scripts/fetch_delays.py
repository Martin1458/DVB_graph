#!/usr/bin/env python3

# /// script
# dependencies = [
#   "requests<3",
# ]
# ///

"""
Fetches current departure monitor data for every Dresden tram/bus station
and computes the average delay per station right now.

Output: data/delays.json
  {
    "fetched_at": "<ISO timestamp>",
    "stations": {
      "<numeric_id>": {
        "avg_delay_min": 2.3,   // null if no real-time data
        "num_departures": 8,
        "num_with_realtime": 5
      }, ...
    }
  }
"""

import json
import os
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
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


def fetch_station_delay(station):
    """Return (numeric_id, result_dict) for one station."""
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

    departures = data.get("Departures", [])
    delays = []
    for dep in departures:
        sched = parse_ms_date(dep.get("ScheduledTime"))
        real = parse_ms_date(dep.get("RealTime"))
        if sched and real:
            delay_min = (real - sched) / 60000
            delays.append(max(delay_min, 0))  # ignore negative (early arrivals)

    return nid, {
        "avg_delay_min": round(sum(delays) / len(delays), 2) if delays else None,
        "num_departures": len(departures),
        "num_with_realtime": len(delays),
    }


def ensure_db(db_path, station_ids):
    """Create the delays table if needed, and add any missing station columns."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # Create table with just timestamp if it doesn't exist yet
    cur.execute("CREATE TABLE IF NOT EXISTS delays (timestamp TEXT NOT NULL)")

    # Find which station columns are already present
    cur.execute("PRAGMA table_info(delays)")
    existing = {row[1] for row in cur.fetchall()}

    # Add a column for every station that isn't there yet
    for nid in station_ids:
        if nid not in existing:
            cur.execute(f'ALTER TABLE delays ADD COLUMN "{nid}" REAL')

    con.commit()
    return con


def insert_row(con, timestamp, station_ids, results):
    """Insert one row: timestamp + avg_delay_min per station."""
    cols = ", ".join(f'"{nid}"' for nid in station_ids)
    placeholders = ", ".join("?" * len(station_ids))
    values = [
        results[nid]["avg_delay_min"] if results.get(nid) else None
        for nid in station_ids
    ]
    con.execute(
        f'INSERT INTO delays (timestamp, {cols}) VALUES (?, {placeholders})',
        [timestamp] + values,
    )
    con.commit()


def main():
    stations_path = os.path.join(DATA_DIR, "stations.json")
    with open(stations_path, encoding="utf-8") as f:
        raw = json.load(f)

    vehicle_filter = {"Straßenbahn", "Stadtbus"}
    stations = [
        s for s in raw["stations"]
        if s["city"] == "Dresden" and any(vt in vehicle_filter for vt in s["lines"])
    ]
    station_ids = [s["numeric_id"] for s in stations]

    print(f"Fetching delays for {len(stations)} Dresden stations with {WORKERS} workers...", file=sys.stderr)

    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_station_delay, s): s for s in stations}
        for future in as_completed(futures):
            nid, result = future.result()
            results[nid] = result
            done += 1
            if done % 50 == 0 or done == len(stations):
                print(f"  {done}/{len(stations)}", file=sys.stderr)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Write to SQLite
    db_path = os.path.join(DATA_DIR, "delays.db")
    con = ensure_db(db_path, station_ids)
    insert_row(con, timestamp, station_ids, results)
    con.close()

    # Write latest snapshot as JSON for visualize_graph.py
    out = {
        "fetched_at": timestamp,
        "stations": results,
    }
    out_path = os.path.join(DATA_DIR, "delays.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    with_data = sum(1 for v in results.values() if v and v["avg_delay_min"] is not None)
    delayed = sum(1 for v in results.values() if v and v["avg_delay_min"] and v["avg_delay_min"] > 1)
    print(f"\nSaved row to {db_path} and snapshot to {out_path}", file=sys.stderr)
    print(f"  Timestamp: {timestamp}", file=sys.stderr)
    print(f"  Stations with real-time data: {with_data}/{len(results)}", file=sys.stderr)
    print(f"  Stations with >1 min avg delay: {delayed}", file=sys.stderr)


if __name__ == "__main__":
    main()
