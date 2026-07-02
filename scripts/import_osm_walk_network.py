from __future__ import annotations

import argparse
import asyncio

from sqlalchemy.engine import make_url

from steptwin_api.core.config import get_settings
from steptwin_api.db.session import close_database, init_database, session_context
from steptwin_api.services.osm_walk_network import (
    fetch_osm_walk_network,
    import_osm_walk_dataset,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch an OSM walking network from Overpass and import it into separate OSM "
            "PostGIS/pgRouting tables."
        )
    )
    parser.add_argument("--south", type=float, required=True, help="South latitude of bbox.")
    parser.add_argument("--west", type=float, required=True, help="West longitude of bbox.")
    parser.add_argument("--north", type=float, required=True, help="North latitude of bbox.")
    parser.add_argument("--east", type=float, required=True, help="East longitude of bbox.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Upsert into OSM tables without deleting existing OSM rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and normalize OSM data, but do not write to PostGIS.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = get_settings()

    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is required")
    if not make_url(settings.database_url).drivername.startswith("postgresql"):
        raise SystemExit("DATABASE_URL must use PostgreSQL/PostGIS/pgRouting")

    dataset = fetch_osm_walk_network(
        south=args.south,
        west=args.west,
        north=args.north,
        east=args.east,
    )
    print(
        "normalized OSM walking graph: "
        f"{len(dataset.nodes)} vertices, {len(dataset.ways)} walkable ways"
    )
    if args.dry_run:
        return

    init_database(settings)
    try:
        async with session_context() as session:
            result = await import_osm_walk_dataset(
                session,
                dataset,
                replace_existing=not args.append,
            )
    finally:
        await close_database()

    print(
        "imported OSM walking graph: "
        f"{result.vertex_count} vertices, {result.edge_count} edges, "
        f"skipped_edges={result.skipped_edge_count}, "
        f"vertex_table={result.vertex_table}, edge_table={result.edge_table}"
    )


if __name__ == "__main__":
    asyncio.run(main())
