# StepTwin API Contract

Version: `0.1.0`

This document is the shared contract between the FastAPI backend and the Android/Kakao Map client.

## Base URL

Local laptop server:

```text
http://<laptop-lan-ip>:8000
```

Current example during development:

```text
http://172.30.1.66:8000
```

The laptop IP can change when the Wi-Fi changes. The backend developer should confirm the current
IP before Android testing.

## Network Requirements

- Android device and backend laptop must be on the same Wi-Fi.
- Backend must run with LAN binding:

```powershell
.\scripts\run-api.ps1 -Lan
```

- Android must not use `localhost`.
- Android must use the laptop LAN IP.
- Android needs internet permission:

```xml
<uses-permission android:name="android.permission.INTERNET" />
```

- Because the PoC uses HTTP, Android may need cleartext traffic enabled:

```xml
<application
    android:usesCleartextTraffic="true">
</application>
```

## Health Check

Use this before calling route preview.

```http
GET /api/v1/health
```

Expected success:

```json
{
  "status": "ok",
  "checks": {
    "application": {
      "status": "ok"
    },
    "database": {
      "status": "ok"
    }
  }
}
```

## Route Preview

This is the main endpoint Android should call for Kakao Map visualization.

```http
POST /api/v1/routes/preview
Content-Type: application/json
```

### Request Body

```json
{
  "origin": {
    "name": "Seoul City Hall",
    "coordinate": {
      "latitude": 37.5665,
      "longitude": 126.978
    }
  },
  "destination": {
    "name": "Namsan Seoul Tower",
    "coordinate": {
      "latitude": 37.5512,
      "longitude": 126.9882
    }
  },
  "vulnerabilities": {
    "speed_vulnerability": 0.4,
    "turn_vulnerability": 0.2,
    "strength_vulnerability": 0.7
  }
}
```

### Request Fields

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `origin.name` | string | yes | Human-readable start label. |
| `origin.coordinate.latitude` | number | yes | Latitude in WGS84. |
| `origin.coordinate.longitude` | number | yes | Longitude in WGS84. |
| `destination.name` | string | yes | Human-readable destination label. |
| `destination.coordinate.latitude` | number | yes | Latitude in WGS84. |
| `destination.coordinate.longitude` | number | yes | Longitude in WGS84. |
| `vulnerabilities.speed_vulnerability` | number | no | Speed vulnerability from `0` to `1`. Default: `0`. |
| `vulnerabilities.turn_vulnerability` | number | no | Turn/direction-change vulnerability from `0` to `1`. Default: `0`. |
| `vulnerabilities.strength_vulnerability` | number | no | Strength/stair/slope vulnerability from `0` to `1`. Default: `0`. |

`preferences` is still accepted for backend/debug callers, but Android should send only
`vulnerabilities`. When `vulnerabilities` is present, the backend ignores raw `preferences` and
derives the internal routing preferences.

### Vulnerability Mapping

Let:

```text
S = speed_vulnerability
T = turn_vulnerability
M = strength_vulnerability
```

The backend derives internal preferences as:

```text
avoid_stairs = M >= 0.45 or S >= 0.65
walking_speed_mps = clamp(1.35 - 0.50*S - 0.20*M, 0.65, 1.35)
stair_weight = clamp(0.6 + 1.8*M + 0.6*S, 0, 3)
slope_weight = clamp(0.4 + 1.4*M + 0.6*S, 0, 3)
corner_weight = clamp(0.2 + 2.0*T + 0.3*S, 0, 3)
shade_weight = clamp(0.45 + 0.25*S + 0.20*M, 0, 1)
crowding_weight = clamp(0.3 + 0.8*T + 0.4*S + 0.3*M, 0, 3)
max_extra_walk_ratio = clamp(0.12 + 0.08*M + 0.07*T - 0.04*S, 0.08, 0.30)
```

Coordinate order is always:

```text
latitude, longitude
```

Do not swap these when converting to Kakao Map coordinates.

### Backend Flow For Origin/Destination Input

When Android posts `origin` and `destination` to `/api/v1/routes/preview`, the current backend flow
is:

1. FastAPI validates the request with `RoutePreviewRequest`.
2. `RoutePreviewService.build_preview()` asks the macro router for a transit skeleton.
3. The macro router returns boarding/alighting stops, transit geometry, and transit metadata.
   - With `TMAP_USE_LIVE=false`, this is deterministic demo geometry between the submitted points.
   - With `TMAP_USE_LIVE=true`, the TMAP adapter calls the configured TMAP transit endpoint and
     falls back to demo geometry only if the payload cannot be parsed.
4. The micro router builds the first walking segment from `origin` to the boarding stop.
5. The micro router builds the last walking segment from the alighting stop to `destination`.
6. The backend combines first-mile walk, transit, and last-mile walk into `segments[]`.
7. The backend builds markers for origin, destination, transit stops, shade points, and avoided stairs.
8. The backend computes `summary` totals and `viewport`.
9. The response is returned as `RoutePreviewResponse`.

Walking segments use pgRouting when endpoints snap to the configured OSM pedestrian graph tables.
Out-of-coverage walking segments fall back to the in-process demo pedestrian router. The Android
response contract remains `segments[].geometry` plus `markers[]`.

When transit candidates are available, the backend still compares them against a direct walking
route. A healthy user may receive a single `custom_walk` segment when direct walking is cheaper than
the transit route after transfer, boarding, and bus-reliability penalties.

### Walking Cost Formula

The backend scores each walking edge in seconds:

```text
base_seconds = distance_meters / walking_speed_mps
score =
  base_seconds
  + stairs_count * 240 * stair_weight
  + avoid_stairs_extra
  + distance_meters * slope_grade * 24.0 * slope_weight
  + corner_count * 18 * corner_weight
  + base_seconds * crowding_score * 0.6 * crowding_weight
  + crossing_wait_seconds
  - base_seconds * shade_score * 0.35 * shade_weight
```

`avoid_stairs_extra` is `360 * stairs_count` when `avoid_stairs=true`. `crowding_score` is a
normalized `0..1` value derived from Seoul S-DoT `VISITOR_COUNT`; higher values make crowded edges
less attractive. The score is clamped to at least `base_seconds * 0.35` so rewards cannot make an
edge unrealistically cheap.

Transit candidates are ranked with:

```text
transit_score =
  transit_duration_seconds
  + transfer_count * 180
  + boarding_count * 90
  + bus_leg_count * 60
  + bus_leg_count * 210
  + bus_to_bus_transfer_count * 120
  + bus_duration_seconds * 0.30
```

For final route preview selection, access/transfer/egress walking durations are added to
`transit_score`. For slower or stronger-vulnerability users, direct walking must be clearly faster
than transit before the response uses walking-only.

### Response Body

```json
{
  "route_id": "cecdccef-e53a-46cb-806e-b9e16fb742be",
  "summary": {
    "total_distance_meters": 1972,
    "total_duration_seconds": 1225,
    "walking_distance_meters": 1202,
    "transit_distance_meters": 770,
    "shade_shelters": 4,
    "stairs_avoided": 2
  },
  "segments": [
    {
      "id": "walk-first-mile",
      "kind": "custom_walk",
      "mode": "walk",
      "title": "Stair-minimized first mile",
      "geometry": [
        { "latitude": 37.5665, "longitude": 126.978 },
        { "latitude": 37.56441018849429, "longitude": 126.97843047551066 }
      ],
      "render": {
        "color": "#16A34A",
        "width": 6,
        "pattern": "dashed"
      },
      "metrics": {
        "distance_meters": 638,
        "duration_seconds": 555,
        "shade_shelters": 2,
        "stairs_avoided": 1
      },
      "transit": null
    },
    {
      "id": "transit-main",
      "kind": "transit",
      "mode": "subway",
      "title": "Ride Demo Transit Line",
      "geometry": [
        { "latitude": 37.561604, "longitude": 126.981264 },
        { "latitude": 37.555484, "longitude": 126.985344 }
      ],
      "render": {
        "color": "#2563EB",
        "width": 7,
        "pattern": "solid"
      },
      "metrics": {
        "distance_meters": 770,
        "duration_seconds": 180,
        "shade_shelters": 0,
        "stairs_avoided": 0
      },
      "transit": {
        "mode": "subway",
        "route_name": "Demo Transit Line",
        "bus_number": null,
        "subway_line": "Demo Transit Line",
        "boarding_stop": "StepTwin Demo Station",
        "alighting_stop": "Sunshade Transfer Stop",
        "headsign": "Namsan Seoul Tower"
      }
    }
  ],
  "markers": [
    {
      "id": "walk-first-mile-shade-1",
      "kind": "shade_shelter",
      "title": "Shade shelter",
      "coordinate": {
        "latitude": 37.56441018849429,
        "longitude": 126.97843047551066
      },
      "segment_id": "walk-first-mile",
      "icon": "parasol"
    }
  ],
  "viewport": {
    "southwest": {
      "latitude": 37.549976,
      "longitude": 126.977
    },
    "northeast": {
      "latitude": 37.567724,
      "longitude": 126.9892
    }
  },
  "debug": {
    "macro_router": "demo-tmap-adapter",
    "micro_router": "demo-custom-pedestrian-router",
    "tmap_live_sync": false,
    "note": "PoC route is deterministic demo data shaped for frontend rendering."
  }
}
```

## Response Fields

### `segments`

Android must draw each `segments[]` item as a polyline.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Stable segment ID within this response. |
| `kind` | string | Either `custom_walk` or `transit`. |
| `mode` | string | `walk`, `bus`, or `subway`. |
| `title` | string | User-facing instruction such as `지하철 수도권1호선: 서울역 -> 회기`. |
| `geometry` | array | Ordered route coordinates. Draw in this order. |
| `render.color` | string | Hex color for polyline. |
| `render.width` | number | Suggested line width. |
| `render.pattern` | string | `solid` or `dashed`. |
| `metrics` | object | Distance/duration/demo scoring values. |
| `transit` | object or null | Transit details only when `kind = transit`. |
| `transit.route_name` | string | Backward-compatible route display name. |
| `transit.bus_number` | string or null | Bus number when `transit.mode = bus`. |
| `transit.subway_line` | string or null | Subway line when `transit.mode = subway`. |

### `markers`

Android must draw each `markers[]` item as a map marker.

| `kind` | Meaning | Suggested icon |
| --- | --- | --- |
| `origin` | Start point | Origin pin |
| `destination` | End point | Destination pin |
| `stop` | Transit boarding/alighting stop | Transit stop icon |
| `shade_shelter` | Shade shelter or tree shade point | Parasol or tree |
| `stairs_avoided` | Stairs avoided by custom walking route | Stairs-off icon |

Use `marker.icon` as the first mapping key when possible.

Known `icon` values:

```text
origin
destination
transit-stop
bus-stop
subway-stop
parasol
tree
stairs-off
```

### `viewport`

Use `viewport.southwest` and `viewport.northeast` to move/fit the Kakao map camera around the full
route.

## Kakao Map Rendering Rules

1. For each `segments[]`, convert `geometry[]` to Kakao coordinates.
2. Draw one polyline per segment.
3. Use `segment.render.color`.
4. Use `segment.render.width`.
5. If `segment.render.pattern = dashed`, draw it as a dashed line if the SDK supports it. Otherwise,
   fallback to a solid green line for the PoC.
6. For each `markers[]`, draw one marker at `marker.coordinate`.
7. Choose marker image by `marker.icon`.

Backend default visual mapping:

| Segment kind | Color | Pattern |
| --- | --- | --- |
| `custom_walk` | `#16A34A` | `dashed` |
| `transit` | `#2563EB` | `solid` |

## Android Implementation Checklist

1. Load Kakao Map.
2. Verify backend health:

```text
GET http://<laptop-lan-ip>:8000/api/v1/health
```

3. Call route preview:

```text
POST http://<laptop-lan-ip>:8000/api/v1/routes/preview
```

4. Parse response.
5. Draw `segments` as polylines.
6. Draw `markers` as markers.
7. Fit camera to `viewport`.

Android does not need to calculate routes during the PoC. The backend returns all geometry needed
for visualization.

## Important Stability Rules

- Treat unknown `marker.kind`, `marker.icon`, or `segment.kind` as non-fatal.
- Ignore unknown fields in JSON.
- Do not depend on exact `title` text.
- Do not depend on a fixed transit segment ID. Mixed bus/subway routes may return `transit-1`,
  `walk-transfer-1`, `transit-2`, and so on.
- Always draw by `geometry` order.
- Always use WGS84 latitude/longitude from the response.
- Backend response may later include real TMAP data without changing this top-level shape.

## Backend TMAP Configuration

Do not put the TMAP appKey in Android code. The backend owns TMAP calls.

The backend reads TMAP settings from local `.env`:

```env
TMAP_APP_KEY=""
TMAP_BASE_URL="https://apis.openapi.sk.com"
TMAP_TRANSIT_PATH="/transit/routes"
TMAP_TIMEOUT_SECONDS=5
TMAP_USE_LIVE=false
TMAP_LANG=0
TMAP_FORMAT="json"
TMAP_COUNT=10
TMAP_SEARCH_DTTM=""
```

Rules:

- Keep the real `TMAP_APP_KEY` in local `.env` only.
- Do not commit `.env`.
- Set `TMAP_USE_LIVE=true` only after the exact TMAP transit endpoint path and response parser are confirmed.
- Android continues to call only StepTwin backend endpoints.

Confirmed TMAP endpoint notes are tracked in:

```text
docs/tmap-transit-api.md
```

## Backend Seoul S-DoT Configuration

The backend maps Seoul OpenAPI S-DoT request parameters like this:

| Seoul parameter | Backend setting | Default |
| --- | --- | --- |
| `KEY` | `SEOUL_OPENAPI_KEY` | empty |
| `TYPE` | `SEOUL_SDOT_TYPE` | `json` |
| `SERVICE` | `SEOUL_SDOT_SERVICE` | `sDoTPeople` |
| `START_INDEX` | `SEOUL_SDOT_START_INDEX` | `1` |
| `END_INDEX` | `SEOUL_SDOT_END_INDEX` | `100` |

Example URL shape:

```text
http://openapi.seoul.go.kr:8088/<KEY>/json/sDoTPeople/1/100/
```

Confirmed TMAP endpoint notes are tracked in:

```text
docs/tmap-transit-api.md
```

Confirmed TMAP endpoint notes are tracked in:

```text
docs/tmap-transit-api.md
```

Confirmed TMAP endpoint notes are tracked in:

```text
docs/tmap-transit-api.md
```
