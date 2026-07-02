import pytest

from steptwin_api.services.osm_walk_network import (
    build_osm_edge_id,
    build_osm_edge_rows,
    build_overpass_walk_network_query,
    infer_roughness_score,
    infer_slope_grade,
    is_walkable_way,
    parse_overpass_walk_network_payload,
)

SAMPLE_OVERPASS_PAYLOAD = {
    "elements": [
        {"type": "node", "id": 1, "lat": 37.0, "lon": 127.0},
        {"type": "node", "id": 2, "lat": 37.0001, "lon": 127.0001},
        {"type": "node", "id": 3, "lat": 37.0002, "lon": 127.0002},
        {
            "type": "way",
            "id": 100,
            "nodes": [1, 2, 3],
            "tags": {"highway": "footway", "name": "campus walk"},
        },
        {
            "type": "way",
            "id": 200,
            "nodes": [1, 3],
            "tags": {"highway": "motorway"},
        },
    ]
}


def test_build_overpass_query_uses_bbox_and_pedestrian_filters() -> None:
    query = build_overpass_walk_network_query(
        south=37.58,
        west=127.04,
        north=37.60,
        east=127.07,
    )

    assert "[out:json]" in query
    assert "37.58,127.04,37.6,127.07" in query
    assert 'way["highway"~' in query
    assert "footway" in query


def test_build_overpass_query_rejects_invalid_bbox() -> None:
    with pytest.raises(ValueError):
        build_overpass_walk_network_query(south=37.6, west=127.04, north=37.58, east=127.07)


def test_parse_overpass_payload_keeps_only_walkable_ways() -> None:
    dataset = parse_overpass_walk_network_payload(
        SAMPLE_OVERPASS_PAYLOAD,
        name="sample",
        bbox=(37.0, 127.0, 37.1, 127.1),
    )

    assert len(dataset.nodes) == 3
    assert [way.id for way in dataset.ways] == [100]


def test_build_osm_edge_rows_splits_ways_into_graph_edges() -> None:
    dataset = parse_overpass_walk_network_payload(
        SAMPLE_OVERPASS_PAYLOAD,
        name="sample",
        bbox=(37.0, 127.0, 37.1, 127.1),
    )

    rows, skipped_count = build_osm_edge_rows(dataset)

    assert skipped_count == 0
    assert [row["id"] for row in rows] == [build_osm_edge_id(100, 0), build_osm_edge_id(100, 1)]
    assert [row["source"] for row in rows] == [1, 2]
    assert [row["target"] for row in rows] == [2, 3]
    distances = [row["distance_meters"] for row in rows]
    assert all(isinstance(distance, int | float) and distance > 0 for distance in distances)


def test_crossing_edges_get_crossing_length_without_wait_seed() -> None:
    dataset = parse_overpass_walk_network_payload(
        {
            "elements": [
                {"type": "node", "id": 1, "lat": 37.0, "lon": 127.0},
                {"type": "node", "id": 2, "lat": 37.0001, "lon": 127.0001},
                {
                    "type": "way",
                    "id": 300,
                    "nodes": [1, 2],
                    "tags": {
                        "highway": "footway",
                        "footway": "crossing",
                        "crossing": "traffic_signals",
                    },
                },
            ]
        },
        name="sample",
        bbox=(37.0, 127.0, 37.1, 127.1),
    )

    rows, skipped_count = build_osm_edge_rows(dataset)

    assert skipped_count == 0
    assert rows[0]["crossing_type"] == "signalized"
    assert rows[0]["crossing_length_meters"] == rows[0]["distance_meters"]
    assert rows[0]["crossing_wait_seconds"] is None


def test_walkable_way_filter_blocks_private_and_non_pedestrian_highways() -> None:
    assert is_walkable_way({"highway": "footway"})
    assert is_walkable_way({"highway": "residential"})
    assert not is_walkable_way({"highway": "motorway"})
    assert not is_walkable_way({"highway": "footway", "access": "private"})
    assert not is_walkable_way({"highway": "primary", "sidewalk": "no"})


def test_osm_roughness_and_slope_are_derived_from_tags() -> None:
    assert infer_roughness_score({"surface": "asphalt"}) == 0.1
    assert infer_roughness_score({"smoothness": "very_bad"}) == 0.65
    assert infer_roughness_score({"highway": "steps"}) == 0.85
    assert infer_slope_grade({"incline": "12%"}) == 0.12
    assert infer_slope_grade({"incline": "-8%"}) == 0.08
