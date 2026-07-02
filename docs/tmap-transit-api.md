# TMAP Transit Route API Notes

This document records the TMAP public transit route API details confirmed during integration.

## Current Status

The request URL, method, headers, and request body options are confirmed. Response parsing rules
will be added after the response sample is provided.

## Endpoint

```http
POST https://apis.openapi.sk.com/transit/routes
```

Backend `.env` mapping:

```env
TMAP_BASE_URL="https://apis.openapi.sk.com"
TMAP_TRANSIT_PATH="/transit/routes"
```

## Headers

```http
Accept: application/json
Content-Type: application/json
appKey: <TMAP_APP_KEY>
```

`Accept` can be `application/json` or `application/xml`. The backend default is JSON.

The real appKey must be stored only in local `.env`:

```env
TMAP_APP_KEY=""
```

Do not put the TMAP appKey in Android code.

## Example Request

Transit route from `Sinchon Station Line 2` to `Sangwangsimni Station Line 2`.

```bash
curl --request POST \
  --url 'https://apis.openapi.sk.com/transit/routes' \
  --header 'Accept: application/json' \
  --header 'Content-Type: application/json' \
  --header 'appKey: {issued appKey}' \
  --data '{
    "startX": "126.936928",
    "startY": "37.555162",
    "endX": "127.029281",
    "endY": "37.564436",
    "lang": 0,
    "format": "json",
    "count": 10
  }'
```

## Request Body Parameters

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `startX` | string | yes | - | Origin longitude in WGS84. |
| `startY` | string | yes | - | Origin latitude in WGS84. |
| `endX` | string | yes | - | Destination longitude in WGS84. |
| `endY` | string | yes | - | Destination latitude in WGS84. |
| `lang` | integer | no | `0` | `0` for Korean, `1` for English. |
| `format` | string | no | `json` | `json` or `xml`. |
| `count` | integer | no | `10` | Maximum number of route results, from `1` to `10`. |
| `searchDttm` | string | no | omitted | Time-machine search timestamp in `yyyymmddhhmi`. |

`searchDttm` range rules:

- Year: `1900` to `9999`
- Month: `01` to `12`
- Day: `01` to `31`
- Hour: `00` to `23`
- Minute: `00` to `59`

## Backend Request Mapping

The backend sends coordinates to TMAP as:

```json
{
  "startX": "<origin longitude>",
  "startY": "<origin latitude>",
  "endX": "<destination longitude>",
  "endY": "<destination latitude>",
  "lang": 0,
  "format": "json",
  "count": 10
}
```

Important:

- TMAP request uses `X = longitude`.
- TMAP request uses `Y = latitude`.
- StepTwin API response to Android still uses `{ "latitude": ..., "longitude": ... }`.
- TMAP coordinate values are sent as strings to match the confirmed curl example.
- `searchDttm` is sent only when `TMAP_SEARCH_DTTM` is configured.

## Backend Environment Mapping

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

## Pending

To complete live TMAP integration, add:

- Real response sample captured from TMAP.
- Error response format and retry/fallback policy.

## Response Mapping

The backend reads JSON responses from:

```text
metaData.plan.itineraries[]
```

For the current StepTwin preview contract, the backend chooses the first itinerary that contains at
least one supported transit leg.

Supported `legs[].mode` values:

| TMAP mode | StepTwin mode |
| --- | --- |
| `SUBWAY` | `subway` |
| `BUS` | `bus` |
| `EXPRESS BUS` | `bus` |

`WALK`, `TRAIN`, `AIRPLANE`, and `FERRY` are not used as the primary transit segment in the current
PoC response shape.

Transit leg mapping:

| StepTwin field | TMAP source |
| --- | --- |
| `boarding_stop.name` | first transit `legs[].start.name` |
| `boarding_stop.coordinate` | first transit `legs[].start.lat/lon` |
| `alighting_stop.name` | last transit `legs[].end.name` |
| `alighting_stop.coordinate` | last transit `legs[].end.lat/lon` |
| `transit.route_name` | unique transit `legs[].route` values joined with ` + ` |
| `transit.mode` | first supported transit `legs[].mode` |
| `geometry` | transit `legs[].passShape.linestring`, with leg end coordinates as fallback points |
| `distance_meters` | sum of transit `legs[].distance`; falls back to geometry distance |
| `duration_seconds` | sum of transit `legs[].sectionTime`; falls back to estimated transit time |
