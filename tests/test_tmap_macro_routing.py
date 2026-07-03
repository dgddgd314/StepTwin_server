import pytest
from pydantic import ValidationError

from steptwin_api.core.config import Settings
from steptwin_api.schemas.routing import Coordinate, Place, TransitDetails
from steptwin_api.services.macro_routing import (
    TransitLegSkeleton,
    TransitSkeleton,
    build_tmap_route_request_body,
    build_tmap_route_request_headers,
    parse_tmap_route_payload,
    transit_route_score_seconds,
)


def test_tmap_route_request_uses_confirmed_headers_and_body_fields() -> None:
    settings = Settings(tmap_app_key="secret")
    origin = Place(
        name="Sinchon Station Line 2",
        coordinate=Coordinate(latitude=37.555162, longitude=126.936928),
    )
    destination = Place(
        name="Sangwangsimni Station Line 2",
        coordinate=Coordinate(latitude=37.564436, longitude=127.029281),
    )

    headers = build_tmap_route_request_headers(settings, "secret")
    body = build_tmap_route_request_body(origin, destination, settings)

    assert headers == {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "appKey": "secret",
    }
    assert body == {
        "startX": "126.936928",
        "startY": "37.555162",
        "endX": "127.029281",
        "endY": "37.564436",
        "lang": 0,
        "format": "json",
        "count": 10,
    }


def test_tmap_route_request_supports_optional_time_machine_parameters() -> None:
    settings = Settings(
        tmap_app_key="secret",
        tmap_lang=1,
        tmap_format="xml",
        tmap_count=3,
        tmap_search_dttm="202301011200",
    )
    origin = Place(name="Origin", coordinate=Coordinate(latitude=37.1, longitude=126.1))
    destination = Place(name="Destination", coordinate=Coordinate(latitude=37.2, longitude=126.2))

    headers = build_tmap_route_request_headers(settings, "secret")
    body = build_tmap_route_request_body(origin, destination, settings)

    assert headers["Accept"] == "application/xml"
    assert body["lang"] == 1
    assert body["format"] == "xml"
    assert body["count"] == 3
    assert body["searchDttm"] == "202301011200"


@pytest.mark.parametrize(
    "search_dttm",
    [
        "20230101126",
        "202313011200",
        "202301321200",
        "202301012400",
        "202301011260",
    ],
)
def test_tmap_search_dttm_rejects_invalid_values(search_dttm: str) -> None:
    with pytest.raises(ValidationError):
        Settings(tmap_app_key="secret", tmap_search_dttm=search_dttm)


def test_parse_tmap_route_payload_builds_transit_skeleton_from_response_nodes() -> None:
    payload = {
        "metaData": {
            "plan": {
                "itineraries": [
                    {
                        "totalTime": 1500,
                        "transferCount": 1,
                        "totalWalkDistance": 300,
                        "totalDistance": 5200,
                        "totalWalkTime": 240,
                        "fare": {
                            "regular": {
                                "totalFare": 1550,
                                "currency": {
                                    "symbol": "W",
                                    "currency": "won",
                                    "currencyCode": "KRW",
                                },
                            }
                        },
                        "legs": [
                            {
                                "mode": "WALK",
                                "distance": 120,
                                "sectionTime": 90,
                                "start": {
                                    "name": "Origin",
                                    "lon": 126.936928,
                                    "lat": 37.555162,
                                },
                                "end": {
                                    "name": "Board",
                                    "lon": 126.938,
                                    "lat": 37.556,
                                },
                            },
                            {
                                "mode": "SUBWAY",
                                "route": "Line 2",
                                "routeColor": "00A84D",
                                "routeId": "SUBWAY:2",
                                "type": 2,
                                "service": 1,
                                "distance": 3000,
                                "sectionTime": 600,
                                "start": {
                                    "name": "Sinchon",
                                    "lon": 126.936928,
                                    "lat": 37.555162,
                                },
                                "end": {
                                    "name": "Wangsimni",
                                    "lon": 127.037,
                                    "lat": 37.561,
                                },
                                "passShape": {
                                    "linestring": (
                                        "126.936928,37.555162 126.98,37.558 "
                                        "127.037,37.561"
                                    )
                                },
                            },
                            {
                                "mode": "BUS",
                                "route": "Blue 2012",
                                "routeColor": "0068B7",
                                "routeId": "BUS:2012",
                                "type": 11,
                                "service": 1,
                                "distance": 1800,
                                "sectionTime": 420,
                                "start": {
                                    "name": "Wangsimni",
                                    "lon": 127.037,
                                    "lat": 37.561,
                                },
                                "end": {
                                    "name": "Destination Stop",
                                    "lon": 127.029281,
                                    "lat": 37.564436,
                                },
                                "passShape": {
                                    "linestring": (
                                        "127.037,37.561 127.033,37.563 "
                                        "127.029281,37.564436"
                                    )
                                },
                            },
                            {
                                "mode": "WALK",
                                "distance": 180,
                                "sectionTime": 150,
                                "start": {
                                    "name": "Destination Stop",
                                    "lon": 127.029281,
                                    "lat": 37.564436,
                                },
                                "end": {
                                    "name": "Destination",
                                    "lon": 127.03,
                                    "lat": 37.565,
                                },
                            },
                        ],
                    }
                ],
                "pathType": 3,
            },
            "requestParameters": {
                "reqDttm": "20260702120000",
                "startX": "126.936928",
                "startY": "37.555162",
                "endX": "127.029281",
                "endY": "37.564436",
                "locale": "ko",
            },
        }
    }

    skeleton = parse_tmap_route_payload(payload)

    assert skeleton is not None
    assert skeleton.boarding_stop.name == "Sinchon"
    assert skeleton.alighting_stop.name == "Destination Stop"
    assert skeleton.transit.mode == "subway"
    assert skeleton.transit.route_name == "Line 2 + Blue 2012"
    assert skeleton.transit.subway_line == "Line 2"
    assert skeleton.transit.bus_number == "2012"
    assert len(skeleton.transit_legs) == 2
    assert [leg.transit.mode for leg in skeleton.transit_legs] == ["subway", "bus"]
    assert [leg.transit.subway_line for leg in skeleton.transit_legs] == ["Line 2", None]
    assert [leg.transit.bus_number for leg in skeleton.transit_legs] == [None, "2012"]
    assert [leg.render_color for leg in skeleton.transit_legs] == ["#00A84D", "#0068B7"]
    assert skeleton.render_color == "#00A84D"
    assert skeleton.distance_meters == 4800
    assert skeleton.duration_seconds == 1020
    assert skeleton.geometry[0].longitude == 126.936928
    assert skeleton.geometry[-1].latitude == 37.564436
    assert len(skeleton.geometry) == 5


def test_parse_tmap_route_payload_returns_none_when_no_transit_leg_exists() -> None:
    payload = {"metaData": {"plan": {"itineraries": [{"legs": [{"mode": "WALK"}]}]}}}

    assert parse_tmap_route_payload(payload) is None


def test_parse_tmap_route_payload_penalizes_transfer_heavy_bus_routes() -> None:
    bus_leg = {
        "mode": "BUS",
        "route": "Blue 2012",
        "routeColor": "0068B7",
        "routeId": "BUS:2012",
        "distance": 600,
        "sectionTime": 180,
        "start": {"name": "Bus Start", "lon": 127.0, "lat": 37.0},
        "end": {"name": "Bus End", "lon": 127.01, "lat": 37.01},
    }
    payload = {
        "metaData": {
            "plan": {
                "itineraries": [
                    {
                        "legs": [
                            bus_leg,
                            {
                                **bus_leg,
                                "route": "Green 7017",
                                "routeId": "BUS:7017",
                                "start": {"name": "Bus End", "lon": 127.01, "lat": 37.01},
                                "end": {"name": "Transfer", "lon": 127.02, "lat": 37.02},
                            },
                            {
                                **bus_leg,
                                "route": "Town 03",
                                "routeId": "BUS:03",
                                "start": {"name": "Transfer", "lon": 127.02, "lat": 37.02},
                                "end": {"name": "Destination Stop", "lon": 127.03, "lat": 37.03},
                            },
                        ]
                    },
                    {
                        "legs": [
                            {
                                "mode": "SUBWAY",
                                "route": "Line 2",
                                "routeColor": "00A84D",
                                "routeId": "SUBWAY:2",
                                "distance": 3000,
                                "sectionTime": 1200,
                                "start": {"name": "Subway Start", "lon": 127.0, "lat": 37.0},
                                "end": {
                                    "name": "Subway End",
                                    "lon": 127.03,
                                    "lat": 37.03,
                                },
                            }
                        ]
                    },
                ]
            }
        }
    }

    skeleton = parse_tmap_route_payload(payload)

    assert skeleton is not None
    assert skeleton.transit.route_name == "Line 2"
    assert skeleton.duration_seconds == 1200
    assert transit_route_score_seconds(skeleton) == 1290


def test_transit_route_score_penalizes_single_bus_boarding() -> None:
    start = Place(name="Bus Start", coordinate=Coordinate(latitude=37.0, longitude=127.0))
    end = Place(name="Bus End", coordinate=Coordinate(latitude=37.01, longitude=127.01))
    bus_leg = TransitLegSkeleton(
        boarding_stop=start,
        alighting_stop=end,
        geometry=[start.coordinate, end.coordinate],
        transit=TransitDetails(
            mode="bus",
            route_name="Blue 1",
            bus_number="1",
            boarding_stop=start.name,
            alighting_stop=end.name,
        ),
        distance_meters=1000,
        duration_seconds=600,
        render_color="#0068B7",
    )
    skeleton = TransitSkeleton(
        boarding_stop=start,
        alighting_stop=end,
        geometry=bus_leg.geometry,
        transit=bus_leg.transit,
        distance_meters=1000,
        duration_seconds=600,
        render_color="#0068B7",
        transit_legs=(bus_leg,),
    )

    assert transit_route_score_seconds(skeleton) == 1140


def test_transit_route_score_penalizes_bus_to_bus_transfer_more_than_bus_to_subway() -> None:
    first_start = Place(name="First Start", coordinate=Coordinate(latitude=37.0, longitude=127.0))
    transfer = Place(name="Transfer", coordinate=Coordinate(latitude=37.01, longitude=127.01))
    second_end = Place(name="Second End", coordinate=Coordinate(latitude=37.02, longitude=127.02))
    bus_leg = TransitLegSkeleton(
        boarding_stop=first_start,
        alighting_stop=transfer,
        geometry=[first_start.coordinate, transfer.coordinate],
        transit=TransitDetails(
            mode="bus",
            route_name="Blue 1",
            bus_number="1",
            boarding_stop=first_start.name,
            alighting_stop=transfer.name,
        ),
        distance_meters=1000,
        duration_seconds=300,
        render_color="#0068B7",
    )
    second_bus_leg = TransitLegSkeleton(
        boarding_stop=transfer,
        alighting_stop=second_end,
        geometry=[transfer.coordinate, second_end.coordinate],
        transit=TransitDetails(
            mode="bus",
            route_name="Blue 2",
            bus_number="2",
            boarding_stop=transfer.name,
            alighting_stop=second_end.name,
        ),
        distance_meters=1000,
        duration_seconds=300,
        render_color="#0068B7",
    )
    subway_leg = TransitLegSkeleton(
        boarding_stop=transfer,
        alighting_stop=second_end,
        geometry=[transfer.coordinate, second_end.coordinate],
        transit=TransitDetails(
            mode="subway",
            route_name="Line 1",
            subway_line="Line 1",
            boarding_stop=transfer.name,
            alighting_stop=second_end.name,
        ),
        distance_meters=1000,
        duration_seconds=300,
        render_color="#0052A4",
    )

    bus_to_bus = TransitSkeleton(
        boarding_stop=first_start,
        alighting_stop=second_end,
        geometry=[first_start.coordinate, transfer.coordinate, second_end.coordinate],
        transit=bus_leg.transit,
        distance_meters=2000,
        duration_seconds=600,
        render_color="#0068B7",
        transit_legs=(bus_leg, second_bus_leg),
    )
    bus_to_subway = TransitSkeleton(
        boarding_stop=first_start,
        alighting_stop=second_end,
        geometry=[first_start.coordinate, transfer.coordinate, second_end.coordinate],
        transit=bus_leg.transit,
        distance_meters=2000,
        duration_seconds=600,
        render_color="#0068B7",
        transit_legs=(bus_leg, subway_leg),
    )

    assert transit_route_score_seconds(bus_to_subway) == 1320
    assert transit_route_score_seconds(bus_to_bus) == 1800
