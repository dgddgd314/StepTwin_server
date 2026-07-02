from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

from steptwin_api.core.config import get_settings
from steptwin_api.db.session import close_database, init_database, session_context

DEFAULT_TOLERANCE_METERS = 2.0
WORK_SRID = 5179
SOURCE_EDGE_TABLE = "pedestrian_edges"
TOPOLOGY_WORK_EDGE_TABLE = "pedestrian_topology_work_edges"
TOPOLOGY_WORK_NODED_EDGE_TABLE = f"{TOPOLOGY_WORK_EDGE_TABLE}_noded"
TOPOLOGY_EDGE_TABLE = "pedestrian_topology_edges"
TOPOLOGY_VERTEX_TABLE = "pedestrian_topology_vertices"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild a pgRouting-ready pedestrian topology by snapping edge endpoints within a "
            "meter tolerance. Source pedestrian_edges/pedestrian_vertices are left untouched."
        )
    )
    parser.add_argument(
        "--tolerance-meters",
        type=float,
        default=DEFAULT_TOLERANCE_METERS,
        help=f"Endpoint snapping tolerance in meters. Default: {DEFAULT_TOLERANCE_METERS}",
    )
    parser.add_argument(
        "--node-network",
        action="store_true",
        help="Run pgr_nodeNetwork before pgr_createTopology to split edges at intersections.",
    )
    return parser.parse_args()


async def rebuild_topology(tolerance_meters: float, *, node_network: bool) -> None:
    if tolerance_meters <= 0:
        raise SystemExit("--tolerance-meters must be positive")

    settings = get_settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is required")
    if not make_url(settings.database_url).drivername.startswith("postgresql"):
        raise SystemExit("DATABASE_URL must use PostgreSQL/PostGIS/pgRouting")

    init_database(settings)
    try:
        async with session_context() as session:
            async with session.begin():
                await session.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
                await session.execute(text("CREATE EXTENSION IF NOT EXISTS pgrouting"))
                await drop_existing_topology_tables(session)
                await create_projected_work_edges(session)
                topology_source_table = TOPOLOGY_WORK_EDGE_TABLE
                if node_network:
                    await run_pgr_node_network(session, tolerance_meters)
                    topology_source_table = TOPOLOGY_WORK_NODED_EDGE_TABLE
                    await add_noded_attribute_columns(session)
                await run_pgr_create_topology(session, topology_source_table, tolerance_meters)
                await create_api_topology_tables(session, topology_source_table)
                await create_api_topology_indexes(session)

            summary = await fetch_summary(session)
    finally:
        await close_database()

    mode = "pgr_nodeNetwork + pgr_createTopology" if node_network else "pgr_createTopology"
    print(f"rebuilt topology with {mode}, tolerance_meters={tolerance_meters:g}")
    for key, value in summary.items():
        print(f"{key}: {value}")


async def drop_existing_topology_tables(session: object) -> None:
    for table_name in (
        TOPOLOGY_EDGE_TABLE,
        TOPOLOGY_VERTEX_TABLE,
        f"{TOPOLOGY_WORK_NODED_EDGE_TABLE}_vertices_pgr",
        TOPOLOGY_WORK_NODED_EDGE_TABLE,
        f"{TOPOLOGY_WORK_EDGE_TABLE}_vertices_pgr",
        TOPOLOGY_WORK_EDGE_TABLE,
    ):
        await session.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))


async def create_projected_work_edges(session: object) -> None:
    await session.execute(
        text(
            f"""
CREATE TABLE "{TOPOLOGY_WORK_EDGE_TABLE}" AS
SELECT
    "id"::bigint AS "id",
    NULL::bigint AS "source",
    NULL::bigint AS "target",
    ST_Transform("geom", {WORK_SRID})::geometry(LineString, {WORK_SRID}) AS "geom",
    "distance_meters"::double precision AS "distance_meters",
    "stairs_count"::integer AS "stairs_count",
    "shade_score"::double precision AS "shade_score",
    "corner_count"::integer AS "corner_count",
    "slope_grade"::double precision AS "slope_grade",
    "crossing_type" AS "crossing_type",
    "surface_type" AS "surface_type",
    "width_meters" AS "width_meters",
    "curb_cut" AS "curb_cut",
    "wheelchair_ok" AS "wheelchair_ok",
    "bidirectional" AS "bidirectional",
    "name" AS "name",
    "tags" AS "tags"
FROM "{SOURCE_EDGE_TABLE}"
WHERE "geom" IS NOT NULL
    AND "distance_meters" > 0
"""
        )
    )
    await session.execute(text(f'ALTER TABLE "{TOPOLOGY_WORK_EDGE_TABLE}" ADD PRIMARY KEY ("id")'))
    await session.execute(
        text(
            f'CREATE INDEX "{TOPOLOGY_WORK_EDGE_TABLE}_geom_gix" '
            f'ON "{TOPOLOGY_WORK_EDGE_TABLE}" USING gist ("geom")'
        )
    )


async def run_pgr_node_network(session: object, tolerance_meters: float) -> None:
    await session.execute(
        text(
            """
SELECT pgr_nodeNetwork(
    :edge_table,
    :tolerance,
    'id',
    'geom',
    'noded',
    'true',
    true
)
"""
        ),
        {"edge_table": TOPOLOGY_WORK_EDGE_TABLE, "tolerance": tolerance_meters},
    )
    await session.execute(
        text(
            f'CREATE INDEX "{TOPOLOGY_WORK_NODED_EDGE_TABLE}_geom_gix" '
            f'ON "{TOPOLOGY_WORK_NODED_EDGE_TABLE}" USING gist ("geom")'
        )
    )


async def add_noded_attribute_columns(session: object) -> None:
    await session.execute(
        text(
            f"""
ALTER TABLE "{TOPOLOGY_WORK_NODED_EDGE_TABLE}"
    ADD COLUMN "distance_meters" double precision,
    ADD COLUMN "stairs_count" integer,
    ADD COLUMN "shade_score" double precision,
    ADD COLUMN "corner_count" integer,
    ADD COLUMN "slope_grade" double precision,
    ADD COLUMN "crossing_type" text,
    ADD COLUMN "surface_type" text,
    ADD COLUMN "width_meters" double precision,
    ADD COLUMN "curb_cut" boolean,
    ADD COLUMN "wheelchair_ok" boolean,
    ADD COLUMN "bidirectional" boolean,
    ADD COLUMN "name" text,
    ADD COLUMN "tags" jsonb
"""
        )
    )
    await session.execute(
        text(
            f"""
UPDATE "{TOPOLOGY_WORK_NODED_EDGE_TABLE}" AS noded
SET
    "distance_meters" = ST_Length(noded."geom"),
    "stairs_count" = source_edge."stairs_count",
    "shade_score" = source_edge."shade_score",
    "corner_count" = source_edge."corner_count",
    "slope_grade" = source_edge."slope_grade",
    "crossing_type" = source_edge."crossing_type",
    "surface_type" = source_edge."surface_type",
    "width_meters" = source_edge."width_meters",
    "curb_cut" = source_edge."curb_cut",
    "wheelchair_ok" = source_edge."wheelchair_ok",
    "bidirectional" = source_edge."bidirectional",
    "name" = source_edge."name",
    "tags" = source_edge."tags"
FROM "{TOPOLOGY_WORK_EDGE_TABLE}" AS source_edge
WHERE source_edge."id" = noded."old_id"
"""
        )
    )


async def run_pgr_create_topology(
    session: object,
    topology_source_table: str,
    tolerance_meters: float,
) -> None:
    await session.execute(
        text(
            """
SELECT pgr_createTopology(
    :edge_table,
    :tolerance,
    'geom',
    'id',
    'source',
    'target',
    'true',
    true
)
"""
        ),
        {"edge_table": topology_source_table, "tolerance": tolerance_meters},
    )


async def create_api_topology_tables(session: object, topology_source_table: str) -> None:
    await session.execute(
        text(
            f"""
CREATE TABLE "{TOPOLOGY_VERTEX_TABLE}" AS
SELECT
    "id"::bigint AS "id",
    ST_Transform("the_geom", 4326)::geometry(Point, 4326) AS "geom",
    "cnt",
    "chk",
    "ein",
    "eout"
FROM "{topology_source_table}_vertices_pgr"
"""
        )
    )
    await session.execute(text(f'ALTER TABLE "{TOPOLOGY_VERTEX_TABLE}" ADD PRIMARY KEY ("id")'))

    await session.execute(
        text(
            f"""
CREATE TABLE "{TOPOLOGY_EDGE_TABLE}" AS
SELECT
    "id"::bigint AS "id",
    "source"::bigint AS "source",
    "target"::bigint AS "target",
    ST_Transform("geom", 4326)::geometry(LineString, 4326) AS "geom",
    "distance_meters"::double precision AS "distance_meters",
    "stairs_count"::integer AS "stairs_count",
    "shade_score"::double precision AS "shade_score",
    "corner_count"::integer AS "corner_count",
    "slope_grade"::double precision AS "slope_grade",
    "crossing_type" AS "crossing_type",
    "surface_type" AS "surface_type",
    "width_meters" AS "width_meters",
    "curb_cut" AS "curb_cut",
    "wheelchair_ok" AS "wheelchair_ok",
    "bidirectional" AS "bidirectional",
    "name" AS "name",
    "tags" AS "tags"
FROM "{TOPOLOGY_WORK_EDGE_TABLE}"
WHERE "source" IS NOT NULL
    AND "target" IS NOT NULL
    AND "source" <> "target"
"""
            if topology_source_table == TOPOLOGY_WORK_EDGE_TABLE
            else f"""
CREATE TABLE "{TOPOLOGY_EDGE_TABLE}" AS
SELECT
    "id"::bigint AS "id",
    "source"::bigint AS "source",
    "target"::bigint AS "target",
    ST_Transform("geom", 4326)::geometry(LineString, 4326) AS "geom",
    "distance_meters"::double precision AS "distance_meters",
    "stairs_count"::integer AS "stairs_count",
    "shade_score"::double precision AS "shade_score",
    "corner_count"::integer AS "corner_count",
    "slope_grade"::double precision AS "slope_grade",
    "crossing_type" AS "crossing_type",
    "surface_type" AS "surface_type",
    "width_meters" AS "width_meters",
    "curb_cut" AS "curb_cut",
    "wheelchair_ok" AS "wheelchair_ok",
    "bidirectional" AS "bidirectional",
    "name" AS "name",
    "tags" AS "tags"
FROM "{topology_source_table}"
WHERE "source" IS NOT NULL
    AND "target" IS NOT NULL
    AND "source" <> "target"
    AND "distance_meters" > 0
"""
        )
    )
    await session.execute(text(f'ALTER TABLE "{TOPOLOGY_EDGE_TABLE}" ADD PRIMARY KEY ("id")'))


async def create_api_topology_indexes(session: object) -> None:
    await session.execute(
        text(
            f'CREATE INDEX "{TOPOLOGY_VERTEX_TABLE}_geom_gix" '
            f'ON "{TOPOLOGY_VERTEX_TABLE}" USING gist ("geom")'
        )
    )
    await session.execute(
        text(
            f'CREATE INDEX "{TOPOLOGY_EDGE_TABLE}_geom_gix" '
            f'ON "{TOPOLOGY_EDGE_TABLE}" USING gist ("geom")'
        )
    )
    await session.execute(
        text(
            f'CREATE INDEX "{TOPOLOGY_EDGE_TABLE}_source_idx" '
            f'ON "{TOPOLOGY_EDGE_TABLE}" ("source")'
        )
    )
    await session.execute(
        text(
            f'CREATE INDEX "{TOPOLOGY_EDGE_TABLE}_target_idx" '
            f'ON "{TOPOLOGY_EDGE_TABLE}" ("target")'
        )
    )


async def fetch_summary(session: object) -> dict[str, int]:
    result = await session.execute(
        text(
            f"""
WITH comps AS (
    SELECT *
    FROM pgr_connectedComponents(
        'SELECT id::bigint AS id,
                source::bigint AS source,
                target::bigint AS target,
                distance_meters::float8 AS cost,
                distance_meters::float8 AS reverse_cost
         FROM {TOPOLOGY_EDGE_TABLE}
         WHERE distance_meters > 0'
    )
),
component_sizes AS (
    SELECT count(*) AS node_count
    FROM comps
    GROUP BY component
)
SELECT
    (SELECT count(*) FROM "{TOPOLOGY_VERTEX_TABLE}")::integer AS vertices,
    (SELECT count(*) FROM "{TOPOLOGY_EDGE_TABLE}")::integer AS edges,
    (SELECT count(*) FROM component_sizes)::integer AS components,
    COALESCE((SELECT max(node_count) FROM component_sizes), 0)::integer AS largest_component_nodes
"""
        )
    )
    return dict(result.mappings().one())


async def main() -> None:
    args = parse_args()
    await rebuild_topology(args.tolerance_meters, node_network=args.node_network)


if __name__ == "__main__":
    asyncio.run(main())
