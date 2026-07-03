from steptwin_api.schemas.routing import Coordinate, Place, RoutingPreferences
from steptwin_api.services.micro_routing import (
    DemoMicroRouter,
    PedestrianEdge,
    PedestrianCostProfile,
    build_segment_graph,
    edge_cost_seconds,
    find_weighted_path,
)


def test_weighted_micro_router_prefers_shade_and_stair_free_edges() -> None:
    start = Coordinate(latitude=37.555162, longitude=126.936928)
    end = Coordinate(latitude=37.564436, longitude=127.029281)
    graph = build_segment_graph(start, end)
    profile = PedestrianCostProfile.from_preferences(
        RoutingPreferences(
            avoid_stairs=True,
            shade_weight=1,
            stair_weight=1,
            slope_weight=1,
            corner_weight=0.3,
            max_extra_walk_ratio=1,
        )
    )

    path = find_weighted_path(graph, "start", "end", profile)

    assert path.stairs_count == 0
    assert path.shade_shelters >= 2
    assert [edge.feature for edge in path.edges] == ["shade", "shade", "shade"]


def test_stair_penalty_increases_edge_cost_for_accessibility_profile() -> None:
    start = Coordinate(latitude=37.555162, longitude=126.936928)
    end = Coordinate(latitude=37.564436, longitude=127.029281)
    graph = build_segment_graph(start, end)
    stairs_edge = next(edge for edge in graph.edges if edge.feature == "stairs")
    neutral = PedestrianCostProfile.from_preferences(
        RoutingPreferences(avoid_stairs=False, stair_weight=0, slope_weight=0, corner_weight=0)
    )
    accessible = PedestrianCostProfile.from_preferences(
        RoutingPreferences(avoid_stairs=True, stair_weight=2, slope_weight=0, corner_weight=0)
    )

    assert edge_cost_seconds(stairs_edge, accessible) > edge_cost_seconds(stairs_edge, neutral)


def test_slope_penalty_strongly_increases_steep_edge_cost() -> None:
    edge = PedestrianEdge(
        start_id="start",
        end_id="end",
        geometry=[
            Coordinate(latitude=37.0, longitude=127.0),
            Coordinate(latitude=37.0, longitude=127.001),
        ],
        distance_meters=100,
        slope_grade=0.15,
    )
    flat_profile = PedestrianCostProfile.from_preferences(
        RoutingPreferences(slope_weight=0, walking_speed_mps=1)
    )
    slope_sensitive_profile = PedestrianCostProfile.from_preferences(
        RoutingPreferences(slope_weight=1, walking_speed_mps=1)
    )

    assert edge_cost_seconds(edge, slope_sensitive_profile) - edge_cost_seconds(
        edge,
        flat_profile,
    ) == 360


def test_micro_router_returns_accessibility_markers_and_metrics() -> None:
    router = DemoMicroRouter()
    start = Place(
        name="Start",
        coordinate=Coordinate(latitude=37.555162, longitude=126.936928),
    )
    end = Place(
        name="End",
        coordinate=Coordinate(latitude=37.564436, longitude=127.029281),
    )

    route = router.build_custom_walk(
        segment_id="walk-test",
        start=start,
        end=end,
        title="Weighted walk",
        preferences=RoutingPreferences(
            avoid_stairs=True,
            shade_weight=1,
            max_extra_walk_ratio=1,
        ),
    )

    assert route.segment.metrics.stairs_avoided == 1
    assert route.segment.metrics.shade_shelters == 2
    assert route.segment.metrics.distance_meters > 0
    assert [marker.kind for marker in route.markers] == [
        "shade_shelter",
        "shade_shelter",
        "stairs_avoided",
    ]
