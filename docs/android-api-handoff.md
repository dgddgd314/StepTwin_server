# StepTwin Android API Contract

Android only needs this file.

## Server

Use the backend laptop LAN IP. Do not use `localhost` from Android.

```text
Base URL: http://172.30.1.66:8000
```

Android manifest requirements:

```xml
<uses-permission android:name="android.permission.INTERNET" />
```

For local HTTP testing:

```xml
<application android:usesCleartextTraffic="true">
</application>
```

## 1. Health Check

```http
GET /api/v1/health
```

Proceed when HTTP status is `200` and top-level `status` is `ok`.

## 2. Route Preview

This is the main Android endpoint.

```http
POST /api/v1/routes/preview
Content-Type: application/json
```

Full URL:

```text
http://172.30.1.66:8000/api/v1/routes/preview
```

The backend returns a full renderable route:

- first walking segment
- one or more public-transit segments from TMAP
- transfer walking segments when TMAP uses multiple transit legs
- last walking segment

For long routes such as Seoul Station to Hoegi Station, Android must call this endpoint, not
`/api/v1/walk-routes/optimize`.

### Request

```json
{
  "origin": {
    "name": "Seoul Station",
    "coordinate": {
      "latitude": 37.5546788,
      "longitude": 126.9706069
    }
  },
  "destination": {
    "name": "Hoegi Station",
    "coordinate": {
      "latitude": 37.589802,
      "longitude": 127.057936
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

Required request fields:

| Field | Type | Required |
| --- | --- | --- |
| `origin.name` | string | yes |
| `origin.coordinate.latitude` | number | yes |
| `origin.coordinate.longitude` | number | yes |
| `destination.name` | string | yes |
| `destination.coordinate.latitude` | number | yes |
| `destination.coordinate.longitude` | number | yes |
| `preferences` | object | no |

Coordinates are always WGS84:

```text
latitude, longitude
```

Do not swap latitude and longitude when creating Kakao Map coordinates.

### Success Response

HTTP status: `200`

```json
{
  "route_id": "36e221a6-f9d9-48a3-8ef2-ad0c80fcbf8d",
  "summary": {
    "total_distance_meters": 10125,
    "total_duration_seconds": 1920,
    "walking_distance_meters": 731,
    "transit_distance_meters": 9394,
    "shade_shelters": 0,
    "stairs_avoided": 0
  },
  "segments": [
    {
      "id": "walk-first-mile",
      "kind": "custom_walk",
      "mode": "walk",
      "title": "Stair-minimized first mile",
      "geometry": [
        { "latitude": 37.5546788, "longitude": 126.9706069 },
        { "latitude": 37.5559556, "longitude": 126.972275 }
      ],
      "render": {
        "color": "#16A34A",
        "width": 6,
        "pattern": "dashed"
      },
      "metrics": {
        "distance_meters": 180,
        "duration_seconds": 156,
        "shade_shelters": 0,
        "stairs_avoided": 0
      },
      "transit": null
    },
    {
      "id": "transit-1",
      "kind": "transit",
      "mode": "subway",
      "title": "Ride 수도권1호선",
      "geometry": [
        { "latitude": 37.5559556, "longitude": 126.972275 },
        { "latitude": 37.5898083, "longitude": 127.0579528 }
      ],
      "render": {
        "color": "#0052A4",
        "width": 7,
        "pattern": "solid"
      },
      "metrics": {
        "distance_meters": 9394,
        "duration_seconds": 1320,
        "shade_shelters": 0,
        "stairs_avoided": 0
      },
      "transit": {
        "mode": "subway",
        "route_name": "수도권1호선",
        "bus_number": null,
        "subway_line": "수도권1호선",
        "boarding_stop": "서울역",
        "alighting_stop": "회기",
        "headsign": "회기"
      }
    }
  ],
  "markers": [
    {
      "id": "origin",
      "kind": "origin",
      "title": "Seoul Station",
      "coordinate": { "latitude": 37.5546788, "longitude": 126.9706069 },
      "segment_id": null,
      "icon": "origin"
    }
  ],
  "viewport": {
    "southwest": { "latitude": 37.5546788, "longitude": 126.9706069 },
    "northeast": { "latitude": 37.5898083, "longitude": 127.0579528 }
  },
  "debug": {
    "macro_router": "live-tmap-adapter",
    "micro_router": "mixed-pgrouting-demo-pedestrian-router",
    "tmap_live_sync": true,
    "note": "Debug text."
  }
}
```

Android must use:

| Field | Android usage |
| --- | --- |
| `segments[]` | Draw one polyline per segment. |
| `segments[].title` | Show the route instruction, for example `지하철 수도권1호선: 서울역 -> 회기`. |
| `segments[].geometry` | Ordered route coordinates. |
| `segments[].render.color` | Polyline color. Use this exactly. |
| `segments[].render.width` | Polyline width. |
| `segments[].render.pattern` | `solid` or `dashed`. |
| `segments[].transit.mode` | Distinguish `bus` from `subway`. |
| `segments[].transit.route_name` | Backward-compatible route display name. |
| `segments[].transit.bus_number` | Bus number when `mode = bus`; otherwise `null`. |
| `segments[].transit.subway_line` | Subway line when `mode = subway`; otherwise `null`. |
| `markers[]` | Draw route markers. |
| `markers[].title` | Show boarding/alighting text, for example `탑승: 지하철 수도권1호선 (서울역)`. |
| `markers[].icon` | Use `bus-stop` or `subway-stop` when available. |
| `viewport` | Fit map camera. |
| `summary` | Show total distance/time. |

Rendering rules:

Important: `segments[]` can contain multiple transit legs. Mixed bus/subway routes are returned as
separate ordered segments such as `transit-1`, `walk-transfer-1`, and `transit-2`. Android must not
look for a fixed `transit-main` ID.

1. Draw every `segments[]` item separately.
2. Walking segments are usually green dashed lines.
3. Transit segments are solid lines. For TMAP subway/bus, use `segment.render.color`.
4. For Seoul Station to Hoegi Station, the transit segment should be `수도권1호선` and visually
   different from walking segments.
5. Do not merge all segment geometry into one same-color polyline.

Known segment kinds:

| kind | Meaning |
| --- | --- |
| `custom_walk` | Custom walking segment. |
| `transit` | TMAP public-transit segment. |

Known marker icons:

| icon | Meaning |
| --- | --- |
| `origin` | Start point |
| `destination` | End point |
| `transit-stop` | Boarding/alighting stop |
| `bus-stop` | Bus boarding/alighting stop |
| `subway-stop` | Subway boarding/alighting stop |
| `parasol` | Shade shelter |
| `tree` | Tree shade |
| `stairs-off` | Avoided stairs |

## 3. Walking-Only Endpoint

This endpoint is not the main Android endpoint for long-distance routing:

```http
POST /api/v1/walk-routes/optimize
```

Use it only for a pure walking route between nearby points. It does not call TMAP and will not choose
subway or bus.

## Error Handling

| HTTP status | Meaning | Android behavior |
| --- | --- | --- |
| `200` | Response is renderable. | Draw route. |
| `404` | Walking graph cannot connect a walking sub-route. | Show route-not-found state. |
| `422` | Invalid request shape or invalid coordinate/preference range. | Treat as input error. |
| `500`/`503` | Backend/database/TMAP issue. | Show temporary backend error. |

Android should ignore unknown JSON fields.
