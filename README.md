# Steptwin API

FastAPI + PostGIS/pgRouting-ready backend starter for the hackathon.

Shared Android/backend API contract:

```text
docs/api-contract.md
```

Android handoff contract for the current PoC:

```text
docs/android-api-handoff.md
```

Keep external API keys in local `.env` only. For TMAP, fill `TMAP_APP_KEY` in `.env` and keep
`TMAP_USE_LIVE=false` until the exact TMAP endpoint/response parser is confirmed.
TMAP transit API integration notes are tracked in `docs/tmap-transit-api.md`.
OSM walking-network notes are tracked in `docs/osm-walk-network.md`.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up -d postgres
```

Fill local secrets in `.env` if needed. Do not commit `.env`.

Check that PostgreSQL is reachable:

```powershell
docker compose ps
```

## Running the API

### Local-only backend

Use this when testing from the same laptop only:

```powershell
.\.venv\Scripts\python.exe -m uvicorn steptwin_api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/docs
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

### Android/LAN backend

Use this when testing from an Android device on the same Wi-Fi:

```powershell
.\.venv\Scripts\python.exe -m uvicorn steptwin_api.main:app --app-dir src --host 0.0.0.0 --port 8000
```

Equivalent helper script:

```powershell
.\scripts\run-api.ps1 -Lan
```

Find the laptop Wi-Fi IP:

```powershell
ipconfig | Select-String -Pattern "IPv4"
```

Current development example:

```text
http://172.30.1.66:8000
```

From the laptop, verify both local and LAN URLs:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://172.30.1.66:8000/api/v1/health
```

From Android, use the laptop LAN IP, not `localhost`:

```text
http://172.30.1.66:8000/api/v1/health
http://172.30.1.66:8000/api/v1/routes/preview
```

Android must be on the same Wi-Fi as the laptop. If the phone cannot reach the backend, allow the
Windows firewall rule once:

```powershell
.\scripts\allow-api-firewall.ps1
```

Remove the firewall rule after testing if needed:

```powershell
.\scripts\remove-api-firewall.ps1
```

### Stop the API

If the server is running in the foreground, press:

```text
Ctrl+C
```

If a background process is using port `8000`, find and stop it:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
Stop-Process -Id <OwningProcess>
```

If you know the process ID:

```powershell
Stop-Process -Id 30264
```

### Android route request smoke test

```powershell
$body = @{
  origin = @{
    name = "Seoul Station"
    coordinate = @{ latitude = 37.5546788; longitude = 126.9706069 }
  }
  destination = @{
    name = "Hoegi Station"
    coordinate = @{ latitude = 37.589802; longitude = 127.057936 }
  }
  preferences = @{
    avoid_stairs = $true
    shade_weight = 0.8
    stair_weight = 1.0
    slope_weight = 0.7
    corner_weight = 0.4
    walking_speed_mps = 1.15
    max_extra_walk_ratio = 0.2
  }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/routes/preview `
  -ContentType "application/json" `
  -Body $body
```

For Seoul Station to Hoegi Station, the response should include a `transit-1` segment with
`transit.route_name = "수도권1호선"`, `transit.subway_line = "수도권1호선"`, and a transit line color
such as `#0052A4`.

The pure walking endpoint still exists:

```text
POST /api/v1/walk-routes/optimize
```

Use it only for nearby walking-only tests. It does not call TMAP and does not choose subway or bus.

## OSM Walking Network Import

The current walking graph source is OSM from Overpass.

Default routing tables:

```env
PEDESTRIAN_GRAPH_VERTEX_TABLE="osm_pedestrian_vertices"
PEDESTRIAN_GRAPH_EDGE_TABLE="osm_pedestrian_edges"
```

If using the experimental topology rebuild tables:

```env
PEDESTRIAN_GRAPH_VERTEX_TABLE="pedestrian_topology_vertices"
PEDESTRIAN_GRAPH_EDGE_TABLE="pedestrian_topology_edges"
```

Import OSM walkable roads from an Overpass bbox into the OSM-only tables:

```powershell
.\.venv\Scripts\python.exe scripts\import_osm_walk_network.py `
  --south 37.58 `
  --west 127.04 `
  --north 37.60 `
  --east 127.07
```

Useful first dry run:

```powershell
.\.venv\Scripts\python.exe scripts\import_osm_walk_network.py `
  --south 37.58 `
  --west 127.04 `
  --north 37.60 `
  --east 127.07 `
  --dry-run
```

By default, the OSM import replaces:

```text
osm_pedestrian_vertices
osm_pedestrian_edges
```

Use `--append` only when adding another bbox to the existing OSM graph:

```powershell
.\.venv\Scripts\python.exe scripts\import_osm_walk_network.py `
  --south 37.56 `
  --west 127.02 `
  --north 37.61 `
  --east 127.08 `
  --append
```

Dongdaemun-gu bbox import used during development:

```powershell
.\.venv\Scripts\python.exe scripts\import_osm_walk_network.py `
  --south 37.5607 `
  --west 127.0233 `
  --north 37.6060 `
  --east 127.0773
```

Current OSM road storage:

```text
osm_pedestrian_vertices
  id                  OSM node id, used as pgRouting vertex id
  osm_node_id          original OSM node id
  geom                WGS84 point
  tags                original OSM node tags

osm_pedestrian_edges
  id                  generated as osm_way_id * 10000 + segment_index + 1
  osm_way_id           original OSM way id
  source              source OSM node id
  target              target OSM node id
  geom                WGS84 LineString between adjacent OSM way nodes
  distance_meters      computed segment length
  stairs_count         1 when highway=steps, otherwise 0
  slope_grade          derived from OSM incline when available, otherwise 0
  roughness_score      0 to 1 bumpiness score derived from smoothness/surface/tracktype/steps
  shade_score          derived from covered/tunnel/bridge tags
  crossing_type        derived from crossing/footway tags
  surface_type         normalized surface bucket
  width_meters         derived from OSM width when parseable
  wheelchair_ok        derived from wheelchair/highway=steps tags
  tags                original OSM way tags
```

The current route cost already uses `slope_grade`. `roughness_score` is stored for bumpy-road
scoring, but a user-facing roughness preference still needs to be wired into the cost model.

Export a standalone visualizer for the currently configured graph tables:

```powershell
.\.venv\Scripts\python.exe scripts\export_pedestrian_graph_viewer.py `
  --output docs\osm_dongdaemun_graph_viewer.html
```

Generated graph viewer HTML files are local inspection artifacts and are ignored by git.

Interactive API docs:

```text
http://127.0.0.1:8000/docs
```

If `DATABASE_URL` is empty, the health API still works and reports the database check as disabled.
The pgRouting walk route APIs require PostgreSQL/PostGIS/pgRouting.

## Project Shape

```text
src/steptwin_api/
  api/        HTTP routers
  core/       app config, logging, lifespan
  db/         async SQLAlchemy engine/session utilities
  schemas/    Pydantic response models
```
