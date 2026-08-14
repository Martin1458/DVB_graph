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

Hits the departure monitor API for all 621 Dresden tram/bus stations concurrently (~15 seconds), and:
- Appends rows to `data/delays.db` (SQLite, table `observations`) — one row per station **per line per direction**, per run (~2,500 rows/run). Each row has `avg_delay_min`, `num_departures`, `num_with_realtime`, `num_cancelled`, and `occupancy_counts` (JSON, e.g. `{"ManySeats": 3, "Unknown": 1}`).
- Overwrites `data/delays.json` with the latest snapshot, collapsed to one entry per station (used by the visualization)
- At the first run of a new calendar month, rotates the previous month's `delays.db` into `data/archive/delays-YYYY-MM.db.gz` (gzip, ~5.5x smaller) and starts a fresh `delays.db` — see [Archiving](#archiving) below.

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
| `data/delays.json` | Latest delay snapshot, per station (overwritten each run) |
| `data/delays.db` | SQLite database, table `observations` — current month's full history, one row per station+line+direction per fetch run |
| `data/archive/delays-YYYY-MM.db.gz` | Previous months' `delays.db`, rotated out and gzipped — see [Archiving](#archiving) |
| `data/graph.html` | Interactive visualization |

## Scheduling on macOS (collect delays every 5 minutes)

Runs via `launchd` (not cron — macOS's native scheduler, survives reboots and login without extra setup). Triggers every 5 minutes, on the `:00/:05/:10/.../:55` marks.

**1. Create a virtualenv and install dependencies**

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

**2. Create the LaunchAgent**

Save as `~/Library/LaunchAgents/com.dvbgraph.fetchdelays.plist`, replacing `/Users/martin/local_projects/DVB_graph` with your actual project path:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dvbgraph.fetchdelays</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/lockf</string>
        <string>-t</string>
        <string>0</string>
        <string>/Users/martin/local_projects/DVB_graph/data/fetch_delays.lock</string>
        <string>/Users/martin/local_projects/DVB_graph/venv/bin/python3</string>
        <string>/Users/martin/local_projects/DVB_graph/scripts/fetch_delays.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/martin/local_projects/DVB_graph</string>

    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Minute</key><integer>0</integer></dict>
        <dict><key>Minute</key><integer>5</integer></dict>
        <dict><key>Minute</key><integer>10</integer></dict>
        <dict><key>Minute</key><integer>15</integer></dict>
        <dict><key>Minute</key><integer>20</integer></dict>
        <dict><key>Minute</key><integer>25</integer></dict>
        <dict><key>Minute</key><integer>30</integer></dict>
        <dict><key>Minute</key><integer>35</integer></dict>
        <dict><key>Minute</key><integer>40</integer></dict>
        <dict><key>Minute</key><integer>45</integer></dict>
        <dict><key>Minute</key><integer>50</integer></dict>
        <dict><key>Minute</key><integer>55</integer></dict>
    </array>

    <key>StandardOutPath</key>
    <string>/Users/martin/local_projects/DVB_graph/data/delays.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/martin/local_projects/DVB_graph/data/delays.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

`lockf` (built into macOS) plays the same role as `flock -n` did on the Pi: if a run is still in progress when the next one is due, the second run is skipped instead of writing to the SQLite database concurrently.

**3. Load it**

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dvbgraph.fetchdelays.plist
```

**4. Check it's working**

```bash
# Confirm it's loaded
launchctl print gui/$(id -u)/com.dvbgraph.fetchdelays

# Trigger a run immediately (don't wait for the next 5-minute mark)
launchctl kickstart gui/$(id -u)/com.dvbgraph.fetchdelays

# Watch script output
tail -f data/delays.log
```

**Useful commands**

```bash
launchctl bootout gui/$(id -u)/com.dvbgraph.fetchdelays   # stop and unload
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dvbgraph.fetchdelays.plist   # reload after editing the plist
```

The Mac mini needs to be powered on and logged in (or at least not fully shut down) for the job to fire — `launchd` will run missed jobs shortly after wake if the machine was asleep, but not if it was off.

**Data volume:** at 5-minute intervals, `delays.db` grows by roughly 100 MB/day (~3 GB/month) before gzip. See [Archiving](#archiving) for how that gets kept in check.

## Archiving

`fetch_delays.py` rotates `data/delays.db` automatically: the first run of a new calendar month gzips the just-finished month into `data/archive/delays-YYYY-MM.db.gz` (~5.5x smaller — a ~3 GB month compresses to roughly 500 MB) and starts a fresh, empty `delays.db`. No manual step needed for this part — it just happens on schedule.

What's manual is getting those chunks off the Mac mini. Whenever it's convenient:

```bash
# Copy archived chunks to a NAS share (adjust the destination to yours)
rsync -av data/archive/ /Volumes/YourNAS/DVB_graph_archive/

# Then, once you've confirmed they copied successfully, free up local space:
rm data/archive/delays-*.db.gz
```

Each chunk is a self-contained gzipped SQLite file — restore one with `gunzip -k data/archive/delays-2026-08.db.gz` and open it directly with `sqlite3` or any SQLite tool, same `observations` table schema as the live `delays.db`.

`data/archive/` is gitignored (large binary data, not meant to live in the repo).

## Data sources

- Station list: [VVO Open Data](https://www.vvo-online.de/open_data/VVO_STOPS.JSON)
- Stop sequences and delays: [VVO WebAPI](https://webapi.vvo-online.de) (departure monitor)
- Network documentation: [`vvo/documentation/`](vvo/documentation/)
