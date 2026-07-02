# Pedestrian Graph Data Contract

This document defines the pedestrian graph data StepTwin expects before pgRouting can optimize
walking segments.

## Key Rule

A single map route polyline is not enough for optimization.

It is useful for display, debugging, or seeding an initial corridor, but pgRouting can only choose a
better route when the database contains a graph with alternatives. For the Hoegi Station to Kyung Hee
Medical Center case, include the recommended path plus nearby walkable side streets, crossings,
station exits, hospital entrances, ramps, stairs, and alternate approaches.

The minimum useful dataset is:

1. Walkable vertices where a person can choose a direction.
2. Walkable edges between those vertices.
3. Edge attributes used by the cost function.
4. Enough alternate edges that stairs, slope, shade, and crossings can trade off against distance.

## Source Direction

The preferred long-term flow is:

1. Fetch candidate pedestrian geometry from a map API or open map dataset.
2. Convert the geometry into StepTwin's canonical vertex/edge graph.
3. Enrich edges with accessibility and comfort attributes.
4. Validate the graph through the StepTwin API.
5. Import the graph into PostGIS tables used by pgRouting.
6. Optimize walking segments with `/api/v1/walk-routes/optimize`.

The API source can be Kakao, TMAP, OSM, public sidewalk data, or manually curated geometry. The
backend should not depend on provider-specific field names after import. Everything should be
normalized into the structure below.

## Seoul Open Data Injection Boundary

Seoul Open Data pedestrian polylines should be injected through a separate graph ingestion API or
job before route calculation. The route preview API should not receive raw Seoul Open Data payloads
and should not depend on Seoul-specific source field names.

The ingestion adapter must:

1. Fetch Seoul Open Data geometry and related attributes.
2. Split source polylines into routable decision points and edges.
3. Convert coordinates to WGS84 latitude/longitude if the source uses another CRS.
4. Normalize the result into `PedestrianGraphDataset`.
5. Preserve source metadata in `tags`, for example `source`, `source_dataset`, and source feature IDs.
6. Call `POST /api/v1/pedestrian-graphs/validate`.
7. Call `POST /api/v1/pedestrian-graphs/import` only after validation is acceptable.

The current backend import target is:

```text
Seoul Open Data API
-> separate ingestion adapter
-> PedestrianGraphDataset JSON
-> /api/v1/pedestrian-graphs/validate
-> /api/v1/pedestrian-graphs/import
-> PostGIS pedestrian_vertices / pedestrian_edges
-> /api/v1/walk-routes/optimize
```

### Seoul OpenAPI Walking Network Source

The confirmed source service is:

```text
http://openapi.seoul.go.kr:8088/{KEY}/xml/TbTraficWlkNet/{START_INDEX}/{END_INDEX}/
```

Local configuration:

```env
SEOUL_OPENAPI_KEY=""
SEOUL_OPENAPI_BASE_URL="http://openapi.seoul.go.kr:8088"
SEOUL_WALK_NET_SERVICE="TbTraficWlkNet"
SEOUL_WALK_NET_FORMAT="xml"
SEOUL_WALK_NET_PAGE_SIZE=1000
```

The downloaded Seoul API specification identifies these request fields:

| Field | Required | Meaning |
| --- | --- | --- |
| `KEY` | yes | Seoul OpenAPI authentication key. |
| `TYPE` | yes | `xml`, `xmlf`, `xls`, or `json`. StepTwin uses `xml`. |
| `SERVICE` | yes | `TbTraficWlkNet`. |
| `START_INDEX` | yes | 1-based start row. |
| `END_INDEX` | yes | 1-based end row. |
| `SGG_NM` | no | District name filter. |
| `WORK_DTTM` | no | Collection timestamp `like` filter. |

Seoul OpenAPI limits one data request to at most 1000 rows. A sample call on 2026-07-02 returned
`list_total_count = 491082`, so a full pull requires pagination rather than one request. Use
`1/1000`, `1001/2000`, and continue until `list_total_count`.

The MVP import job loads only Dongdaemun-gu:

```powershell
.\.venv\Scripts\python.exe scripts\import_seoul_walk_network.py --sgg-name "동대문구"
```

By default, this replaces `pedestrian_vertices` and `pedestrian_edges` with the normalized
Dongdaemun-gu walking graph. Use `--append` only when the existing graph should be preserved and
upserted.

The source stream mixes `NODE` and `LINK` rows. StepTwin normalizes them before import:

| Seoul field | StepTwin field |
| --- | --- |
| `NODE_TYPE = NODE` | `PedestrianGraphVertex` |
| `NODE_WKT` | vertex `coordinate` |
| `NODE_ID` | vertex `id` |
| `NODE_TYPE_CD` | vertex `kind` using the code sheet |
| `NODE_TYPE = LINK` | `PedestrianGraphEdge` |
| `LNKG_WKT` | edge `geometry` |
| `LNKG_ID` | edge `id` |
| `BGNG_LNKG_ID` | edge `source` |
| `END_LNKG_ID` | edge `target` |
| `LNKG_LEN` | edge `distance_meters` |
| `LNKG_TYPE_CD` | edge accessibility filter and `tags.link_type_code` |
| `CRSWK`, `OVRP`, `TNL` | `crossing_type` |
| `SGG_*`, `EMD_*`, `WORK_DTTM` | source metadata in `tags` |

The separate link/node code sheet maps `LNKG_TYPE_CD` as a four-bit access code. Links whose code
starts with `1` are pedestrian-passable and are eligible for the routing graph. Other links should
stay out of `pedestrian_edges` unless a future multimodal router needs them.

Store the normalized graph in PostGIS. Do not call Seoul OpenAPI per A-B route request: the dataset
is large, pgRouting needs indexed edge and vertex tables, and the source is a daily snapshot rather
than a low-latency routing service. If source auditing is needed, keep a raw staging table as a
separate ingestion concern, then upsert normalized rows into `pedestrian_vertices` and
`pedestrian_edges`.

`/api/v1/routes/preview` currently builds demo walking segments with the in-process
`DemoMicroRouter`. After Seoul Open Data has been injected into PostGIS, the production integration
point is the walking router boundary used by `RoutePreviewService`: first-mile and last-mile walking
segments should be resolved by the pgRouting-backed router instead of the demo graph generator.

The public Android response shape does not need to change. Android should continue to render
`segments[].geometry` as ordered polylines and should ignore the backend's source provider.

## Vertices

Vertices are decision points, not every shape point in a polyline.

Use vertices for:

- Station exits, elevator exits, and stair exits.
- Hospital gates and destination entrances.
- Crosswalk endpoints.
- Sidewalk and alley intersections.
- Points where slope, stairs, surface, or accessibility changes.
- Bus stops or transit anchors that need walking connections.

```json
{
  "id": 1,
  "kind": "station_exit",
  "name": "Hoegi Station Exit",
  "coordinate": {
    "latitude": 37.58945,
    "longitude": 127.05775
  },
  "tags": {
    "source": "manual"
  }
}
```

Supported `kind` values:

```text
station_exit
hospital_gate
intersection
crossing
bus_stop
entrance
landmark
waypoint
```

## Edges

Edges are walkable segments between two vertices.

Split an edge when any routing-relevant attribute changes:

- Stairs start or end.
- Slope changes meaningfully.
- Shade changes meaningfully.
- A crosswalk begins or ends.
- Surface or sidewalk width changes.
- Wheelchair access changes.

```json
{
  "id": 10,
  "source": 1,
  "target": 2,
  "geometry": [
    {
      "latitude": 37.58945,
      "longitude": 127.05775
    },
    {
      "latitude": 37.58955,
      "longitude": 127.05785
    }
  ],
  "distance_meters": 14,
  "stairs_count": 0,
  "shade_score": 0.6,
  "slope_grade": 0.02,
  "corner_count": 1,
  "crossing_type": "crosswalk",
  "surface_type": "paved",
  "width_meters": 2.5,
  "curb_cut": true,
  "wheelchair_ok": true,
  "bidirectional": true,
  "tags": {
    "source": "manual"
  }
}
```

Required for pgRouting:

```text
id
source
target
geometry
distance_meters
stairs_count
shade_score
slope_grade
corner_count
```

Recommended for accessibility scoring:

```text
crossing_type
surface_type
width_meters
curb_cut
wheelchair_ok
bidirectional
tags
```

Supported `crossing_type` values:

```text
none
crosswalk
signalized
unsignalized
underpass
overpass
```

Supported `surface_type` values:

```text
unknown
paved
rough
gravel
stairs
ramp
```

## Full Dataset Example

```json
{
  "name": "hoegi-station-to-kyunghee-medical-center",
  "version": "draft-2026-07-02",
  "vertices": [
    {
      "id": 1,
      "kind": "station_exit",
      "name": "Hoegi Station Exit",
      "coordinate": {
        "latitude": 37.58945,
        "longitude": 127.05775
      }
    },
    {
      "id": 2,
      "kind": "crossing",
      "name": "Olive Young crossing",
      "coordinate": {
        "latitude": 37.58955,
        "longitude": 127.05785
      }
    }
  ],
  "edges": [
    {
      "id": 10,
      "source": 1,
      "target": 2,
      "geometry": [
        {
          "latitude": 37.58945,
          "longitude": 127.05775
        },
        {
          "latitude": 37.58955,
          "longitude": 127.05785
        }
      ],
      "distance_meters": 14,
      "stairs_count": 0,
      "shade_score": 0.6,
      "slope_grade": 0.02,
      "corner_count": 1,
      "crossing_type": "crosswalk",
      "surface_type": "paved",
      "width_meters": 2.5,
      "curb_cut": true,
      "wheelchair_ok": true,
      "bidirectional": true
    }
  ]
}
```

## Validation API

Before importing graph data into PostGIS, validate the JSON shape:

```http
POST /api/v1/pedestrian-graphs/validate
Content-Type: application/json
```

The endpoint checks:

- Duplicate vertex IDs.
- Duplicate edge IDs.
- Edges that reference missing vertices.
- Edge geometry that appears reversed relative to source/target.
- Edge geometry endpoints that are too far from source/target vertices.
- Missing `distance_meters`.
- Declared distance that differs sharply from computed geometry distance.
- Stairs edges where `wheelchair_ok` is not explicitly false.

Example response:

```json
{
  "dataset_name": "hoegi-station-to-kyunghee-medical-center",
  "dataset_version": "draft-2026-07-02",
  "summary": {
    "vertex_count": 2,
    "edge_count": 1,
    "total_declared_distance_meters": 14,
    "total_computed_distance_meters": 14,
    "stairs_edge_count": 0,
    "shaded_edge_count": 1,
    "crossing_edge_count": 1,
    "wheelchair_blocked_edge_count": 0,
    "missing_distance_edge_count": 0,
    "route_ready": true
  },
  "warnings": []
}
```

## Import API

After validation, import the same graph into PostGIS/pgRouting tables:

```http
POST /api/v1/pedestrian-graphs/import
Content-Type: application/json
```

```json
{
  "replace_existing": true,
  "dataset": {
    "name": "hoegi-station-to-kyunghee-medical-center",
    "version": "draft-2026-07-02",
    "vertices": [
      {
        "id": 1,
        "kind": "station_exit",
        "name": "Hoegi Station Exit",
        "coordinate": {
          "latitude": 37.58945,
          "longitude": 127.05775
        }
      },
      {
        "id": 2,
        "kind": "hospital_gate",
        "name": "Kyung Hee Medical Center",
        "coordinate": {
          "latitude": 37.59375,
          "longitude": 127.05158
        }
      }
    ],
    "edges": [
      {
        "id": 10,
        "source": 1,
        "target": 2,
        "geometry": [
          {
            "latitude": 37.58945,
            "longitude": 127.05775
          },
          {
            "latitude": 37.59375,
            "longitude": 127.05158
          }
        ],
        "stairs_count": 0,
        "shade_score": 0.4,
        "slope_grade": 0.03,
        "corner_count": 1
      }
    ]
  }
}
```

`distance_meters` may be omitted in the import payload. The importer computes it from the edge
geometry before writing to PostGIS.

When `replace_existing` is false, import uses upsert semantics:

- Existing vertex and edge IDs are updated.
- New vertex and edge IDs are inserted.
- Old rows not mentioned in the payload remain in the routing tables.

When `replace_existing` is true, the default `pedestrian_vertices` and `pedestrian_edges` tables are
cleared before inserting the new dataset. Use this for a complete replacement of the local graph.

Example response:

```json
{
  "dataset_name": "hoegi-station-to-kyunghee-medical-center",
  "dataset_version": "draft-2026-07-02",
  "vertex_count": 2,
  "edge_count": 1,
  "computed_distance_edge_count": 1,
  "replaced_existing": true,
  "vertex_table": "pedestrian_vertices",
  "edge_table": "pedestrian_edges",
  "ready_for_routing": true,
  "warnings": []
}
```

## Optimization API

After the graph is imported into PostGIS/pgRouting tables, optimize one walking segment:

```http
POST /api/v1/walk-routes/optimize
Content-Type: application/json
```

```json
{
  "start": {
    "name": "Hoegi Station",
    "coordinate": {
      "latitude": 37.58945,
      "longitude": 127.05775
    }
  },
  "end": {
    "name": "Kyung Hee Medical Center",
    "coordinate": {
      "latitude": 37.59375,
      "longitude": 127.05158
    }
  },
  "preferences": {
    "avoid_stairs": true,
    "shade_weight": 0.8,
    "stair_weight": 1.0,
    "slope_weight": 0.7,
    "corner_weight": 0.4,
    "walking_speed_mps": 1.15,
    "max_extra_walk_ratio": 0.2
  }
}
```

This endpoint returns the optimized walking geometry, snapped graph vertices, per-edge route steps,
and metrics. It does not wrap the result into the public route preview contract.

## PostGIS Table Target

The current pgRouting function expects these default tables:

```text
pedestrian_vertices
pedestrian_edges
```

Default `pedestrian_vertices` columns:

```text
id
geom
```

Default `pedestrian_edges` columns:

```text
id
source
target
geom
distance_meters
stairs_count
shade_score
corner_count
slope_grade
```

The importer should convert API graph JSON into these tables. Additional attributes can be stored
for future scorers, but the columns above are enough for the current pgRouting cost function.
