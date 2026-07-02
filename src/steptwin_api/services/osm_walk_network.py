from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from steptwin_api.schemas.routing import Coordinate
from steptwin_api.services.geometry import distance_meters

OSM_VERTEX_TABLE = "osm_pedestrian_vertices"
OSM_EDGE_TABLE = "osm_pedestrian_edges"
OSM_IMPORT_BATCH_SIZE = 5000
OSM_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

WalkableHighway = Literal[
    "footway",
    "pedestrian",
    "path",
    "steps",
    "living_street",
    "residential",
    "service",
    "unclassified",
    "tertiary",
    "secondary",
    "primary",
    "cycleway",
    "track",
]

WALKABLE_HIGHWAYS: set[WalkableHighway] = {
    "footway",
    "pedestrian",
    "path",
    "steps",
    "living_street",
    "residential",
    "service",
    "unclassified",
    "tertiary",
    "secondary",
    "primary",
    "cycleway",
    "track",
}
BLOCKED_VALUES = {"no", "private", "permissive_no"}


class OsmWalkNetworkError(RuntimeError):
    """Raised when OSM walking network data cannot be fetched or parsed."""


@dataclass(frozen=True, slots=True)
class OsmNode:
    id: int
    latitude: float
    longitude: float
    tags: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class OsmWay:
    id: int
    node_ids: tuple[int, ...]
    tags: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class OsmWalkDataset:
    name: str
    bbox: tuple[float, float, float, float]
    nodes: tuple[OsmNode, ...]
    ways: tuple[OsmWay, ...]


@dataclass(frozen=True, slots=True)
class OsmImportSummary:
    vertex_count: int
    edge_count: int
    skipped_edge_count: int
    vertex_table: str = OSM_VERTEX_TABLE
    edge_table: str = OSM_EDGE_TABLE


def build_overpass_walk_network_query(
    *,
    south: float,
    west: float,
    north: float,
    east: float,
) -> str:
    validate_bbox(south=south, west=west, north=north, east=east)
    highway_filter = "|".join(sorted(WALKABLE_HIGHWAYS))
    bbox = f"{south},{west},{north},{east}"
    return f"""
[out:json][timeout:90];
(
  way["highway"~"^({highway_filter})$"]["area"!~"yes"]["access"!~"^(no|private)$"]["foot"!~"^(no|private)$"]({bbox});
  way["footway"]["access"!~"^(no|private)$"]["foot"!~"^(no|private)$"]({bbox});
  way["sidewalk"]["access"!~"^(no|private)$"]["foot"!~"^(no|private)$"]({bbox});
);
out body;
>;
out skel qt;
""".strip()


def validate_bbox(*, south: float, west: float, north: float, east: float) -> None:
    if not -90 <= south <= 90 or not -90 <= north <= 90:
        raise ValueError("south/north must be valid latitudes")
    if not -180 <= west <= 180 or not -180 <= east <= 180:
        raise ValueError("west/east must be valid longitudes")
    if south >= north:
        raise ValueError("south must be smaller than north")
    if west >= east:
        raise ValueError("west must be smaller than east")


def fetch_osm_walk_network(
    *,
    south: float,
    west: float,
    north: float,
    east: float,
    overpass_url: str = OSM_OVERPASS_URL,
) -> OsmWalkDataset:
    query = build_overpass_walk_network_query(south=south, west=west, north=north, east=east)
    with httpx.Client(timeout=120) as client:
        response = client.post(
            overpass_url,
            data={"data": query},
            headers={
                "Accept": "application/json",
                "User-Agent": "StepTwin/0.1 OSM importer",
            },
        )
        response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise OsmWalkNetworkError("Overpass response must be a JSON object")

    return parse_overpass_walk_network_payload(
        payload,
        name="osm-walk-network",
        bbox=(south, west, north, east),
    )


def parse_overpass_walk_network_payload(
    payload: Mapping[str, Any],
    *,
    name: str,
    bbox: tuple[float, float, float, float],
) -> OsmWalkDataset:
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise OsmWalkNetworkError("Overpass response must contain elements[]")

    nodes: dict[int, OsmNode] = {}
    ways: dict[int, OsmWay] = {}
    for element in elements:
        if not isinstance(element, dict):
            continue
        element_type = element.get("type")
        if element_type == "node":
            node = parse_osm_node(element)
            if node is not None:
                nodes[node.id] = node
        elif element_type == "way":
            way = parse_osm_way(element)
            if way is not None and is_walkable_way(way.tags):
                ways[way.id] = way

    referenced_node_ids = {node_id for way in ways.values() for node_id in way.node_ids}
    nodes = {node_id: node for node_id, node in nodes.items() if node_id in referenced_node_ids}
    ways = {
        way_id: way
        for way_id, way in ways.items()
        if sum(1 for node_id in way.node_ids if node_id in nodes) >= 2
    }

    return OsmWalkDataset(
        name=name,
        bbox=bbox,
        nodes=tuple(nodes.values()),
        ways=tuple(ways.values()),
    )


def parse_osm_node(element: Mapping[str, Any]) -> OsmNode | None:
    node_id = parse_int(element.get("id"))
    latitude = parse_float(element.get("lat"))
    longitude = parse_float(element.get("lon"))
    if node_id is None or latitude is None or longitude is None:
        return None

    return OsmNode(
        id=node_id,
        latitude=latitude,
        longitude=longitude,
        tags=parse_tags(element.get("tags")),
    )


def parse_osm_way(element: Mapping[str, Any]) -> OsmWay | None:
    way_id = parse_int(element.get("id"))
    raw_node_ids = element.get("nodes")
    if way_id is None or not isinstance(raw_node_ids, list):
        return None

    node_ids = tuple(node_id for value in raw_node_ids if (node_id := parse_int(value)) is not None)
    if len(node_ids) < 2:
        return None

    return OsmWay(id=way_id, node_ids=node_ids, tags=parse_tags(element.get("tags")))


def parse_tags(value: object) -> Mapping[str, str]:
    if not isinstance(value, dict):
        return {}

    return {
        str(key): str(tag_value)
        for key, tag_value in value.items()
        if isinstance(key, str) and tag_value is not None
    }


def is_walkable_way(tags: Mapping[str, str]) -> bool:
    highway = tags.get("highway")
    if highway not in WALKABLE_HIGHWAYS:
        return False

    if normalized_tag_value(tags.get("access")) in BLOCKED_VALUES:
        return False
    if normalized_tag_value(tags.get("foot")) in BLOCKED_VALUES:
        return False
    return not (
        normalized_tag_value(tags.get("sidewalk")) == "no" and highway in {"primary", "secondary"}
    )


def normalized_tag_value(value: str | None) -> str | None:
    if value is None:
        return None

    return value.strip().lower().replace("-", "_")


async def import_osm_walk_dataset(
    session: AsyncSession,
    dataset: OsmWalkDataset,
    *,
    replace_existing: bool,
) -> OsmImportSummary:
    vertex_rows = [build_osm_vertex_row(node) for node in dataset.nodes]
    edge_rows, skipped_edge_count = build_osm_edge_rows(dataset)

    async with session.begin():
        await prepare_osm_walk_tables(session)
        if replace_existing:
            await session.execute(text(f'DELETE FROM "{OSM_EDGE_TABLE}"'))
            await session.execute(text(f'DELETE FROM "{OSM_VERTEX_TABLE}"'))

        for batch in chunk_rows(vertex_rows, OSM_IMPORT_BATCH_SIZE):
            await session.execute(text(build_osm_vertex_upsert_sql()), batch)
        for batch in chunk_rows(edge_rows, OSM_IMPORT_BATCH_SIZE):
            await session.execute(text(build_osm_edge_upsert_sql()), batch)

    return OsmImportSummary(
        vertex_count=len(vertex_rows),
        edge_count=len(edge_rows),
        skipped_edge_count=skipped_edge_count,
    )


async def prepare_osm_walk_tables(session: AsyncSession) -> None:
    await session.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    await session.execute(text("CREATE EXTENSION IF NOT EXISTS pgrouting"))
    await session.execute(text(build_osm_vertex_table_sql()))
    await session.execute(text(build_osm_edge_table_sql()))
    await session.execute(
        text(
            f'ALTER TABLE "{OSM_EDGE_TABLE}" '
            'ADD COLUMN IF NOT EXISTS "roughness_score" double precision NOT NULL DEFAULT 0 '
            'CHECK ("roughness_score" >= 0 AND "roughness_score" <= 1)'
        )
    )
    await session.execute(
        text(
            f'ALTER TABLE "{OSM_EDGE_TABLE}" '
            'ADD COLUMN IF NOT EXISTS "crossing_length_meters" double precision '
            'CHECK ("crossing_length_meters" IS NULL OR "crossing_length_meters" > 0)'
        )
    )
    await session.execute(
        text(
            f'ALTER TABLE "{OSM_EDGE_TABLE}" '
            'ADD COLUMN IF NOT EXISTS "crossing_wait_seconds" double precision '
            'CHECK ("crossing_wait_seconds" IS NULL OR "crossing_wait_seconds" >= 0)'
        )
    )
    await session.execute(
        text(
            f'ALTER TABLE "{OSM_EDGE_TABLE}" '
            'ADD COLUMN IF NOT EXISTS "crowding_score" double precision NOT NULL DEFAULT 0 '
            'CHECK ("crowding_score" >= 0 AND "crowding_score" <= 1)'
        )
    )
    await session.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS "{OSM_VERTEX_TABLE}_geom_gix" '
            f'ON "{OSM_VERTEX_TABLE}" USING gist ("geom")'
        )
    )
    await session.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS "{OSM_EDGE_TABLE}_geom_gix" '
            f'ON "{OSM_EDGE_TABLE}" USING gist ("geom")'
        )
    )
    await session.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS "{OSM_EDGE_TABLE}_source_idx" '
            f'ON "{OSM_EDGE_TABLE}" ("source")'
        )
    )
    await session.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS "{OSM_EDGE_TABLE}_target_idx" '
            f'ON "{OSM_EDGE_TABLE}" ("target")'
        )
    )


def build_osm_vertex_table_sql() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS "{OSM_VERTEX_TABLE}" (
    "id" bigint PRIMARY KEY,
    "osm_node_id" bigint NOT NULL UNIQUE,
    "tags" jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    "geom" geometry(Point, 4326) NOT NULL,
    "updated_at" timestamptz NOT NULL DEFAULT now()
)
""".strip()


def build_osm_edge_table_sql() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS "{OSM_EDGE_TABLE}" (
    "id" bigint PRIMARY KEY,
    "osm_way_id" bigint NOT NULL,
    "source" bigint NOT NULL REFERENCES "{OSM_VERTEX_TABLE}" ("id"),
    "target" bigint NOT NULL REFERENCES "{OSM_VERTEX_TABLE}" ("id"),
    "geom" geometry(LineString, 4326) NOT NULL,
    "distance_meters" double precision NOT NULL CHECK ("distance_meters" > 0),
    "stairs_count" integer NOT NULL DEFAULT 0 CHECK ("stairs_count" >= 0),
    "shade_score" double precision NOT NULL DEFAULT 0
        CHECK ("shade_score" >= 0 AND "shade_score" <= 1),
    "corner_count" integer NOT NULL DEFAULT 0 CHECK ("corner_count" >= 0),
    "slope_grade" double precision NOT NULL DEFAULT 0 CHECK ("slope_grade" >= 0),
    "crowding_score" double precision NOT NULL DEFAULT 0
        CHECK ("crowding_score" >= 0 AND "crowding_score" <= 1),
    "roughness_score" double precision NOT NULL DEFAULT 0
        CHECK ("roughness_score" >= 0 AND "roughness_score" <= 1),
    "crossing_type" text NOT NULL DEFAULT 'none',
    "crossing_length_meters" double precision
        CHECK ("crossing_length_meters" IS NULL OR "crossing_length_meters" > 0),
    "crossing_wait_seconds" double precision
        CHECK ("crossing_wait_seconds" IS NULL OR "crossing_wait_seconds" >= 0),
    "surface_type" text NOT NULL DEFAULT 'unknown',
    "width_meters" double precision CHECK ("width_meters" IS NULL OR "width_meters" > 0),
    "curb_cut" boolean,
    "wheelchair_ok" boolean,
    "bidirectional" boolean NOT NULL DEFAULT true,
    "name" text,
    "tags" jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    "updated_at" timestamptz NOT NULL DEFAULT now()
)
""".strip()


def build_osm_vertex_upsert_sql() -> str:
    return f"""
INSERT INTO "{OSM_VERTEX_TABLE}" (
    "id",
    "osm_node_id",
    "tags",
    "geom",
    "updated_at"
)
VALUES (
    :id,
    :osm_node_id,
    CAST(:tags_json AS jsonb),
    ST_SetSRID(ST_MakePoint(CAST(:longitude AS float8), CAST(:latitude AS float8)), 4326),
    now()
)
ON CONFLICT ("id") DO UPDATE SET
    "osm_node_id" = EXCLUDED."osm_node_id",
    "tags" = EXCLUDED."tags",
    "geom" = EXCLUDED."geom",
    "updated_at" = now()
""".strip()


def build_osm_edge_upsert_sql() -> str:
    return f"""
INSERT INTO "{OSM_EDGE_TABLE}" (
    "id",
    "osm_way_id",
    "source",
    "target",
    "geom",
    "distance_meters",
    "stairs_count",
    "shade_score",
    "corner_count",
    "slope_grade",
    "crowding_score",
    "roughness_score",
    "crossing_type",
    "crossing_length_meters",
    "crossing_wait_seconds",
    "surface_type",
    "width_meters",
    "curb_cut",
    "wheelchair_ok",
    "bidirectional",
    "name",
    "tags",
    "updated_at"
)
VALUES (
    :id,
    :osm_way_id,
    :source,
    :target,
    ST_SetSRID(ST_GeomFromGeoJSON(:geometry_geojson), 4326),
    :distance_meters,
    :stairs_count,
    :shade_score,
    :corner_count,
    :slope_grade,
    :crowding_score,
    :roughness_score,
    :crossing_type,
    :crossing_length_meters,
    :crossing_wait_seconds,
    :surface_type,
    :width_meters,
    :curb_cut,
    :wheelchair_ok,
    :bidirectional,
    :name,
    CAST(:tags_json AS jsonb),
    now()
)
ON CONFLICT ("id") DO UPDATE SET
    "osm_way_id" = EXCLUDED."osm_way_id",
    "source" = EXCLUDED."source",
    "target" = EXCLUDED."target",
    "geom" = EXCLUDED."geom",
    "distance_meters" = EXCLUDED."distance_meters",
    "stairs_count" = EXCLUDED."stairs_count",
    "shade_score" = EXCLUDED."shade_score",
    "corner_count" = EXCLUDED."corner_count",
    "slope_grade" = EXCLUDED."slope_grade",
    "crowding_score" = EXCLUDED."crowding_score",
    "roughness_score" = EXCLUDED."roughness_score",
    "crossing_type" = EXCLUDED."crossing_type",
    "crossing_length_meters" = EXCLUDED."crossing_length_meters",
    "crossing_wait_seconds" = EXCLUDED."crossing_wait_seconds",
    "surface_type" = EXCLUDED."surface_type",
    "width_meters" = EXCLUDED."width_meters",
    "curb_cut" = EXCLUDED."curb_cut",
    "wheelchair_ok" = EXCLUDED."wheelchair_ok",
    "bidirectional" = EXCLUDED."bidirectional",
    "name" = EXCLUDED."name",
    "tags" = EXCLUDED."tags",
    "updated_at" = now()
""".strip()


def build_osm_vertex_row(node: OsmNode) -> dict[str, object]:
    return {
        "id": node.id,
        "osm_node_id": node.id,
        "latitude": node.latitude,
        "longitude": node.longitude,
        "tags_json": json.dumps(dict(node.tags)),
    }


def build_osm_edge_rows(dataset: OsmWalkDataset) -> tuple[list[dict[str, object]], int]:
    nodes = {node.id: node for node in dataset.nodes}
    edge_rows: list[dict[str, object]] = []
    skipped_edge_count = 0
    for way in dataset.ways:
        for index, source_id, target_id in iter_way_pairs(way.node_ids):
            source = nodes.get(source_id)
            target = nodes.get(target_id)
            if source is None or target is None or source.id == target.id:
                skipped_edge_count += 1
                continue

            geometry = [
                Coordinate(latitude=source.latitude, longitude=source.longitude),
                Coordinate(latitude=target.latitude, longitude=target.longitude),
            ]
            distance = distance_meters(geometry)
            if distance <= 0:
                skipped_edge_count += 1
                continue

            crossing_type = infer_crossing_type(way.tags)
            edge_rows.append(
                {
                    "id": build_osm_edge_id(way.id, index),
                    "osm_way_id": way.id,
                    "source": source.id,
                    "target": target.id,
                    "geometry_geojson": build_linestring_geojson(geometry),
                    "distance_meters": distance,
                    "stairs_count": 1 if way.tags.get("highway") == "steps" else 0,
                    "shade_score": infer_shade_score(way.tags),
                    "corner_count": 0,
                    "slope_grade": infer_slope_grade(way.tags),
                    "crowding_score": 0.0,
                    "roughness_score": infer_roughness_score(way.tags),
                    "crossing_type": crossing_type,
                    "crossing_length_meters": distance if crossing_type != "none" else None,
                    "crossing_wait_seconds": None,
                    "surface_type": infer_surface_type(way.tags),
                    "width_meters": parse_float(way.tags.get("width")),
                    "curb_cut": None,
                    "wheelchair_ok": infer_wheelchair_ok(way.tags),
                    "bidirectional": True,
                    "name": way.tags.get("name"),
                    "tags_json": json.dumps(dict(way.tags)),
                }
            )

    return edge_rows, skipped_edge_count


def iter_way_pairs(node_ids: tuple[int, ...]) -> Iterable[tuple[int, int, int]]:
    for index in range(len(node_ids) - 1):
        yield index, node_ids[index], node_ids[index + 1]


def build_osm_edge_id(way_id: int, segment_index: int) -> int:
    return way_id * 10000 + segment_index + 1


def infer_shade_score(tags: Mapping[str, str]) -> float:
    if normalized_tag_value(tags.get("covered")) == "yes":
        return 0.7
    if tags.get("tunnel") is not None or tags.get("bridge") is not None:
        return 0.2
    return 0.0


def infer_crossing_type(tags: Mapping[str, str]) -> str:
    if tags.get("crossing") is not None or tags.get("footway") == "crossing":
        if tags.get("crossing") == "traffic_signals":
            return "signalized"
        return "crosswalk"
    return "none"


def infer_surface_type(tags: Mapping[str, str]) -> str:
    highway = tags.get("highway")
    surface = normalized_tag_value(tags.get("surface"))
    if highway == "steps":
        return "stairs"
    if surface in {"paved", "asphalt", "concrete", "paving_stones", "sett"}:
        return "paved"
    if surface in {"gravel", "fine_gravel", "dirt", "earth", "ground"}:
        return "rough"
    return "unknown"


def infer_roughness_score(tags: Mapping[str, str]) -> float:
    smoothness = normalized_tag_value(tags.get("smoothness"))
    if smoothness in {"excellent", "good"}:
        return 0.0
    if smoothness in {"intermediate"}:
        return 0.25
    if smoothness in {"bad", "very_bad"}:
        return 0.65
    if smoothness in {"horrible", "very_horrible", "impassable"}:
        return 1.0

    surface = normalized_tag_value(tags.get("surface"))
    if surface in {"asphalt", "concrete", "paved", "paving_stones", "sett"}:
        return 0.1
    if surface in {"compacted", "fine_gravel"}:
        return 0.35
    if surface in {"gravel", "dirt", "earth", "ground", "grass", "sand"}:
        return 0.75
    if tags.get("highway") == "steps":
        return 0.85

    tracktype = normalized_tag_value(tags.get("tracktype"))
    if tracktype == "grade1":
        return 0.2
    if tracktype in {"grade2", "grade3"}:
        return 0.55
    if tracktype in {"grade4", "grade5"}:
        return 0.85

    return 0.0


def infer_slope_grade(tags: Mapping[str, str]) -> float:
    raw_incline = tags.get("incline")
    incline = raw_incline.strip().lower() if raw_incline is not None else None
    if incline is None or incline in {"up", "down"}:
        return 0.0

    if incline.endswith("%"):
        value = parse_float(incline.removesuffix("%"))
        if value is not None:
            return min(abs(value) / 100, 1.0)

    if incline.endswith("°"):
        value = parse_float(incline.removesuffix("°"))
        if value is not None:
            return min(abs(math_tan_degrees(value)), 1.0)

    value = parse_float(incline)
    if value is not None:
        return min(abs(value) / 100, 1.0)

    return 0.0


def math_tan_degrees(value: float) -> float:
    import math

    return math.tan(math.radians(value))


def infer_wheelchair_ok(tags: Mapping[str, str]) -> bool | None:
    wheelchair = normalized_tag_value(tags.get("wheelchair"))
    if wheelchair in {"yes", "designated"}:
        return True
    if wheelchair == "no" or tags.get("highway") == "steps":
        return False
    return None


def build_linestring_geojson(geometry: list[Coordinate]) -> str:
    return json.dumps(
        {
            "type": "LineString",
            "coordinates": [
                [coordinate.longitude, coordinate.latitude] for coordinate in geometry
            ],
        }
    )


def chunk_rows[T](rows: list[T], batch_size: int) -> list[list[T]]:
    return [rows[index : index + batch_size] for index in range(0, len(rows), batch_size)]


def parse_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def parse_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None
