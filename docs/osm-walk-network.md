# OSM Walking Network

The current pedestrian graph source is OSM data fetched from Overpass and stored in PostGIS tables
that are directly usable by pgRouting.

## Tables

Default routing tables:

```env
PEDESTRIAN_GRAPH_VERTEX_TABLE="osm_pedestrian_vertices"
PEDESTRIAN_GRAPH_EDGE_TABLE="osm_pedestrian_edges"
```

`osm_pedestrian_vertices` stores OSM nodes as WGS84 points. `osm_pedestrian_edges` stores adjacent
OSM way-node segments as WGS84 LineStrings with derived routing attributes:

```text
distance_meters
stairs_count
shade_score
corner_count
slope_grade
roughness_score
crossing_type
surface_type
width_meters
wheelchair_ok
```

## Import

Fetch and import a bbox:

```powershell
.\.venv\Scripts\python.exe scripts\import_osm_walk_network.py `
  --south 37.5607 `
  --west 127.0233 `
  --north 37.6060 `
  --east 127.0773
```

Dry run without writing to PostGIS:

```powershell
.\.venv\Scripts\python.exe scripts\import_osm_walk_network.py `
  --south 37.5607 `
  --west 127.0233 `
  --north 37.6060 `
  --east 127.0773 `
  --dry-run
```

Use `--append` only when adding another bbox to the existing OSM graph. Without `--append`, the OSM
tables are replaced.

## Routing

`/api/v1/routes/preview` and `/api/v1/walk-routes/optimize` read the configured graph tables through
`PEDESTRIAN_GRAPH_VERTEX_TABLE` and `PEDESTRIAN_GRAPH_EDGE_TABLE`. Route responses continue to return
ordered WGS84 coordinates in `geometry`.

Generated graph viewer HTML files are local inspection artifacts and are ignored by git.
