# DVB Graph

Interactive graph of the Dresden tram and bus network (DVB/VVO), with real-time delay coloring.

## Setup

```bash
pip3 install -r requirements.txt
```

## Scripts

All scripts are in `scripts/` and write output to `data/`.

### Fetch everything and build the graph (first run)

```bash
python3 scripts/fetch_all.py
```

This runs two steps in sequence:
1. Downloads all VVO stations from the open data API → `data/stations.json`
2. Fetches live stop sequences for each tram/bus line → `data/graph.json`, `data/graph.gexf`

Takes about 2–3 minutes.

### Fetch current delays

```bash
python3 scripts/fetch_delays.py
```

Hits the departure monitor API for all 621 Dresden tram/bus stations concurrently (~15 seconds), computes the average delay at each station right now, and:
- Appends a row to `data/delays.db` (SQLite) — timestamp + one column per station
- Overwrites `data/delays.json` with the latest snapshot (used by the visualization)

### Generate the visualization

```bash
python3 scripts/visualize_graph.py
```

Reads `data/graph.json` and `data/delays.json` (if present) and writes `data/graph.html`.

Open `data/graph.html` in a browser. The page has two rows of toggle buttons:
- **Map layout / Physics** — switch between geographic positions and force-directed layout
- **Line colors / Delay** — switch between tram/bus color coding and a green→red delay heatmap

The Delay button is greyed out until `fetch_delays.py` has been run at least once.

### Run individual steps

```bash
python3 scripts/fetch_stations.py   # refresh stations.json only
python3 scripts/build_graph.py      # rebuild graph from existing stations.json
```

## Data files

| File | Description |
|------|-------------|
| `data/stations.json` | All VVO stops with coordinates and line info |
| `data/graph.json` | Graph nodes and edges (station pairs + which lines connect them) |
| `data/graph.gexf` | Same graph in Gephi format |
| `data/delays.json` | Latest delay snapshot (overwritten each run) |
| `data/delays.db` | SQLite database — one row per fetch run, one column per station |
| `data/graph.html` | Interactive visualization |

## Scheduling on Raspberry Pi (collect delays every 30 minutes)

**1. Install dependencies**

```bash
pip3 install -r requirements.txt
```

**2. Find your Python path**

```bash
which python3
```

**3. Add a crontab entry**

```bash
crontab -e
```

Add this line (adjust paths to match where you cloned the project):

```
*/30 * * * * flock -n /tmp/fetch_delays.lock /usr/bin/python3 /home/pi/DVB_graph/scripts/fetch_delays.py >> /home/pi/DVB_graph/data/delays.log 2>&1
```

`flock -n` ensures that if a run is still in progress when the next one is due, the second run is skipped rather than running in parallel (which would cause concurrent writes to the SQLite database).

**4. Check it's working**

```bash
# Confirm cron is firing
grep CRON /var/log/syslog | tail -20

# Watch script output
tail -f /home/pi/DVB_graph/data/delays.log
```

## Data sources

- Station list: [VVO Open Data](https://www.vvo-online.de/open_data/VVO_STOPS.JSON)
- Stop sequences and delays: [VVO WebAPI](https://webapi.vvo-online.de) (departure monitor)
- Network documentation: [`vvo/documentation/`](vvo/documentation/)
