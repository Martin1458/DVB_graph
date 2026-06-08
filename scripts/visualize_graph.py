#!/usr/bin/env python3

# /// script
# dependencies = [
#   "networkx",
#   "pyvis",
# ]
# ///

"""
Visualizes the Dresden transit graph as an interactive HTML file using pyvis.

Usage:
  python scripts/visualize_graph.py [graph.json] [output.html]

Defaults:
  graph.json  → data/graph.json
  output.html → data/graph.html
"""

import json
import sys
import os
from pyvis.network import Network

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# Colors per line type (tram lines are numeric 1-13, buses are 60+)
TRAM_COLOR = "#E8342A"   # DVB red
BUS_COLOR = "#3A7FD4"    # blue

LINE_COLORS = {
    # Trams
    "1": "#E8342A", "2": "#E8342A", "3": "#E8342A", "4": "#E8342A",
    "6": "#E8342A", "7": "#E8342A", "8": "#E8342A", "9": "#E8342A",
    "10": "#E8342A", "11": "#E8342A", "12": "#E8342A", "13": "#E8342A",
}


def line_color(line):
    try:
        if int(line) <= 20:
            return TRAM_COLOR
    except ValueError:
        pass
    return BUS_COLOR


def node_color(lines):
    has_tram = any(int(l) <= 20 for l in lines if l.isdigit())
    has_bus = any(int(l) > 20 for l in lines if l.isdigit())
    if has_tram and has_bus:
        return "#9B59B6"  # purple = interchange
    if has_tram:
        return TRAM_COLOR
    return BUS_COLOR


def node_size(lines):
    n = len(lines)
    if n >= 5:
        return 20
    if n >= 3:
        return 14
    return 9


CANVAS_W = 9000
CANVAS_H = 6000


def geo_to_canvas(lat, lon, all_nodes):
    """Map WGS84 lat/lon to canvas pixels. Latitude is flipped (north = up)."""
    lats = [n["lat"] for n in all_nodes if n.get("lat")]
    lons = [n["lon"] for n in all_nodes if n.get("lon")]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    pad = 0.05  # 5% padding on each side
    x = (lon - lon_min) / (lon_max - lon_min)
    y = 1 - (lat - lat_min) / (lat_max - lat_min)  # flip so north is up
    return (
        pad * CANVAS_W + x * CANVAS_W * (1 - 2 * pad),
        pad * CANVAS_H + y * CANVAS_H * (1 - 2 * pad),
    )


def load_delays():
    """Load delays.json if present. Returns dict of numeric_id → avg_delay_min (or None)."""
    path = os.path.join(DATA_DIR, "delays.json")
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    delays = {nid: (v["avg_delay_min"] if v else None) for nid, v in data["stations"].items()}
    return delays, data.get("fetched_at", "")


def inject_toggle(html_path, geo_positions, delays, fetched_at):
    """Post-process pyvis HTML to add layout/delay toggle buttons."""
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    geo_js = json.dumps(geo_positions)
    delay_js = json.dumps(delays) if delays else "null"
    fetched_label = f"Delays as of {fetched_at[:16].replace('T', ' ')} UTC" if fetched_at else "No delay data"

    has_delay = "true" if delays else "false"

    toggle_css = """
    <style>
      #controls {
        position: fixed;
        top: 12px;
        right: 12px;
        z-index: 1000;
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 8px;
      }
      #controls .btn-row { display: flex; gap: 8px; }
      #controls button {
        padding: 8px 16px;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        font-size: 13px;
        font-weight: 600;
        background: #2a2a4a;
        color: #aaa;
        transition: background 0.2s, color 0.2s;
      }
      #controls button.active { background: #E8342A; color: white; }
      #controls button:disabled { opacity: 0.35; cursor: default; }
      #delay-label {
        font-size: 11px;
        color: #888;
        font-family: sans-serif;
      }
      #delay-legend {
        display: none;
        gap: 6px;
        align-items: center;
        font-size: 11px;
        color: #ccc;
        font-family: sans-serif;
      }
      .swatch {
        width: 12px; height: 12px;
        border-radius: 50%;
        display: inline-block;
      }
    </style>
    """

    delay_legend = f"""
    <span id="delay-label">{fetched_label}</span>
    <div id="delay-legend">
      <span class="swatch" style="background:#555"></span>no data&nbsp;
      <span class="swatch" style="background:#27ae60"></span>on time&nbsp;
      <span class="swatch" style="background:#f39c12"></span>~3 min&nbsp;
      <span class="swatch" style="background:#e74c3c"></span>5+ min
    </div>
    """

    delay_btn = (
        'onclick="setColor(\'delay\')"'
        if delays else
        'disabled title="Run fetch_delays.py first"'
    )
    toggle_html = f"""
    <div id="controls">
      <div class="btn-row">
        <button id="btnGeo" class="active" onclick="setLayout('geo')">Map layout</button>
        <button id="btnPhysics" onclick="setLayout('physics')">Physics</button>
      </div>
      <div class="btn-row">
        <button id="btnLines" class="active" onclick="setColor('lines')">Line colors</button>
        <button id="btnDelay" {delay_btn}>Delay</button>
      </div>
      {delay_legend}
    </div>
    """

    toggle_js = f"""
    <script>
      var geoPositions = {geo_js};
      var delayData = {delay_js};
      var hasDelay = {has_delay};
      var nodeOrigColors = {{}};

      // Store original node colors after network is ready
      setTimeout(function() {{
        network.body.data.nodes.get().forEach(function(n) {{
          nodeOrigColors[n.id] = n.color;
        }});
      }}, 500);

      function delayColor(minutes) {{
        if (minutes === null || minutes === undefined) return '#555555';
        if (minutes <= 0.5) return '#27ae60';
        if (minutes >= 5)   return '#e74c3c';
        // interpolate green → yellow → red
        var t = minutes / 5;
        if (t < 0.5) {{
          var r = Math.round(39  + (243 - 39)  * (t * 2));
          var g = Math.round(174 + (156 - 174) * (t * 2));
          var b = Math.round(96  + (18  - 96)  * (t * 2));
        }} else {{
          var t2 = (t - 0.5) * 2;
          var r = Math.round(243 + (231 - 243) * t2);
          var g = Math.round(156 + (76  - 156) * t2);
          var b = Math.round(18  + (60  - 18)  * t2);
        }}
        return 'rgb(' + r + ',' + g + ',' + b + ')';
      }}

      function setLayout(mode) {{
        document.getElementById('btnGeo').className    = mode === 'geo'     ? 'active' : '';
        document.getElementById('btnPhysics').className = mode === 'physics' ? 'active' : '';

        if (mode === 'geo') {{
          network.setOptions({{ physics: {{ enabled: false }} }});
          var updates = Object.keys(geoPositions).map(function(id) {{
            return {{ id: id, x: geoPositions[id].x, y: geoPositions[id].y, physics: false }};
          }});
          network.body.data.nodes.update(updates);
        }} else {{
          var updates = Object.keys(geoPositions).map(function(id) {{
            return {{ id: id, physics: true }};
          }});
          network.body.data.nodes.update(updates);
          network.setOptions({{ physics: {{
            enabled: true,
            barnesHut: {{ springLength: 60, springConstant: 0.02, damping: 0.2 }}
          }} }});
        }}
      }}

      function setColor(mode) {{
        document.getElementById('btnLines').className = mode === 'lines' ? 'active' : '';
        document.getElementById('btnDelay').className = mode === 'delay' ? 'active' : '';
        document.getElementById('delay-legend').style.display = mode === 'delay' ? 'flex' : 'none';

        var updates = network.body.data.nodes.get().map(function(n) {{
          if (mode === 'delay' && hasDelay) {{
            var d = delayData[n.id];
            return {{ id: n.id, color: delayColor(d) }};
          }} else {{
            return {{ id: n.id, color: nodeOrigColors[n.id] || n.color }};
          }}
        }});
        network.body.data.nodes.update(updates);
      }}
    </script>
    """

    html = html.replace("</head>", toggle_css + "</head>", 1)
    html = html.replace("</body>", toggle_html + toggle_js + "</body>", 1)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


def build_pyvis(graph_data, output_path):
    net = Network(
        height="900px",
        width="100%",
        bgcolor="#1a1a2e",
        font_color="white",
        heading="Dresden DVB Transit Graph",
    )

    nodes = {n["id"]: n for n in graph_data["nodes"]}
    all_nodes = list(nodes.values())
    geo_positions = {}

    for nid, n in nodes.items():
        lines = n.get("lines", [])
        label = n["name"]
        title = (
            f"<b>{n['name']}</b><br>"
            f"Lines: {', '.join(sorted(lines))}<br>"
            f"ID: {nid}"
        )
        x, y = geo_to_canvas(n["lat"], n["lon"], all_nodes)
        geo_positions[nid] = {"x": round(x, 1), "y": round(y, 1)}
        net.add_node(
            nid,
            label=label,
            title=title,
            color=node_color(lines),
            size=node_size(lines),
            font={"size": 10, "color": "white"},
            x=x, y=y,
            physics=False,  # pin to geographic position
        )

    # Group parallel edges by station pair, collecting all lines between them
    from collections import defaultdict
    pair_lines = defaultdict(set)
    for e in graph_data["edges"]:
        if e["source"] not in nodes or e["target"] not in nodes:
            continue
        key = (min(e["source"], e["target"]), max(e["source"], e["target"]))
        pair_lines[key].add(e.get("line", "?"))

    for (a, b), lines in pair_lines.items():
        sorted_lines = sorted(lines, key=lambda x: (not x.isdigit(), x))
        # Color: tram red if any tram line, else bus blue, mixed = purple
        has_tram = any(l.isdigit() and int(l) <= 20 for l in lines)
        has_bus = any(l.isdigit() and int(l) > 20 for l in lines)
        if has_tram and has_bus:
            color = "#9B59B6"
        elif has_tram:
            color = TRAM_COLOR
        else:
            color = BUS_COLOR
        net.add_edge(
            a, b,
            title=f"Lines: {', '.join(sorted_lines)}",
            label=", ".join(sorted_lines) if len(sorted_lines) > 1 else "",
            color=color,
            width=1 + len(lines),  # thicker when more lines share the segment
            font={"size": 9, "color": "white"},
        )

    net.set_options("""
    {
      "interaction": {
        "hover": true,
        "tooltipDelay": 100,
        "navigationButtons": true,
        "zoomView": true,
        "dragView": true
      },
      "physics": {
        "enabled": false
      }
    }
    """)

    net.write_html(output_path)
    delays, fetched_at = load_delays()
    inject_toggle(output_path, geo_positions, delays, fetched_at)
    print(f"Saved visualization to {output_path}")


def main():
    graph_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA_DIR, "graph.json")
    output_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DATA_DIR, "graph.html")

    with open(graph_path, encoding="utf-8") as f:
        graph_data = json.load(f)

    print(f"Loaded {len(graph_data['nodes'])} nodes, {len(graph_data['edges'])} edges")
    build_pyvis(graph_data, output_path)


if __name__ == "__main__":
    main()
