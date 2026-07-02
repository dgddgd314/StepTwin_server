import pytest

from steptwin_api.schemas.routing import Coordinate, RoutingPreferences
from steptwin_api.services.pgrouting_micro_routing import (
    PgRoutingCostProfile,
    PgRoutingGraphConfig,
    PgRoutingSnappedEndpoint,
    build_pgrouting_route_query,
    build_route_from_rows,
    build_shortest_edges_sql,
    build_snap_endpoints_query,
    build_weighted_edges_sql,
)


def test_cost_profile_converts_user_preferences_to_edge_coefficients() -> None:
    profile = PgRoutingCostProfile.from_preferences(
        RoutingPreferences(
            avoid_stairs=True,
            shade_weight=0.8,
            stair_weight=1.5,
            slope_weight=0.7,
            corner_weight=0.4,
            crowding_weight=0.6,
            walking_speed_mps=1.2,
            max_extra_walk_ratio=0.25,
        )
    )

    assert profile.walking_speed_mps == 1.2
    assert profile.stair_penalty_seconds_per_count == 720
    assert profile.slope_penalty_seconds_per_meter_grade == pytest.approx(3.15)
    assert profile.corner_penalty_seconds_per_count == pytest.approx(7.2)
    assert profile.crowding_penalty_fraction == pytest.approx(0.36)
    assert profile.shade_reward_fraction == pytest.approx(0.28)
    assert profile.min_cost_fraction_of_base == 0.35
    assert profile.max_extra_walk_ratio == 0.25


def test_weighted_edges_sql_matches_pgrouting_contract() -> None:
    profile = PgRoutingCostProfile.from_preferences(
        RoutingPreferences(
            avoid_stairs=True,
            shade_weight=1,
            stair_weight=1,
            slope_weight=1,
            corner_weight=1,
            crowding_weight=1,
        )
    )

    sql = build_weighted_edges_sql(profile)

    assert 'edge."id"::bigint AS id' in sql
    assert 'edge."source"::bigint AS source' in sql
    assert 'edge."target"::bigint AS target' in sql
    assert "AS cost" in sql
    assert "AS reverse_cost" in sql
    assert "GREATEST(" in sql
    assert '* 600' in sql
    assert '* 4.5' in sql
    assert '* 18' in sql
    assert '* 0.6' in sql
    assert '* 0.35' in sql
    assert 'edge."crowding_score"' in sql
    assert 'edge."crossing_wait_seconds"' in sql
    assert 'COALESCE(edge."crossing_type", \'none\') <> \'none\'' in sql


def test_route_query_runs_weighted_and_shortest_candidates_in_one_statement() -> None:
    sql = build_pgrouting_route_query(PgRoutingGraphConfig(edge_table="public.walk_edges"))

    assert sql.count("pgr_dijkstra(") == 2
    assert "weighted_path AS" in sql
    assert "shortest_path AS" in sql
    assert "shortest_fallback" in sql
    assert "CAST(:max_extra_walk_ratio AS float8)" in sql
    assert '"public"."walk_edges"' in sql


def test_snap_query_ignores_vertices_not_connected_to_any_edge() -> None:
    sql = build_snap_endpoints_query(PgRoutingGraphConfig())

    assert "WHERE EXISTS" in sql
    assert 'FROM "osm_pedestrian_edges" AS edge' in sql
    assert 'edge."source" = candidate."id"' in sql
    assert 'edge."target" = candidate."id"' in sql


def test_invalid_identifiers_are_rejected_before_sql_generation() -> None:
    with pytest.raises(ValueError):
        build_shortest_edges_sql(PgRoutingGraphConfig(edge_table="walk_edges;DROP TABLE users"))


def test_build_route_from_rows_maps_geometry_and_metrics() -> None:
    start = Coordinate(latitude=37.0, longitude=126.0)
    end = Coordinate(latitude=37.2, longitude=126.2)
    snapped_start = PgRoutingSnappedEndpoint(vertex_id=1, coordinate=start, snap_distance_meters=0)
    snapped_end = PgRoutingSnappedEndpoint(vertex_id=3, coordinate=end, snap_distance_meters=0)
    rows = [
        {
            "route_kind": "weighted",
            "path_seq": 1,
            "node_id": 1,
            "edge_id": 10,
            "cost_seconds": 12.5,
            "agg_cost_seconds": 0.0,
            "distance_meters": 20.4,
            "stairs_count": 1,
            "shade_score": 0.8,
            "corner_count": 2,
            "slope_grade": 0.05,
            "crowding_score": 0.4,
            "geometry_geojson": (
                '{"type":"LineString","coordinates":[[126.0,37.0],[126.1,37.1]]}'
            ),
        },
        {
            "route_kind": "weighted",
            "path_seq": 2,
            "node_id": 2,
            "edge_id": 11,
            "cost_seconds": 14.5,
            "agg_cost_seconds": 12.5,
            "distance_meters": 29.6,
            "stairs_count": 0,
            "shade_score": 0.3,
            "corner_count": 1,
            "slope_grade": 0.02,
            "crowding_score": 0.2,
            "geometry_geojson": (
                '{"type":"LineString","coordinates":[[126.1,37.1],[126.2,37.2]]}'
            ),
        },
    ]

    route = build_route_from_rows(
        rows,
        requested_start=start,
        requested_end=end,
        snapped_start=snapped_start,
        snapped_end=snapped_end,
        walking_speed_mps=1.0,
        shade_marker_threshold=0.45,
    )

    assert route.route_kind == "weighted"
    assert route.total_cost_seconds == 27
    assert route.total_distance_meters == 50
    assert route.duration_seconds == 60
    assert route.stairs_count == 1
    assert route.shade_shelters == 1
    assert [step.edge_id for step in route.steps] == [10, 11]
    assert route.geometry == (
        Coordinate(latitude=37.0, longitude=126.0),
        Coordinate(latitude=37.1, longitude=126.1),
        Coordinate(latitude=37.2, longitude=126.2),
    )
