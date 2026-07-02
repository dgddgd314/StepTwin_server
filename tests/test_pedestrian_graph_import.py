import json

from steptwin_api.schemas.pedestrian_graph import PedestrianGraphEdge
from steptwin_api.schemas.routing import Coordinate
from steptwin_api.services.pedestrian_graph import (
    build_edge_import_row,
    build_edge_table_sql,
    build_edge_upsert_sql,
    build_linestring_geojson,
    build_vertex_table_sql,
    chunk_rows,
)


def test_edge_import_row_computes_distance_when_missing() -> None:
    edge = PedestrianGraphEdge(
        id=10,
        source=1,
        target=2,
        geometry=[
            Coordinate(latitude=37.58945, longitude=127.05775),
            Coordinate(latitude=37.59375, longitude=127.05158),
        ],
        shade_score=0.5,
        slope_grade=0.02,
    )

    row = build_edge_import_row(edge)

    distance = row["distance_meters"]
    assert isinstance(distance, int | float)
    assert distance > 0
    assert row["geometry_geojson"] == build_linestring_geojson(edge.geometry)
    assert row["source"] == 1
    assert row["target"] == 2


def test_linestring_geojson_uses_longitude_latitude_order() -> None:
    geojson = build_linestring_geojson(
        [
            Coordinate(latitude=37.1, longitude=127.1),
            Coordinate(latitude=37.2, longitude=127.2),
        ]
    )

    assert json.loads(geojson) == {
        "type": "LineString",
        "coordinates": [[127.1, 37.1], [127.2, 37.2]],
    }


def test_import_sql_prepares_pgroute_ready_tables() -> None:
    vertex_sql = build_vertex_table_sql()
    edge_sql = build_edge_table_sql()
    edge_upsert_sql = build_edge_upsert_sql()

    assert '"geom" geometry(Point, 4326) NOT NULL' in vertex_sql
    assert '"geom" geometry(LineString, 4326) NOT NULL' in edge_sql
    assert '"source" bigint NOT NULL REFERENCES "pedestrian_vertices" ("id")' in edge_sql
    assert '"target" bigint NOT NULL REFERENCES "pedestrian_vertices" ("id")' in edge_sql
    assert '"distance_meters" double precision NOT NULL' in edge_sql
    assert "ST_GeomFromGeoJSON(:geometry_geojson)" in edge_upsert_sql
    assert 'ON CONFLICT ("id") DO UPDATE SET' in edge_upsert_sql


def test_chunk_rows_splits_large_import_batches() -> None:
    assert chunk_rows([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
