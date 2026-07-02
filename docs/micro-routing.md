# Micro Routing Design

StepTwin micro routing is a pedestrian-only weighted graph problem. The goal is not just shortest
distance; each road edge needs a per-user cost based on accessibility risk and comfort.

## Engine Judgment

OSRM is useful for high-throughput shortest-path routing over OSM, but it is not the best primary
engine for StepTwin's user-specific weights. OSRM profiles are designed around preprocessing and
relatively stable costs. Running many per-user combinations for stairs, slopes, corners, and shade
would require either many profiles or a separate post-processing layer.

pgRouting is a better fit for the main production engine because edge cost can be calculated per
request in SQL:

```sql
cost =
  base_walk_seconds
  + stairs_count * :stair_penalty
  + distance_meters * slope_grade * :slope_penalty
  + corner_count * :corner_penalty
  - base_walk_seconds * shade_score * :shade_reward
```

The recommended production shape is:

1. Import OSM pedestrian edges into PostGIS.
2. Attach public-data features to edges: shade shelters, trees, stairs, crossings, slopes, curb cuts.
3. Store derived attributes per edge.
4. Run pgRouting shortest path with request-specific `cost`.
5. Return ordered WGS84 coordinates to the existing StepTwin response contract.

The OSM graph import and table shape are documented in:

```text
docs/osm-walk-network.md
```

## License Check

pgRouting is open source and free to use, but its project license is GPL-2.0. For StepTwin, the
practical approach is to run PostgreSQL/PostGIS/pgRouting as server infrastructure and keep Android
calling only the StepTwin API.

Compliance notes:

- Do not embed pgRouting into Android.
- Keep pgRouting as a backend database extension.
- Track the exact package/source used in deployment notes.
- If distributing a backend appliance/image externally, review GPL obligations before release.

For hackathon/server-only operation, pgRouting is acceptable from a cost perspective. It is not a
paid routing API.

## AI and GA Feasibility

The StepTwin optimization target is the walking route itself. The transit itinerary from TMAP is
fixed once selected; optimization happens only on:

- Origin to first stop
- Transfer walking segments
- Last stop to destination

A genetic algorithm should not be used to tune request parameters such as `stair_weight` or
`shade_weight`. Those weights represent the user's declared condition and should remain stable for a
request.

If another AI module uses GA, it must optimize feasible walking-route choices on the pedestrian
network:

1. Keep TMAP transit legs fixed.
2. Generate or mutate only valid pedestrian graph paths between fixed walking endpoints.
3. Score candidate paths by stairs, slope, corner load, shade exposure, and distance.
4. Select the best walking geometry while respecting `max_extra_walk_ratio`.

This is reserved for a separate AI component. The backend must not silently change user preference
weights.

Do not use GA to mutate arbitrary latitude/longitude polylines directly. That can create paths that
cut through buildings or leave the pedestrian network.

## Current Implementation

The current backend implements the same cost model with an in-process Dijkstra router. It builds a
small synthetic graph for each walking segment so the API already supports:

- Stair penalty
- Shade reward
- Slope penalty
- Corner penalty
- Walking speed
- Maximum detour guard

This is intentionally replaceable. The caller still uses:

```python
build_custom_walk(segment_id, start, end, title, preferences)
```

The future pgRouting adapter should return the same `WalkingRoute` shape.
