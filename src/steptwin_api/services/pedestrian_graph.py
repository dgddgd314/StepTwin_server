import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from steptwin_api.schemas.pedestrian_graph import (
    PedestrianGraphDataset,
    PedestrianGraphEdge,
    PedestrianGraphImportResponse,
    PedestrianGraphValidationResponse,
    PedestrianGraphValidationSummary,
    PedestrianGraphVertex,
)
from steptwin_api.schemas.routing import Coordinate
from steptwin_api.services.geometry import distance_meters

ENDPOINT_GEOMETRY_TOLERANCE_METERS = 20
DISTANCE_MISMATCH_RATIO_THRESHOLD = 0.15
DEFAULT_VERTEX_TABLE = "pedestrian_vertices"
DEFAULT_EDGE_TABLE = "pedestrian_edges"
IMPORT_BATCH_SIZE = 5000


async def import_pedestrian_graph_dataset(
    session: AsyncSession,
    dataset: PedestrianGraphDataset,
    *,
    replace_existing: bool,
) -> PedestrianGraphImportResponse:
    validation = validate_pedestrian_graph_dataset(dataset)
    edge_rows = [build_edge_import_row(edge) for edge in dataset.edges]
    computed_distance_edge_count = sum(1 for edge in dataset.edges if edge.distance_meters is None)

    async with session.begin():
        await prepare_pedestrian_graph_tables(session)

        if replace_existing:
            await session.execute(text(f'DELETE FROM "{DEFAULT_EDGE_TABLE}"'))
            await session.execute(text(f'DELETE FROM "{DEFAULT_VERTEX_TABLE}"'))

        vertex_rows = [build_vertex_import_row(vertex) for vertex in dataset.vertices]
        vertex_upsert = text(build_vertex_upsert_sql())
        edge_upsert = text(build_edge_upsert_sql())
        for batch in chunk_rows(vertex_rows, IMPORT_BATCH_SIZE):
            await session.execute(vertex_upsert, batch)
        for batch in chunk_rows(edge_rows, IMPORT_BATCH_SIZE):
            await session.execute(edge_upsert, batch)

    return PedestrianGraphImportResponse(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        vertex_count=len(dataset.vertices),
        edge_count=len(dataset.edges),
        computed_distance_edge_count=computed_distance_edge_count,
        replaced_existing=replace_existing,
        vertex_table=DEFAULT_VERTEX_TABLE,
        edge_table=DEFAULT_EDGE_TABLE,
        ready_for_routing=True,
        warnings=validation.warnings,
    )


async def prepare_pedestrian_graph_tables(session: AsyncSession) -> None:
    await session.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    await session.execute(text("CREATE EXTENSION IF NOT EXISTS pgrouting"))
    await session.execute(text(build_vertex_table_sql()))
    await session.execute(text(build_edge_table_sql()))
    await session.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS "{DEFAULT_VERTEX_TABLE}_geom_gix" '
            f'ON "{DEFAULT_VERTEX_TABLE}" USING gist ("geom")'
        )
    )
    await session.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS "{DEFAULT_EDGE_TABLE}_geom_gix" '
            f'ON "{DEFAULT_EDGE_TABLE}" USING gist ("geom")'
        )
    )
    await session.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS "{DEFAULT_EDGE_TABLE}_source_idx" '
            f'ON "{DEFAULT_EDGE_TABLE}" ("source")'
        )
    )
    await session.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS "{DEFAULT_EDGE_TABLE}_target_idx" '
            f'ON "{DEFAULT_EDGE_TABLE}" ("target")'
        )
    )


def build_vertex_table_sql() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS "{DEFAULT_VERTEX_TABLE}" (
    "id" bigint PRIMARY KEY,
    "kind" text NOT NULL,
    "name" text,
    "tags" jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    "geom" geometry(Point, 4326) NOT NULL,
    "updated_at" timestamptz NOT NULL DEFAULT now()
)
""".strip()


def chunk_rows[T](rows: list[T], batch_size: int) -> list[list[T]]:
    return [rows[index : index + batch_size] for index in range(0, len(rows), batch_size)]


def build_edge_table_sql() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS "{DEFAULT_EDGE_TABLE}" (
    "id" bigint PRIMARY KEY,
    "source" bigint NOT NULL REFERENCES "{DEFAULT_VERTEX_TABLE}" ("id"),
    "target" bigint NOT NULL REFERENCES "{DEFAULT_VERTEX_TABLE}" ("id"),
    "geom" geometry(LineString, 4326) NOT NULL,
    "distance_meters" double precision NOT NULL CHECK ("distance_meters" > 0),
    "stairs_count" integer NOT NULL DEFAULT 0 CHECK ("stairs_count" >= 0),
    "shade_score" double precision NOT NULL DEFAULT 0
        CHECK ("shade_score" >= 0 AND "shade_score" <= 1),
    "corner_count" integer NOT NULL DEFAULT 0 CHECK ("corner_count" >= 0),
    "slope_grade" double precision NOT NULL DEFAULT 0 CHECK ("slope_grade" >= 0),
    "crossing_type" text NOT NULL DEFAULT 'none',
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


def build_vertex_upsert_sql() -> str:
    return f"""
INSERT INTO "{DEFAULT_VERTEX_TABLE}" (
    "id",
    "kind",
    "name",
    "tags",
    "geom",
    "updated_at"
)
VALUES (
    :id,
    :kind,
    :name,
    CAST(:tags_json AS jsonb),
    ST_SetSRID(ST_MakePoint(CAST(:longitude AS float8), CAST(:latitude AS float8)), 4326),
    now()
)
ON CONFLICT ("id") DO UPDATE SET
    "kind" = EXCLUDED."kind",
    "name" = EXCLUDED."name",
    "tags" = EXCLUDED."tags",
    "geom" = EXCLUDED."geom",
    "updated_at" = now()
""".strip()


def build_edge_upsert_sql() -> str:
    return f"""
INSERT INTO "{DEFAULT_EDGE_TABLE}" (
    "id",
    "source",
    "target",
    "geom",
    "distance_meters",
    "stairs_count",
    "shade_score",
    "corner_count",
    "slope_grade",
    "crossing_type",
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
    :source,
    :target,
    ST_SetSRID(ST_GeomFromGeoJSON(:geometry_geojson), 4326),
    :distance_meters,
    :stairs_count,
    :shade_score,
    :corner_count,
    :slope_grade,
    :crossing_type,
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
    "source" = EXCLUDED."source",
    "target" = EXCLUDED."target",
    "geom" = EXCLUDED."geom",
    "distance_meters" = EXCLUDED."distance_meters",
    "stairs_count" = EXCLUDED."stairs_count",
    "shade_score" = EXCLUDED."shade_score",
    "corner_count" = EXCLUDED."corner_count",
    "slope_grade" = EXCLUDED."slope_grade",
    "crossing_type" = EXCLUDED."crossing_type",
    "surface_type" = EXCLUDED."surface_type",
    "width_meters" = EXCLUDED."width_meters",
    "curb_cut" = EXCLUDED."curb_cut",
    "wheelchair_ok" = EXCLUDED."wheelchair_ok",
    "bidirectional" = EXCLUDED."bidirectional",
    "name" = EXCLUDED."name",
    "tags" = EXCLUDED."tags",
    "updated_at" = now()
""".strip()


def build_vertex_import_row(vertex: PedestrianGraphVertex) -> dict[str, object]:
    coordinate = vertex.coordinate
    return {
        "id": vertex.id,
        "kind": vertex.kind,
        "name": vertex.name,
        "tags_json": json.dumps(vertex.tags),
        "longitude": coordinate.longitude,
        "latitude": coordinate.latitude,
    }


def build_edge_import_row(edge: PedestrianGraphEdge) -> dict[str, object]:
    return {
        "id": edge.id,
        "source": edge.source,
        "target": edge.target,
        "geometry_geojson": build_linestring_geojson(edge.geometry),
        "distance_meters": edge.distance_meters
        if edge.distance_meters is not None
        else distance_meters(edge.geometry),
        "stairs_count": edge.stairs_count,
        "shade_score": edge.shade_score,
        "corner_count": edge.corner_count,
        "slope_grade": edge.slope_grade,
        "crossing_type": edge.crossing_type,
        "surface_type": edge.surface_type,
        "width_meters": edge.width_meters,
        "curb_cut": edge.curb_cut,
        "wheelchair_ok": edge.wheelchair_ok,
        "bidirectional": edge.bidirectional,
        "name": edge.name,
        "tags_json": json.dumps(edge.tags),
    }


def build_linestring_geojson(geometry: list[Coordinate]) -> str:
    return json.dumps(
        {
            "type": "LineString",
            "coordinates": [
                [coordinate.longitude, coordinate.latitude] for coordinate in geometry
            ],
        }
    )


def validate_pedestrian_graph_dataset(
    dataset: PedestrianGraphDataset,
) -> PedestrianGraphValidationResponse:
    vertices_by_id = {vertex.id: vertex for vertex in dataset.vertices}
    warnings: list[str] = []
    total_declared_distance = 0
    total_computed_distance = 0
    missing_distance_edge_count = 0

    for edge in dataset.edges:
        computed_distance = distance_meters(edge.geometry)
        total_computed_distance += computed_distance

        if edge.distance_meters is None:
            missing_distance_edge_count += 1
        else:
            declared_distance = round(edge.distance_meters)
            total_declared_distance += declared_distance
            if has_large_distance_mismatch(declared_distance, computed_distance):
                warnings.append(
                    f"edge {edge.id} declared distance differs from geometry distance "
                    f"by more than {round(DISTANCE_MISMATCH_RATIO_THRESHOLD * 100)}%"
                )

        source = vertices_by_id[edge.source]
        target = vertices_by_id[edge.target]
        validate_edge_endpoint_geometry(
            edge_id=edge.id,
            source_coordinate=source.coordinate,
            target_coordinate=target.coordinate,
            edge_geometry=edge.geometry,
            warnings=warnings,
        )

        if edge.surface_type == "stairs" and edge.wheelchair_ok is not False:
            warnings.append(f"edge {edge.id} is stairs but wheelchair_ok is not false")

    if missing_distance_edge_count:
        warnings.append(
            "distance_meters is missing on some edges; importer must compute it before pgRouting"
        )

    summary = PedestrianGraphValidationSummary(
        vertex_count=len(dataset.vertices),
        edge_count=len(dataset.edges),
        total_declared_distance_meters=total_declared_distance,
        total_computed_distance_meters=total_computed_distance,
        stairs_edge_count=sum(1 for edge in dataset.edges if edge.stairs_count > 0),
        shaded_edge_count=sum(1 for edge in dataset.edges if edge.shade_score >= 0.45),
        crossing_edge_count=sum(1 for edge in dataset.edges if edge.crossing_type != "none"),
        wheelchair_blocked_edge_count=sum(
            1 for edge in dataset.edges if edge.wheelchair_ok is False
        ),
        missing_distance_edge_count=missing_distance_edge_count,
        route_ready=missing_distance_edge_count == 0,
    )

    return PedestrianGraphValidationResponse(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        summary=summary,
        warnings=warnings,
    )


def has_large_distance_mismatch(declared_distance: int, computed_distance: int) -> bool:
    if computed_distance <= 0:
        return declared_distance > 0

    distance_mismatch_ratio = abs(declared_distance - computed_distance) / computed_distance
    return distance_mismatch_ratio > DISTANCE_MISMATCH_RATIO_THRESHOLD


def validate_edge_endpoint_geometry(
    *,
    edge_id: int,
    source_coordinate: Coordinate,
    target_coordinate: Coordinate,
    edge_geometry: list[Coordinate],
    warnings: list[str],
) -> None:
    source_gap = distance_meters([source_coordinate, edge_geometry[0]])
    target_gap = distance_meters([target_coordinate, edge_geometry[-1]])
    reversed_source_gap = distance_meters([source_coordinate, edge_geometry[-1]])
    reversed_target_gap = distance_meters([target_coordinate, edge_geometry[0]])

    if (
        source_gap <= ENDPOINT_GEOMETRY_TOLERANCE_METERS
        and target_gap <= ENDPOINT_GEOMETRY_TOLERANCE_METERS
    ):
        return

    if (
        reversed_source_gap <= ENDPOINT_GEOMETRY_TOLERANCE_METERS
        and reversed_target_gap <= ENDPOINT_GEOMETRY_TOLERANCE_METERS
    ):
        warnings.append(f"edge {edge_id} geometry appears reversed relative to source/target")
        return

    warnings.append(f"edge {edge_id} geometry endpoints are far from source/target vertices")
