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

Call this before route requests.

```http
GET /api/v1/health
```

Success:

```json
{
  "status": "ok",
  "service": "Steptwin API",
  "environment": "local",
  "version": "0.1.0",
  "timestamp": "2026-07-02T13:00:00Z",
  "checks": {
    "application": { "status": "ok", "detail": null },
    "database": { "status": "ok", "detail": null }
  }
}
```

Proceed when HTTP status is `200` and `status` is `ok`.

## 2. Custom Walking Route

This is the main endpoint for custom walking route search.

```http
POST /api/v1/walk-routes/optimize
Content-Type: application/json
```

Full URL:

```text
http://172.30.1.66:8000/api/v1/walk-routes/optimize
```

### Request

Android sends start, end, and optional user preferences.

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

Required request fields:

| Field | Type | Required |
| --- | --- | --- |
| `start.name` | string | yes |
| `start.coordinate.latitude` | number | yes |
| `start.coordinate.longitude` | number | yes |
| `end.name` | string | yes |
| `end.coordinate.latitude` | number | yes |
| `end.coordinate.longitude` | number | yes |
| `preferences` | object | no |

Preference defaults if omitted:

| Field | Default | Range |
| --- | --- | --- |
| `avoid_stairs` | `true` | boolean |
| `shade_weight` | `0.8` | `0` to `1` |
| `stair_weight` | `1.0` | `0` to `3` |
| `slope_weight` | `0.7` | `0` to `3` |
| `corner_weight` | `0.4` | `0` to `3` |
| `walking_speed_mps` | `1.15` | `> 0` to `2.5` |
| `max_extra_walk_ratio` | `0.2` | `0` to `1` |

Coordinates are always WGS84:

```text
latitude, longitude
```

Do not swap latitude and longitude when creating Kakao Map coordinates.

### Success Response

HTTP status: `200`

```json
{
  "route_kind": "weighted",
  "start": {
    "vertex_id": 1976,
    "coordinate": {
      "latitude": 37.58945,
      "longitude": 127.05775
    },
    "snap_distance_meters": 0.0
  },
  "end": {
    "vertex_id": 99467,
    "coordinate": {
      "latitude": 37.59375,
      "longitude": 127.05158
    },
    "snap_distance_meters": 0.0
  },
  "geometry": [
    { "latitude": 37.58945, "longitude": 127.05775 },
    { "latitude": 37.5901, "longitude": 127.0568 },
    { "latitude": 37.59375, "longitude": 127.05158 }
  ],
  "metrics": {
    "total_cost_seconds": 872.55,
    "total_distance_meters": 1003,
    "duration_seconds": 872,
    "stairs_count": 0,
    "shade_shelters": 0
  },
  "steps": [
    {
      "path_seq": 1,
      "node_id": 1,
      "edge_id": 210574,
      "cost_seconds": 40.2,
      "agg_cost_seconds": 0.0,
      "distance_meters": 46.2,
      "stairs_count": 0,
      "shade_score": 0.0,
      "corner_count": 0,
      "slope_grade": 0.0,
      "geometry": [
        { "latitude": 37.58945, "longitude": 127.05775 },
        { "latitude": 37.5901, "longitude": 127.0568 }
      ]
    }
  ]
}
```

Android must use:

| Field | Android usage |
| --- | --- |
| `geometry` | Draw this as the route polyline. |
| `metrics.total_distance_meters` | Show walking distance. |
| `metrics.duration_seconds` | Show walking time. |
| `metrics.stairs_count` | Show stairs count if needed. |
| `metrics.shade_shelters` | Show shade count if needed. |
| `start.coordinate` | Optional snapped start marker. |
| `end.coordinate` | Optional snapped end marker. |
| `steps` | Optional debug/detail data. Not needed for first map drawing. |

Recommended map drawing:

| Item | Value |
| --- | --- |
| Route line color | `#16A34A` |
| Route line width | `6` |
| Start marker | Existing Android start marker |
| End marker | Existing Android end marker |
| Camera | Fit all points in `geometry` |

## Error Responses

### No Route

HTTP status: `404`

```json
{
  "detail": "No pedestrian path from vertex 1976 to vertex 99467"
}
```

Android behavior:

- Keep start and end markers visible.
- Do not draw a route polyline.
- Show a route-not-found message.
- This can happen while the walking network data is still being improved.

### Invalid Request

HTTP status: `422`

Usually means a missing field, invalid latitude/longitude, or preference value outside its allowed
range.

Android behavior:

- Treat as input/request error.
- Check request JSON shape and coordinate order.

### Backend Error

HTTP status: `500` or `503`

Android behavior:

- Show temporary backend error.
- User may retry later.

## Android Implementation Checklist

1. Call `GET /api/v1/health`.
2. Let user pick start and end.
3. Send `POST /api/v1/walk-routes/optimize`.
4. If `200`, draw `geometry` as one Kakao Map polyline.
5. If `404`, show start/end markers and route-not-found state.
6. Fit camera to route geometry when a route exists.
7. Ignore unknown JSON fields.
