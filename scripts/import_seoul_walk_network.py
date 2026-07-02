from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from sqlalchemy.engine import make_url

from steptwin_api.core.config import get_settings
from steptwin_api.db.session import close_database, init_database, session_context
from steptwin_api.services.pedestrian_graph import import_pedestrian_graph_dataset
from steptwin_api.services.seoul_walk_network import (
    build_pedestrian_graph_dataset_from_seoul_rows,
    fetch_all_seoul_walk_network_rows,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch the full Seoul OpenAPI walking network and import it into PostGIS."
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Upsert fetched Seoul rows without clearing existing pedestrian graph tables.",
    )
    parser.add_argument(
        "--sgg-name",
        help="Optional Seoul district filter passed to TbTraficWlkNet.",
    )
    parser.add_argument(
        "--work-dttm",
        help="Optional WORK_DTTM like-filter passed to TbTraficWlkNet.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and normalize rows, but do not write to PostGIS.",
    )
    return parser.parse_args()


def print_progress(page_number: int, fetched_count: int, total_count: int) -> None:
    print(f"fetched page {page_number}: {fetched_count}/{total_count} rows")


async def main() -> None:
    args = parse_args()
    settings = get_settings()

    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is required")
    if not make_url(settings.database_url).drivername.startswith("postgresql"):
        raise SystemExit("DATABASE_URL must use PostgreSQL/PostGIS/pgRouting")

    rows = fetch_all_seoul_walk_network_rows(
        settings,
        sgg_name=args.sgg_name,
        work_dttm=args.work_dttm,
        progress_callback=print_progress,
    )
    dataset = build_pedestrian_graph_dataset_from_seoul_rows(
        rows,
        version=datetime.now().strftime("%Y%m%d%H%M%S"),
    )

    print(
        "normalized Seoul walking graph: "
        f"{len(dataset.vertices)} vertices, {len(dataset.edges)} pedestrian edges"
    )
    if args.dry_run:
        return

    init_database(settings)
    try:
        async with session_context() as session:
            result = await import_pedestrian_graph_dataset(
                session,
                dataset,
                replace_existing=not args.append,
            )
    finally:
        await close_database()

    print(
        "imported Seoul walking graph: "
        f"{result.vertex_count} vertices, {result.edge_count} edges, "
        f"replace_existing={result.replaced_existing}"
    )
    for warning in result.warnings:
        print(f"warning: {warning}")


if __name__ == "__main__":
    asyncio.run(main())
