#!/usr/bin/env python3

# /// script
# dependencies = [
#   "requests<3",
#   "networkx",
# ]
# ///

"""
Builds a station graph for Dresden tram/bus network.

Strategy:
  1. Load Dresden stops from vvo/data/stations.json (nodes).
  2. For each unique tram/bus line, hit the departure monitor to get a live trip.
  3. Use dm/trip to get the ordered stop sequence → edges between consecutive stops.
  4. Save as graph.json (node/edge lists) and graph.gexf (Gephi/networkx format).
"""

import json
import time
import sys
import os
import requests
import networkx as nx
from collections import defaultdict

WEBAPI = "https://webapi.vvo-online.de"
HEADERS = {"Content-Type": "application/json;charset=UTF-8"}

VEHICLE_FILTER = {"Straßenbahn", "Stadtbus"}  # tram + city bus only
MOT_FILTER = ["Tram", "CityBus"]

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_DIR = DATA_DIR
os.makedirs(OUT_DIR, exist_ok=True)


def load_dresden_stations():
    path = os.path.join(DATA_DIR, "stations.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    stations = {}
    lines_to_stops = defaultdict(list)  # line_name → [(numeric_id, station)]

    for s in raw["stations"]:
        if s["city"] != "Dresden":
            continue
        if not any(vt in VEHICLE_FILTER for vt in s["lines"]):
            continue
        stations[s["numeric_id"]] = s
        for vt in VEHICLE_FILTER:
            for line in s["lines"].get(vt, []):
                lines_to_stops[line].append(s)

    print(f"Loaded {len(stations)} Dresden tram/bus stations", file=sys.stderr)
    print(f"Unique lines: {sorted(lines_to_stops.keys())}", file=sys.stderr)
    return stations, lines_to_stops


def dm_get_departure(stop_id, line_name):
    """Hit the departure monitor; return first departure matching line_name."""
    url = f"{WEBAPI}/dm"
    payload = {
        "stopid": stop_id,
        "limit": 20,
        "mot": MOT_FILTER,
        "format": "json",
    }
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  DM error for stop {stop_id}: {e}", file=sys.stderr)
        return None

    if data.get("Status", {}).get("Code") != "Ok":
        return None

    for dep in data.get("Departures", []):
        if dep.get("LineName") == line_name:
            return dep
    return None


def dm_get_trip(trip_id, scheduled_time, stop_id):
    """Fetch ordered stop list for a trip."""
    url = f"{WEBAPI}/dm/trip"
    payload = {
        "tripid": trip_id,
        "time": scheduled_time,
        "stopid": stop_id,
        "format": "json",
    }
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  Trip error: {e}", file=sys.stderr)
        return []

    if data.get("Status", {}).get("Code") != "Ok":
        return []

    return [s["Id"] for s in data.get("Stops", [])]


def fetch_line_sequences(lines_to_stops):
    """
    For each line, get the ordered stop sequence from one direction.
    Returns dict: line_name → list of numeric stop IDs (in order).
    """
    sequences = {}

    for line, stops in sorted(lines_to_stops.items()):
        print(f"Line {line}: trying {len(stops)} stops...", file=sys.stderr)
        found = False

        for stop in stops:
            stop_id = stop["numeric_id"]
            dep = dm_get_departure(stop_id, line)
            if dep is None:
                continue

            trip_id = dep.get("Id")
            sched_time = dep.get("ScheduledTime", dep.get("RealTime", ""))
            if not trip_id or not sched_time:
                continue

            seq = dm_get_trip(trip_id, sched_time, stop_id)
            if len(seq) >= 2:
                sequences[line] = seq
                print(f"  → {len(seq)} stops: {seq[0]} … {seq[-1]}", file=sys.stderr)
                found = True
                break

            time.sleep(0.2)

        if not found:
            print(f"  → could not get sequence for line {line}", file=sys.stderr)

        time.sleep(0.3)

    return sequences


def build_graph(stations, sequences):
    G = nx.MultiGraph()

    # Add all stations as nodes
    for nid, s in stations.items():
        G.add_node(
            nid,
            name=s["name"],
            city=s["city"],
            lat=s["latitude"],
            lon=s["longitude"],
            lines=list({l for vt in VEHICLE_FILTER for l in s["lines"].get(vt, [])}),
            operators=s["operators"],
        )

    # Add edges from stop sequences
    for line, seq in sequences.items():
        for i in range(len(seq) - 1):
            a, b = seq[i], seq[i + 1]
            # Only add edge if both stops are in our Dresden station set
            if a in stations and b in stations:
                G.add_edge(a, b, line=line)

    return G


def save_graph(G, stations, sequences):
    # Save as node/edge JSON (easy to reload)
    graph_data = {
        "nodes": [
            {
                "id": n,
                **{k: v for k, v in G.nodes[n].items()},
            }
            for n in G.nodes
        ],
        "edges": [
            {"source": u, "target": v, "line": d.get("line")}
            for u, v, d in G.edges(data=True)
        ],
        "line_sequences": sequences,
    }

    json_path = os.path.join(OUT_DIR, "graph.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(graph_data, f, ensure_ascii=False, indent=2)
    print(f"Saved graph.json ({len(G.nodes)} nodes, {len(G.edges)} edges)", file=sys.stderr)

    # GEXF needs string-serializable attributes (no lists)
    G_gexf = nx.Graph()
    for n, d in G.nodes(data=True):
        G_gexf.add_node(n, **{k: ("|".join(v) if isinstance(v, list) else v) for k, v in d.items()})
    for u, v, d in G.edges(data=True):
        if G_gexf.has_edge(u, v):
            G_gexf[u][v]["line"] += "|" + d.get("line", "")
        else:
            G_gexf.add_edge(u, v, **d)
    gexf_path = os.path.join(OUT_DIR, "graph.gexf")
    nx.write_gexf(G_gexf, gexf_path)
    print(f"Saved graph.gexf", file=sys.stderr)

    return json_path


def main():
    stations, lines_to_stops = load_dresden_stations()
    sequences = fetch_line_sequences(lines_to_stops)
    G = build_graph(stations, sequences)
    save_graph(G, stations, sequences)

    print(f"\nGraph summary:", file=sys.stderr)
    print(f"  Nodes: {G.number_of_nodes()}", file=sys.stderr)
    print(f"  Edges: {G.number_of_edges()}", file=sys.stderr)
    print(f"  Lines with sequences: {len(sequences)}", file=sys.stderr)
    cc = list(nx.connected_components(G))
    print(f"  Connected components: {len(cc)}", file=sys.stderr)


if __name__ == "__main__":
    main()
