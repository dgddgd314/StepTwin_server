from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from steptwin_api.schemas.routing import Coordinate, RoutingPreferences
from steptwin_api.services.geometry import distance_meters

PgRoutingExecutor = AsyncSession | AsyncConnection
PgRoutingRouteKind = Literal["weighted", "shortest_fallback", "same_vertex"]

_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PgRoutingError(RuntimeError):
    """Base error for pgRouting pedestrian route calculation."""


class PgRoutingNoPathError(PgRoutingError):
    """Raised when pgRouting cannot find a graph path between the snapped vertices."""


class PgRoutingSnapError(PgRoutingError):
    """Raised when either endpoint cannot be snapped to a pedestrian graph vertex."""


@dataclass(frozen=True, slots=True)
class PgRoutingGraphConfig:
    edge_table: str = "osm_pedestrian_edges"
    vertex_table: str = "osm_pedestrian_vertices"
    edge_id_column: str = "id"
    source_column: str = "source"
    target_column: str = "target"
    edge_geom_column: str = "geom"
    vertex_id_column: str = "id"
    vertex_geom_column: str = "geom"
    distance_column: str = "distance_meters"
    stairs_column: str = "stairs_count"
    shade_column: str = "shade_score"
    corner_column: str = "corner_count"
    slope_column: str = "slope_grade"
    crowding_column: str = "crowding_score"
    crossing_type_column: str = "crossing_type"
    crossing_wait_seconds_column: str = "crossing_wait_seconds"
    graph_srid: int = 4326
    shade_marker_threshold: float = 0.45


DEFAULT_PGROUTING_GRAPH_CONFIG = PgRoutingGraphConfig()


@dataclass(frozen=True, slots=True)
class PgRoutingCostProfile:
    walking_speed_mps: float
    stair_penalty_seconds_per_count: float
    slope_penalty_seconds_per_meter_grade: float
    corner_penalty_seconds_per_count: float
    crowding_penalty_fraction: float
    shade_reward_fraction: float
    min_cost_fraction_of_base: float
    max_extra_walk_ratio: float

    @classmethod
    def from_preferences(cls, preferences: RoutingPreferences) -> PgRoutingCostProfile:
        stair_penalty = 240 * preferences.stair_weight
        if preferences.avoid_stairs:
            stair_penalty += 360

        return cls(
            walking_speed_mps=preferences.walking_speed_mps,
            stair_penalty_seconds_per_count=stair_penalty,
            slope_penalty_seconds_per_meter_grade=24.0 * preferences.slope_weight,
            corner_penalty_seconds_per_count=18 * preferences.corner_weight,
            crowding_penalty_fraction=0.6 * preferences.crowding_weight,
            shade_reward_fraction=0.35 * preferences.shade_weight,
            min_cost_fraction_of_base=0.35,
            max_extra_walk_ratio=preferences.max_extra_walk_ratio,
        )


@dataclass(frozen=True, slots=True)
class PgRoutingSnappedEndpoint:
    vertex_id: int
    coordinate: Coordinate
    snap_distance_meters: float


@dataclass(frozen=True, slots=True)
class PgRoutingRouteStep:
    path_seq: int
    node_id: int
    edge_id: int
    cost_seconds: float
    agg_cost_seconds: float
    distance_meters: float
    stairs_count: int
    shade_score: float
    corner_count: int
    slope_grade: float
    crowding_score: float
    geometry: tuple[Coordinate, ...]


@dataclass(frozen=True, slots=True)
class PgRoutingPedestrianRoute:
    geometry: tuple[Coordinate, ...]
    steps: tuple[PgRoutingRouteStep, ...]
    total_cost_seconds: float
    total_distance_meters: int
    duration_seconds: int
    stairs_count: int
    shade_shelters: int
    route_kind: PgRoutingRouteKind
    start: PgRoutingSnappedEndpoint
    end: PgRoutingSnappedEndpoint


async def find_pgrouting_walk_route(
    executor: PgRoutingExecutor,
    start: Coordinate,
    end: Coordinate,
    preferences: RoutingPreferences,
    *,
    graph_config: PgRoutingGraphConfig = DEFAULT_PGROUTING_GRAPH_CONFIG,
) -> PgRoutingPedestrianRoute:
    """Return the user-weighted pedestrian route between two WGS84 coordinates.

    This is the hot-path pgRouting boundary. Callers pass an existing async SQLAlchemy session or
    connection so route calculation does not create engines, sessions, or HTTP response wrappers.
    """

    validate_graph_config(graph_config)
    cost_profile = PgRoutingCostProfile.from_preferences(preferences)
    snapped_start, snapped_end = await snap_route_endpoints(
        executor,
        start,
        end,
        graph_config=graph_config,
    )

    if snapped_start.vertex_id == snapped_end.vertex_id:
        return build_same_vertex_route(
            start=start,
            end=end,
            snapped_start=snapped_start,
            snapped_end=snapped_end,
            walking_speed_mps=cost_profile.walking_speed_mps,
        )

    query = build_pgrouting_route_query(graph_config)
    params = {
        "weighted_edges_sql": build_weighted_edges_sql(cost_profile, graph_config),
        "shortest_edges_sql": build_shortest_edges_sql(graph_config),
        "start_vertex_id": snapped_start.vertex_id,
        "end_vertex_id": snapped_end.vertex_id,
        "max_extra_walk_ratio": cost_profile.max_extra_walk_ratio,
    }
    result = await executor.execute(text(query), params)
    rows = cast(Sequence[Mapping[str, object]], result.mappings().all())

    return build_route_from_rows(
        rows,
        requested_start=start,
        requested_end=end,
        snapped_start=snapped_start,
        snapped_end=snapped_end,
        walking_speed_mps=cost_profile.walking_speed_mps,
        shade_marker_threshold=graph_config.shade_marker_threshold,
    )


async def snap_route_endpoints(
    executor: PgRoutingExecutor,
    start: Coordinate,
    end: Coordinate,
    *,
    graph_config: PgRoutingGraphConfig = DEFAULT_PGROUTING_GRAPH_CONFIG,
) -> tuple[PgRoutingSnappedEndpoint, PgRoutingSnappedEndpoint]:
    validate_graph_config(graph_config)
    result = await executor.execute(
        text(build_snap_endpoints_query(graph_config)),
        {
            "start_longitude": start.longitude,
            "start_latitude": start.latitude,
            "end_longitude": end.longitude,
            "end_latitude": end.latitude,
        },
    )
    rows = cast(Sequence[Mapping[str, object]], result.mappings().all())
    endpoints: dict[str, PgRoutingSnappedEndpoint] = {}
    for row in rows:
        endpoint_kind = row.get("endpoint_kind")
        if endpoint_kind in {"start", "end"}:
            endpoints[str(endpoint_kind)] = parse_snapped_endpoint(row)

    try:
        return endpoints["start"], endpoints["end"]
    except KeyError as exc:
        message = "Could not snap start and end coordinates to graph vertices"
        raise PgRoutingSnapError(message) from exc


def build_snap_endpoints_query(graph_config: PgRoutingGraphConfig) -> str:
    edge_table = quote_qualified_identifier(graph_config.edge_table)
    vertex_table = quote_qualified_identifier(graph_config.vertex_table)
    source = quote_identifier(graph_config.source_column)
    target = quote_identifier(graph_config.target_column)
    vertex_id = quote_identifier(graph_config.vertex_id_column)
    vertex_geom = quote_identifier(graph_config.vertex_geom_column)
    graph_srid = checked_srid(graph_config.graph_srid)

    graph_point_expression = (
        "point.wgs84_geom"
        if graph_srid == 4326
        else f"ST_Transform(point.wgs84_geom, {graph_srid})"
    )

    return f"""
WITH input_points(endpoint_kind, longitude, latitude) AS (
    VALUES
        ('start', CAST(:start_longitude AS float8), CAST(:start_latitude AS float8)),
        ('end', CAST(:end_longitude AS float8), CAST(:end_latitude AS float8))
),
projected_points AS (
    SELECT
        endpoint_kind,
        longitude,
        latitude,
        ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) AS wgs84_geom
    FROM input_points
)
SELECT
    point.endpoint_kind,
    vertex.{vertex_id}::bigint AS vertex_id,
    ST_AsGeoJSON(ST_Transform(vertex.{vertex_geom}, 4326)) AS coordinate_geojson,
    ST_Distance(
        ST_Transform(vertex.{vertex_geom}, 4326)::geography,
        point.wgs84_geom::geography
    )::float8 AS snap_distance_meters
FROM projected_points AS point
CROSS JOIN LATERAL (
    SELECT candidate.{vertex_id}, candidate.{vertex_geom}
    FROM {vertex_table} AS candidate
    WHERE EXISTS (
        SELECT 1
        FROM {edge_table} AS edge
        WHERE edge.{source} = candidate.{vertex_id}
            OR edge.{target} = candidate.{vertex_id}
    )
    ORDER BY candidate.{vertex_geom} <-> {graph_point_expression}
    LIMIT 1
) AS vertex
ORDER BY point.endpoint_kind
""".strip()


def build_pgrouting_route_query(graph_config: PgRoutingGraphConfig) -> str:
    edge_table = quote_qualified_identifier(graph_config.edge_table)
    edge_id = quote_identifier(graph_config.edge_id_column)
    source = quote_identifier(graph_config.source_column)
    target = quote_identifier(graph_config.target_column)
    edge_geom = quote_identifier(graph_config.edge_geom_column)
    distance = quote_identifier(graph_config.distance_column)
    stairs = quote_identifier(graph_config.stairs_column)
    shade = quote_identifier(graph_config.shade_column)
    corners = quote_identifier(graph_config.corner_column)
    slope = quote_identifier(graph_config.slope_column)
    crowding = quote_identifier(graph_config.crowding_column)

    return f"""
WITH
weighted_path AS (
    SELECT *
    FROM pgr_dijkstra(
        :weighted_edges_sql,
        CAST(:start_vertex_id AS bigint),
        CAST(:end_vertex_id AS bigint),
        true
    )
),
weighted_stats AS (
    SELECT
        COUNT(edge.{edge_id})::integer AS step_count,
        COALESCE(SUM(edge.{distance}), 0)::float8 AS distance_meters
    FROM weighted_path AS path
    JOIN {edge_table} AS edge ON edge.{edge_id} = path.edge
    WHERE path.edge <> -1
),
shortest_path AS (
    SELECT *
    FROM pgr_dijkstra(
        :shortest_edges_sql,
        CAST(:start_vertex_id AS bigint),
        CAST(:end_vertex_id AS bigint),
        true
    )
),
shortest_stats AS (
    SELECT
        COUNT(edge.{edge_id})::integer AS step_count,
        COALESCE(SUM(edge.{distance}), 0)::float8 AS distance_meters
    FROM shortest_path AS path
    JOIN {edge_table} AS edge ON edge.{edge_id} = path.edge
    WHERE path.edge <> -1
),
route_choice AS (
    SELECT CASE
        WHEN weighted_stats.step_count = 0 AND shortest_stats.step_count = 0 THEN 'none'
        WHEN weighted_stats.step_count = 0 THEN 'shortest_fallback'
        WHEN shortest_stats.step_count = 0 THEN 'weighted'
        WHEN weighted_stats.distance_meters
            <= shortest_stats.distance_meters * (1 + CAST(:max_extra_walk_ratio AS float8))
            THEN 'weighted'
        ELSE 'shortest_fallback'
    END AS route_kind
    FROM weighted_stats
    CROSS JOIN shortest_stats
),
selected_path AS (
    SELECT route_choice.route_kind, path.*
    FROM weighted_path AS path
    CROSS JOIN route_choice
    WHERE route_choice.route_kind = 'weighted'
    UNION ALL
    SELECT route_choice.route_kind, path.*
    FROM shortest_path AS path
    CROSS JOIN route_choice
    WHERE route_choice.route_kind = 'shortest_fallback'
)
SELECT
    selected_path.route_kind,
    selected_path.path_seq::integer AS path_seq,
    selected_path.node::bigint AS node_id,
    selected_path.edge::bigint AS edge_id,
    selected_path.cost::float8 AS cost_seconds,
    selected_path.agg_cost::float8 AS agg_cost_seconds,
    edge.{source}::bigint AS edge_source,
    edge.{target}::bigint AS edge_target,
    edge.{distance}::float8 AS distance_meters,
    COALESCE(edge.{stairs}, 0)::integer AS stairs_count,
    LEAST(GREATEST(COALESCE(edge.{shade}, 0), 0), 1)::float8 AS shade_score,
    COALESCE(edge.{corners}, 0)::integer AS corner_count,
    GREATEST(COALESCE(edge.{slope}, 0), 0)::float8 AS slope_grade,
    LEAST(GREATEST(COALESCE(edge.{crowding}, 0), 0), 1)::float8 AS crowding_score,
    ST_AsGeoJSON(
        CASE
            WHEN selected_path.node = edge.{source}
                THEN ST_Transform(edge.{edge_geom}, 4326)
            ELSE ST_Reverse(ST_Transform(edge.{edge_geom}, 4326))
        END
    ) AS geometry_geojson
FROM selected_path
JOIN {edge_table} AS edge ON edge.{edge_id} = selected_path.edge
WHERE selected_path.edge <> -1
ORDER BY selected_path.path_seq
""".strip()


def build_weighted_edges_sql(
    cost_profile: PgRoutingCostProfile,
    graph_config: PgRoutingGraphConfig = DEFAULT_PGROUTING_GRAPH_CONFIG,
) -> str:
    validate_graph_config(graph_config)
    edge_table = quote_qualified_identifier(graph_config.edge_table)
    edge_id = quote_identifier(graph_config.edge_id_column)
    source = quote_identifier(graph_config.source_column)
    target = quote_identifier(graph_config.target_column)
    distance = quote_identifier(graph_config.distance_column)
    stairs = quote_identifier(graph_config.stairs_column)
    shade = quote_identifier(graph_config.shade_column)
    corners = quote_identifier(graph_config.corner_column)
    slope = quote_identifier(graph_config.slope_column)
    crowding = quote_identifier(graph_config.crowding_column)
    crossing_type = quote_identifier(graph_config.crossing_type_column)
    crossing_wait = quote_identifier(graph_config.crossing_wait_seconds_column)

    walking_speed = checked_sql_float(cost_profile.walking_speed_mps, "walking_speed_mps")
    stair_penalty = checked_sql_float(
        cost_profile.stair_penalty_seconds_per_count,
        "stair_penalty_seconds_per_count",
    )
    slope_penalty = checked_sql_float(
        cost_profile.slope_penalty_seconds_per_meter_grade,
        "slope_penalty_seconds_per_meter_grade",
    )
    corner_penalty = checked_sql_float(
        cost_profile.corner_penalty_seconds_per_count,
        "corner_penalty_seconds_per_count",
    )
    crowding_penalty = checked_sql_float(
        cost_profile.crowding_penalty_fraction,
        "crowding_penalty_fraction",
    )
    shade_reward = checked_sql_float(cost_profile.shade_reward_fraction, "shade_reward_fraction")
    min_cost_fraction = checked_sql_float(
        cost_profile.min_cost_fraction_of_base,
        "min_cost_fraction_of_base",
    )

    base_seconds = f"(edge.{distance}::float8 / {walking_speed})"
    weighted_cost = f"""
GREATEST(
    {base_seconds} * {min_cost_fraction},
    {base_seconds}
        + GREATEST(COALESCE(edge.{stairs}, 0), 0)::float8 * {stair_penalty}
        + edge.{distance}::float8
            * GREATEST(COALESCE(edge.{slope}, 0), 0)::float8
            * {slope_penalty}
        + GREATEST(COALESCE(edge.{corners}, 0), 0)::float8 * {corner_penalty}
        + {base_seconds}
            * LEAST(GREATEST(COALESCE(edge.{crowding}, 0), 0), 1)::float8
            * {crowding_penalty}
        + CASE
            WHEN COALESCE(edge.{crossing_type}, 'none') <> 'none'
                THEN GREATEST(COALESCE(edge.{crossing_wait}, 0), 0)::float8
            ELSE 0
        END
        - {base_seconds}
            * LEAST(GREATEST(COALESCE(edge.{shade}, 0), 0), 1)::float8
            * {shade_reward}
)""".strip()

    return f"""
SELECT
    edge.{edge_id}::bigint AS id,
    edge.{source}::bigint AS source,
    edge.{target}::bigint AS target,
    {weighted_cost}::float8 AS cost,
    {weighted_cost}::float8 AS reverse_cost
FROM {edge_table} AS edge
WHERE edge.{source} IS NOT NULL
    AND edge.{target} IS NOT NULL
    AND edge.{distance} IS NOT NULL
    AND edge.{distance} > 0
""".strip()


def build_shortest_edges_sql(
    graph_config: PgRoutingGraphConfig = DEFAULT_PGROUTING_GRAPH_CONFIG,
) -> str:
    validate_graph_config(graph_config)
    edge_table = quote_qualified_identifier(graph_config.edge_table)
    edge_id = quote_identifier(graph_config.edge_id_column)
    source = quote_identifier(graph_config.source_column)
    target = quote_identifier(graph_config.target_column)
    distance = quote_identifier(graph_config.distance_column)

    return f"""
SELECT
    edge.{edge_id}::bigint AS id,
    edge.{source}::bigint AS source,
    edge.{target}::bigint AS target,
    edge.{distance}::float8 AS cost,
    edge.{distance}::float8 AS reverse_cost
FROM {edge_table} AS edge
WHERE edge.{source} IS NOT NULL
    AND edge.{target} IS NOT NULL
    AND edge.{distance} IS NOT NULL
    AND edge.{distance} > 0
""".strip()


def build_route_from_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    requested_start: Coordinate,
    requested_end: Coordinate,
    snapped_start: PgRoutingSnappedEndpoint,
    snapped_end: PgRoutingSnappedEndpoint,
    walking_speed_mps: float,
    shade_marker_threshold: float,
) -> PgRoutingPedestrianRoute:
    if not rows:
        raise PgRoutingNoPathError(
            f"No pedestrian path from vertex {snapped_start.vertex_id} "
            f"to vertex {snapped_end.vertex_id}"
        )

    steps = tuple(parse_route_step(row) for row in rows)
    route_kind = parse_route_kind(rows[0]["route_kind"])
    geometry = merge_route_geometry(requested_start, requested_end, steps)
    total_distance = round(sum(step.distance_meters for step in steps))
    stairs_count = sum(step.stairs_count for step in steps)
    shade_shelters = sum(1 for step in steps if step.shade_score >= shade_marker_threshold)

    return PgRoutingPedestrianRoute(
        geometry=geometry,
        steps=steps,
        total_cost_seconds=sum(step.cost_seconds for step in steps),
        total_distance_meters=total_distance,
        duration_seconds=estimate_walking_seconds(total_distance, walking_speed_mps),
        stairs_count=stairs_count,
        shade_shelters=shade_shelters,
        route_kind=route_kind,
        start=snapped_start,
        end=snapped_end,
    )


def build_same_vertex_route(
    *,
    start: Coordinate,
    end: Coordinate,
    snapped_start: PgRoutingSnappedEndpoint,
    snapped_end: PgRoutingSnappedEndpoint,
    walking_speed_mps: float,
) -> PgRoutingPedestrianRoute:
    geometry = dedupe_adjacent_coordinates((start, snapped_start.coordinate, end))
    total_distance = distance_meters(list(geometry))

    return PgRoutingPedestrianRoute(
        geometry=geometry,
        steps=(),
        total_cost_seconds=total_distance / walking_speed_mps,
        total_distance_meters=total_distance,
        duration_seconds=estimate_walking_seconds(total_distance, walking_speed_mps),
        stairs_count=0,
        shade_shelters=0,
        route_kind="same_vertex",
        start=snapped_start,
        end=snapped_end,
    )


def parse_route_step(row: Mapping[str, object]) -> PgRoutingRouteStep:
    geometry = tuple(parse_linestring_geojson(row["geometry_geojson"]))
    if len(geometry) < 2:
        raise PgRoutingError("pgRouting edge geometry must contain at least two coordinates")

    return PgRoutingRouteStep(
        path_seq=as_int(row["path_seq"], "path_seq"),
        node_id=as_int(row["node_id"], "node_id"),
        edge_id=as_int(row["edge_id"], "edge_id"),
        cost_seconds=as_float(row["cost_seconds"], "cost_seconds"),
        agg_cost_seconds=as_float(row["agg_cost_seconds"], "agg_cost_seconds"),
        distance_meters=as_float(row["distance_meters"], "distance_meters"),
        stairs_count=as_int(row["stairs_count"], "stairs_count"),
        shade_score=as_float(row["shade_score"], "shade_score"),
        corner_count=as_int(row["corner_count"], "corner_count"),
        slope_grade=as_float(row["slope_grade"], "slope_grade"),
        crowding_score=as_float(row["crowding_score"], "crowding_score"),
        geometry=geometry,
    )


def parse_snapped_endpoint(row: Mapping[str, object]) -> PgRoutingSnappedEndpoint:
    return PgRoutingSnappedEndpoint(
        vertex_id=as_int(row["vertex_id"], "vertex_id"),
        coordinate=parse_point_geojson(row["coordinate_geojson"]),
        snap_distance_meters=as_float(row["snap_distance_meters"], "snap_distance_meters"),
    )


def parse_route_kind(value: object) -> PgRoutingRouteKind:
    if value in {"weighted", "shortest_fallback", "same_vertex"}:
        return cast(PgRoutingRouteKind, value)

    raise PgRoutingError(f"Unexpected pgRouting route kind: {value!r}")


def parse_linestring_geojson(value: object) -> list[Coordinate]:
    payload = parse_geojson_payload(value)
    geometry_type = payload.get("type")
    coordinates = payload.get("coordinates")

    if geometry_type == "LineString":
        if not isinstance(coordinates, list):
            raise PgRoutingError("LineString GeoJSON coordinates must be a list")
        return [coordinate_from_lon_lat(pair) for pair in coordinates]

    if geometry_type == "MultiLineString":
        if not isinstance(coordinates, list):
            raise PgRoutingError("MultiLineString GeoJSON coordinates must be a list")
        flattened: list[Coordinate] = []
        for line in coordinates:
            if not isinstance(line, list):
                raise PgRoutingError("MultiLineString GeoJSON line must be a list")
            flattened.extend(coordinate_from_lon_lat(pair) for pair in line)
        return list(dedupe_adjacent_coordinates(flattened))

    raise PgRoutingError(f"Unsupported edge geometry GeoJSON type: {geometry_type!r}")


def parse_point_geojson(value: object) -> Coordinate:
    payload = parse_geojson_payload(value)
    if payload.get("type") != "Point":
        raise PgRoutingError(f"Expected Point GeoJSON, got {payload.get('type')!r}")

    return coordinate_from_lon_lat(payload.get("coordinates"))


def parse_geojson_payload(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        payload = cast(object, json.loads(value))
    elif isinstance(value, bytes):
        payload = cast(object, json.loads(value.decode()))
    elif isinstance(value, dict):
        payload = value
    else:
        raise PgRoutingError(f"Unsupported GeoJSON payload type: {type(value).__name__}")

    if not isinstance(payload, dict):
        raise PgRoutingError("GeoJSON payload must be an object")

    return cast(dict[str, Any], payload)


def coordinate_from_lon_lat(value: object) -> Coordinate:
    if not isinstance(value, list | tuple) or len(value) < 2:
        raise PgRoutingError("GeoJSON coordinate must be [longitude, latitude]")

    return Coordinate(
        latitude=as_float(value[1], "latitude"),
        longitude=as_float(value[0], "longitude"),
    )


def merge_route_geometry(
    requested_start: Coordinate,
    requested_end: Coordinate,
    steps: tuple[PgRoutingRouteStep, ...],
) -> tuple[Coordinate, ...]:
    coordinates: list[Coordinate] = [requested_start]
    for step in steps:
        coordinates.extend(step.geometry)
    coordinates.append(requested_end)
    return tuple(dedupe_adjacent_coordinates(coordinates))


def dedupe_adjacent_coordinates(
    coordinates: list[Coordinate] | tuple[Coordinate, ...],
) -> tuple[Coordinate, ...]:
    deduped: list[Coordinate] = []
    for coordinate in coordinates:
        if not deduped or coordinate != deduped[-1]:
            deduped.append(coordinate)

    return tuple(deduped)


def estimate_walking_seconds(distance: int, walking_speed_mps: float) -> int:
    return max(round(distance / walking_speed_mps), 60)


def validate_graph_config(graph_config: PgRoutingGraphConfig) -> None:
    quote_qualified_identifier(graph_config.edge_table)
    quote_qualified_identifier(graph_config.vertex_table)
    for column in (
        graph_config.edge_id_column,
        graph_config.source_column,
        graph_config.target_column,
        graph_config.edge_geom_column,
        graph_config.vertex_id_column,
        graph_config.vertex_geom_column,
        graph_config.distance_column,
        graph_config.stairs_column,
        graph_config.shade_column,
        graph_config.corner_column,
        graph_config.slope_column,
        graph_config.crowding_column,
        graph_config.crossing_type_column,
        graph_config.crossing_wait_seconds_column,
    ):
        quote_identifier(column)

    checked_srid(graph_config.graph_srid)
    if not 0 <= graph_config.shade_marker_threshold <= 1:
        raise ValueError("shade_marker_threshold must be between 0 and 1")


def quote_qualified_identifier(identifier: str) -> str:
    parts = identifier.split(".")
    if not 1 <= len(parts) <= 2:
        raise ValueError(f"Invalid SQL identifier: {identifier!r}")

    return ".".join(quote_identifier(part) for part in parts)


def quote_identifier(identifier: str) -> str:
    if not _SQL_IDENTIFIER_PATTERN.match(identifier):
        raise ValueError(f"Invalid SQL identifier: {identifier!r}")

    return f'"{identifier}"'


def checked_srid(value: int) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError("graph_srid must be a positive integer")

    return value


def checked_sql_float(value: float, field_name: str) -> str:
    number = as_float(value, field_name)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    if number < 0:
        raise ValueError(f"{field_name} must be non-negative")

    return format(number, ".12g")


def as_float(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PgRoutingError(f"{field_name} must be numeric")

    return float(value)


def as_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PgRoutingError(f"{field_name} must be an integer")

    return value
