from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url

from steptwin_api.core.config import PROJECT_ROOT, get_settings
from steptwin_api.db.session import close_database, init_database, session_context
from steptwin_api.services.pgrouting_micro_routing import quote_qualified_identifier

DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "pedestrian_graph_viewer.html"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the current PostGIS pedestrian graph as a standalone HTML viewer."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output HTML path. Default: {DEFAULT_OUTPUT}",
    )
    return parser.parse_args()


async def fetch_graph_payload() -> dict[str, Any]:
    settings = get_settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is required")
    if not make_url(settings.database_url).drivername.startswith("postgresql"):
        raise SystemExit("DATABASE_URL must use PostgreSQL/PostGIS/pgRouting")

    init_database(settings)
    edge_table = quote_qualified_identifier(settings.pedestrian_graph_edge_table)
    vertex_table = quote_qualified_identifier(settings.pedestrian_graph_vertex_table)
    try:
        async with session_context() as session:
            edge_rows = (
                await session.execute(
                    text(
                        f"""
WITH comps AS (
    SELECT *
    FROM pgr_connectedComponents(
        'SELECT id::bigint AS id,
                source::bigint AS source,
                target::bigint AS target,
                distance_meters::float8 AS cost,
                distance_meters::float8 AS reverse_cost
         FROM {settings.pedestrian_graph_edge_table}
         WHERE distance_meters > 0'
    )
),
component_sizes AS (
    SELECT component, count(*)::integer AS node_count
    FROM comps
    GROUP BY component
),
edge_components AS (
    SELECT edge.id, comps.component, component_sizes.node_count
    FROM {edge_table} AS edge
    LEFT JOIN comps ON comps.node = edge.source
    LEFT JOIN component_sizes ON component_sizes.component = comps.component
)
SELECT
    edge.id,
    edge.source,
    edge.target,
    edge.distance_meters,
    edge.stairs_count,
    edge.shade_score,
    edge.corner_count,
    edge.slope_grade,
    COALESCE(edge_components.component, edge.source)::bigint AS component,
    COALESCE(edge_components.node_count, 1)::integer AS component_node_count,
    ST_AsGeoJSON(edge.geom) AS geometry_json
FROM {edge_table} AS edge
LEFT JOIN edge_components ON edge_components.id = edge.id
ORDER BY component_node_count DESC, component, edge.id
"""
                    )
                )
            ).mappings().all()
            vertex_rows = (
                await session.execute(
                    text(
                        f"""
WITH comps AS (
    SELECT *
    FROM pgr_connectedComponents(
        'SELECT id::bigint AS id,
                source::bigint AS source,
                target::bigint AS target,
                distance_meters::float8 AS cost,
                distance_meters::float8 AS reverse_cost
         FROM {settings.pedestrian_graph_edge_table}
         WHERE distance_meters > 0'
    )
),
degrees AS (
    SELECT vertex_id, count(*)::integer AS degree
    FROM (
        SELECT source AS vertex_id FROM {edge_table}
        UNION ALL
        SELECT target AS vertex_id FROM {edge_table}
    ) AS edge_vertices
    GROUP BY vertex_id
),
component_sizes AS (
    SELECT component, count(*)::integer AS node_count
    FROM comps
    GROUP BY component
)
SELECT
    vertex.id,
    ST_Y(vertex.geom) AS latitude,
    ST_X(vertex.geom) AS longitude,
    COALESCE(degrees.degree, 0)::integer AS degree,
    comps.component,
    COALESCE(component_sizes.node_count, 0)::integer AS component_node_count
FROM {vertex_table} AS vertex
LEFT JOIN degrees ON degrees.vertex_id = vertex.id
LEFT JOIN comps ON comps.node = vertex.id
LEFT JOIN component_sizes ON component_sizes.component = comps.component
ORDER BY component_node_count DESC, component, vertex.id
"""
                    )
                )
            ).mappings().all()
    finally:
        await close_database()

    edges = [edge_payload(row) for row in edge_rows]
    vertices = [dict(row) for row in vertex_rows]
    components: dict[str, dict[str, Any]] = {}
    for edge in edges:
        key = str(edge["component"])
        current = components.setdefault(
            key,
            {
                "component": edge["component"],
                "node_count": edge["component_node_count"],
                "edge_count": 0,
                "distance_meters": 0.0,
            },
        )
        current["edge_count"] += 1
        current["distance_meters"] += edge["distance_meters"]

    return {
        "summary": {
            "edge_count": len(edges),
            "vertex_count": len(vertices),
            "component_count": len(components),
        },
        "components": sorted(
            components.values(),
            key=lambda item: (item["node_count"], item["edge_count"]),
            reverse=True,
        ),
        "edges": edges,
        "vertices": vertices,
    }


def edge_payload(row: Any) -> dict[str, Any]:
    geometry = json.loads(row["geometry_json"])
    return {
        "id": row["id"],
        "source": row["source"],
        "target": row["target"],
        "distance_meters": float(row["distance_meters"]),
        "stairs_count": row["stairs_count"],
        "shade_score": float(row["shade_score"]),
        "corner_count": row["corner_count"],
        "slope_grade": float(row["slope_grade"]),
        "component": row["component"],
        "component_node_count": row["component_node_count"],
        "coordinates": geometry["coordinates"],
    }


def build_html(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StepTwin Pedestrian Graph Viewer</title>
<style>
html, body {{ margin: 0; height: 100%; font-family: Arial, sans-serif; color: #17202a; }}
body {{ display: grid; grid-template-columns: 320px 1fr; background: #f5f7f8; }}
aside {{ padding: 16px; border-right: 1px solid #d7dde2; background: #ffffff; overflow: auto; }}
main {{ position: relative; min-width: 0; }}
canvas {{ display: block; width: 100%; height: 100%; background: #eef2f3; }}
h1 {{ margin: 0 0 12px; font-size: 20px; line-height: 1.2; }}
label {{ display: block; margin: 14px 0 6px; font-size: 12px; font-weight: 700; color: #4c5965; }}
select, button {{ width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid #c7d0d8; border-radius: 6px; background: #fff; }}
button {{ cursor: pointer; }}
.stat {{ display: grid; grid-template-columns: 1fr auto; gap: 8px; padding: 7px 0; border-bottom: 1px solid #edf0f2; font-size: 13px; }}
.legend {{ margin-top: 12px; font-size: 12px; line-height: 1.45; color: #4c5965; }}
.hint {{ position: absolute; left: 12px; bottom: 12px; padding: 8px 10px; background: rgba(255,255,255,.92); border: 1px solid #d7dde2; border-radius: 6px; font-size: 12px; }}
.row {{ display: flex; gap: 8px; }}
.row button {{ flex: 1; }}
@media (max-width: 800px) {{
  body {{ grid-template-columns: 1fr; grid-template-rows: auto 1fr; }}
  aside {{ max-height: 280px; border-right: 0; border-bottom: 1px solid #d7dde2; }}
}}
</style>
</head>
<body>
<aside>
  <h1>Pedestrian Graph</h1>
  <div id="summary"></div>
  <label for="componentSelect">Component</label>
  <select id="componentSelect"></select>
  <label for="vertexMode">Vertices</label>
  <select id="vertexMode">
    <option value="connected">Connected only</option>
    <option value="all">All vertices</option>
    <option value="none">Hidden</option>
  </select>
  <label for="edgeMode">Edges</label>
  <select id="edgeMode">
    <option value="component">Color by component</option>
    <option value="shade">Shade score</option>
    <option value="stairs">Stairs</option>
  </select>
  <label>View</label>
  <div class="row">
    <button id="fitButton">Fit</button>
    <button id="topButton">Top 10</button>
  </div>
  <div class="legend">
    Drag to pan. Wheel to zoom. Large components use stronger colors; tiny fragments make the
    topology break visible.
  </div>
</aside>
<main>
  <canvas id="map"></canvas>
  <div class="hint" id="hint"></div>
</main>
<script id="graph-data" type="application/json">{payload_json}</script>
<script>
const graph = JSON.parse(document.getElementById("graph-data").textContent);
const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");
const componentSelect = document.getElementById("componentSelect");
const vertexMode = document.getElementById("vertexMode");
const edgeMode = document.getElementById("edgeMode");
const hint = document.getElementById("hint");
let view = {{ scale: 1, x: 0, y: 0 }};
let dragging = false;
let lastMouse = null;

function componentColor(component) {{
  const n = Number(component) || 0;
  const hue = (n * 47) % 360;
  return `hsl(${{hue}} 72% 42%)`;
}}

function edgeColor(edge) {{
  if (edgeMode.value === "shade") {{
    const green = Math.round(80 + edge.shade_score * 140);
    return `rgb(30,${{green}},90)`;
  }}
  if (edgeMode.value === "stairs") {{
    return edge.stairs_count > 0 ? "#c2410c" : "#2f6f8f";
  }}
  return componentColor(edge.component);
}}

function lonLatToWorld(lon, lat) {{
  const x = lon;
  const y = Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
  return [x, y];
}}

function getBounds(edges) {{
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const edge of edges) {{
    for (const pair of edge.coordinates) {{
      const [x, y] = lonLatToWorld(pair[0], pair[1]);
      minX = Math.min(minX, x); minY = Math.min(minY, y);
      maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
    }}
  }}
  return {{ minX, minY, maxX, maxY }};
}}

function selectedEdges() {{
  const selected = componentSelect.value;
  if (selected === "all") return graph.edges;
  if (selected === "top10") {{
    const top = new Set(graph.components.slice(0, 10).map(c => String(c.component)));
    return graph.edges.filter(edge => top.has(String(edge.component)));
  }}
  return graph.edges.filter(edge => String(edge.component) === selected);
}}

function fit(edges) {{
  const bounds = getBounds(edges.length ? edges : graph.edges);
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const pad = 40;
  const sx = (width - pad * 2) / Math.max(bounds.maxX - bounds.minX, 1e-9);
  const sy = (height - pad * 2) / Math.max(bounds.maxY - bounds.minY, 1e-9);
  view.scale = Math.min(sx, sy);
  view.x = pad - bounds.minX * view.scale + (width - pad * 2 - (bounds.maxX - bounds.minX) * view.scale) / 2;
  view.y = pad + bounds.maxY * view.scale + (height - pad * 2 - (bounds.maxY - bounds.minY) * view.scale) / 2;
}}

function project(lon, lat) {{
  const [x, y] = lonLatToWorld(lon, lat);
  return [x * view.scale + view.x, -y * view.scale + view.y];
}}

function resize() {{
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(canvas.clientWidth * dpr));
  canvas.height = Math.max(1, Math.floor(canvas.clientHeight * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}}

function draw() {{
  const edges = selectedEdges();
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.globalAlpha = 0.92;
  for (const edge of edges) {{
    ctx.beginPath();
    for (let i = 0; i < edge.coordinates.length; i++) {{
      const [lon, lat] = edge.coordinates[i];
      const [x, y] = project(lon, lat);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }}
    ctx.strokeStyle = edgeColor(edge);
    ctx.lineWidth = edge.component_node_count >= 40 ? 2.4 : 1.3;
    ctx.stroke();
  }}
  drawVertices(edges);
  const meters = Math.round(edges.reduce((sum, edge) => sum + edge.distance_meters, 0));
  hint.textContent = `${{edges.length}} edges, ${{meters.toLocaleString()}} m`;
}}

function drawVertices(edges) {{
  if (vertexMode.value === "none") return;
  const selected = componentSelect.value;
  let vertices = graph.vertices;
  if (selected === "top10") {{
    const top = new Set(graph.components.slice(0, 10).map(c => String(c.component)));
    vertices = vertices.filter(v => top.has(String(v.component)));
  }} else if (selected !== "all") {{
    vertices = vertices.filter(v => String(v.component) === selected);
  }}
  if (vertexMode.value === "connected") vertices = vertices.filter(v => v.degree > 0);
  ctx.globalAlpha = 0.75;
  for (const vertex of vertices) {{
    const [x, y] = project(vertex.longitude, vertex.latitude);
    ctx.beginPath();
    ctx.arc(x, y, vertex.degree > 0 ? 2.2 : 1.6, 0, Math.PI * 2);
    ctx.fillStyle = vertex.degree > 0 ? "#111827" : "#ef4444";
    ctx.fill();
  }}
  ctx.globalAlpha = 0.92;
}}

function populateControls() {{
  componentSelect.innerHTML = "";
  componentSelect.append(new Option("All components", "all"));
  componentSelect.append(new Option("Top 10 components", "top10"));
  for (const comp of graph.components.slice(0, 120)) {{
    const label = `#${{comp.component}} - ${{comp.node_count}} nodes, ${{comp.edge_count}} edges`;
    componentSelect.append(new Option(label, String(comp.component)));
  }}
  document.getElementById("summary").innerHTML = `
    <div class="stat"><span>Vertices</span><b>${{graph.summary.vertex_count.toLocaleString()}}</b></div>
    <div class="stat"><span>Edges</span><b>${{graph.summary.edge_count.toLocaleString()}}</b></div>
    <div class="stat"><span>Components</span><b>${{graph.summary.component_count.toLocaleString()}}</b></div>
    <div class="stat"><span>Largest component</span><b>${{graph.components[0]?.node_count ?? 0}} nodes</b></div>
  `;
}}

canvas.addEventListener("mousedown", event => {{
  dragging = true;
  lastMouse = [event.clientX, event.clientY];
}});
window.addEventListener("mouseup", () => dragging = false);
window.addEventListener("mousemove", event => {{
  if (!dragging || !lastMouse) return;
  view.x += event.clientX - lastMouse[0];
  view.y += event.clientY - lastMouse[1];
  lastMouse = [event.clientX, event.clientY];
  draw();
}});
canvas.addEventListener("wheel", event => {{
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.18 : 0.85;
  const before = [(event.offsetX - view.x) / view.scale, (event.offsetY - view.y) / view.scale];
  view.scale *= factor;
  view.x = event.offsetX - before[0] * view.scale;
  view.y = event.offsetY - before[1] * view.scale;
  draw();
}}, {{ passive: false }});

componentSelect.addEventListener("change", () => {{ fit(selectedEdges()); draw(); }});
vertexMode.addEventListener("change", draw);
edgeMode.addEventListener("change", draw);
document.getElementById("fitButton").addEventListener("click", () => {{ fit(selectedEdges()); draw(); }});
document.getElementById("topButton").addEventListener("click", () => {{
  componentSelect.value = "top10";
  fit(selectedEdges());
  draw();
}});
window.addEventListener("resize", resize);

populateControls();
fit(graph.edges);
resize();
</script>
</body>
</html>
"""


async def main() -> None:
    args = parse_args()
    payload = await fetch_graph_payload()
    html = build_html(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(f"wrote {args.output}")
    print(
        "summary: "
        f"{payload['summary']['vertex_count']} vertices, "
        f"{payload['summary']['edge_count']} edges, "
        f"{payload['summary']['component_count']} components"
    )


if __name__ == "__main__":
    asyncio.run(main())
