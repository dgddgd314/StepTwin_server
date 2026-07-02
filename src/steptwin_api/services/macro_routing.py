import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from steptwin_api.core.config import Settings
from steptwin_api.schemas.routing import Coordinate, Place, TransitDetails
from steptwin_api.services.geometry import distance_meters, interpolate

JsonValue = str | int
TmapNormalizedMode = Literal["bus", "subway"]
TRANSFER_PENALTY_SECONDS = 180
BOARDING_PENALTY_SECONDS = 90
BUS_LEG_PENALTY_SECONDS = 60
BUS_BOARDING_PENALTY_SECONDS = 210
BUS_TO_BUS_TRANSFER_PENALTY_SECONDS = 120
BUS_DURATION_PENALTY_FRACTION = 0.30


@dataclass(frozen=True)
class TransitLegSkeleton:
    boarding_stop: Place
    alighting_stop: Place
    geometry: list[Coordinate]
    transit: TransitDetails
    distance_meters: int
    duration_seconds: int
    render_color: str = "#2563EB"


@dataclass(frozen=True)
class TransitSkeleton:
    boarding_stop: Place
    alighting_stop: Place
    geometry: list[Coordinate]
    transit: TransitDetails
    distance_meters: int
    duration_seconds: int
    render_color: str = "#2563EB"
    transit_legs: tuple[TransitLegSkeleton, ...] = ()


class DemoMacroRouter:
    """TMAP public-transit adapter seam.

    The PoC implementation generates deterministic transit anchors. The production implementation
    should replace this class with a TMAP client that returns the same internal skeleton shape.
    """

    def build_transit_skeleton(self, origin: Place, destination: Place) -> TransitSkeleton:
        boarding_coordinate = interpolate(origin.coordinate, destination.coordinate, 0.32)
        alighting_coordinate = interpolate(origin.coordinate, destination.coordinate, 0.72)

        boarding_stop = Place(name="StepTwin Demo Station", coordinate=boarding_coordinate)
        alighting_stop = Place(name="Sunshade Transfer Stop", coordinate=alighting_coordinate)
        geometry = [
            boarding_coordinate,
            interpolate(boarding_coordinate, alighting_coordinate, 0.25),
            interpolate(boarding_coordinate, alighting_coordinate, 0.5),
            interpolate(boarding_coordinate, alighting_coordinate, 0.75),
            alighting_coordinate,
        ]
        distance = distance_meters(geometry)

        leg = TransitLegSkeleton(
            boarding_stop=boarding_stop,
            alighting_stop=alighting_stop,
            geometry=geometry,
            transit=TransitDetails(
                mode="subway",
                route_name="Demo Transit Line",
                subway_line="Demo Transit Line",
                boarding_stop=boarding_stop.name,
                alighting_stop=alighting_stop.name,
                headsign=destination.name,
            ),
            distance_meters=distance,
            duration_seconds=estimate_transit_seconds(distance),
            render_color="#2563EB",
        )
        return TransitSkeleton(
            boarding_stop=boarding_stop,
            alighting_stop=alighting_stop,
            geometry=geometry,
            transit=leg.transit,
            distance_meters=leg.distance_meters,
            duration_seconds=leg.duration_seconds,
            render_color=leg.render_color,
            transit_legs=(leg,),
        )


class TmapMacroRouter:
    """Live TMAP public-transit adapter boundary.

    Keep all TMAP-specific request/response handling here so the Android contract stays stable.
    """

    def __init__(
        self,
        settings: Settings,
        fallback_router: DemoMacroRouter | None = None,
    ) -> None:
        if settings.tmap_app_key is None:
            raise ValueError("TMAP_APP_KEY is required when TMAP_USE_LIVE=true")

        self._settings = settings
        self._app_key = settings.tmap_app_key
        self._fallback_router = fallback_router or DemoMacroRouter()

    def build_transit_skeleton(self, origin: Place, destination: Place) -> TransitSkeleton:
        payload = self._request_route(origin, destination)
        parsed = self._parse_route_payload(payload)

        if parsed is not None:
            return parsed

        return self._fallback_router.build_transit_skeleton(origin, destination)

    def _request_route(self, origin: Place, destination: Place) -> dict[str, Any]:
        base_url = self._settings.tmap_base_url.rstrip("/")
        transit_path = self._settings.tmap_transit_path.lstrip("/")
        url = f"{base_url}/{transit_path}"
        body = build_tmap_route_request_body(origin, destination, self._settings)
        headers = build_tmap_route_request_headers(self._settings, self._app_key)

        with httpx.Client(timeout=self._settings.tmap_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()

            if self._settings.tmap_format == "xml":
                return {"_raw_xml": response.text}

            payload = response.json()

        if not isinstance(payload, dict):
            raise ValueError("TMAP response must be a JSON object")

        return payload

    def _parse_route_payload(self, payload: dict[str, Any]) -> TransitSkeleton | None:
        return parse_tmap_route_payload(payload)


def estimate_transit_seconds(distance: int) -> int:
    return max(round(distance / 8.5), 180)


def parse_tmap_route_payload(payload: dict[str, Any]) -> TransitSkeleton | None:
    itineraries = get_nested_list(payload, "metaData", "plan", "itineraries")
    if itineraries is None:
        return None

    candidates: list[TransitSkeleton] = []
    for itinerary in itineraries:
        if not isinstance(itinerary, dict):
            continue

        skeleton = parse_tmap_itinerary(itinerary)
        if skeleton is not None:
            candidates.append(skeleton)

    if not candidates:
        return None

    return min(candidates, key=transit_route_score_seconds)


def transit_route_score_seconds(skeleton: TransitSkeleton) -> int:
    boarding_count = len(skeleton.transit_legs) if skeleton.transit_legs else 1
    transfer_count = max(boarding_count - 1, 0)
    bus_leg_count = sum(1 for leg in skeleton.transit_legs if leg.transit.mode == "bus")
    bus_duration_seconds = sum(
        leg.duration_seconds for leg in skeleton.transit_legs if leg.transit.mode == "bus"
    )
    bus_to_bus_transfer_count = sum(
        1
        for previous_leg, next_leg in zip(
            skeleton.transit_legs,
            skeleton.transit_legs[1:],
            strict=False,
        )
        if previous_leg.transit.mode == "bus" and next_leg.transit.mode == "bus"
    )
    return (
        skeleton.duration_seconds
        + transfer_count * TRANSFER_PENALTY_SECONDS
        + boarding_count * BOARDING_PENALTY_SECONDS
        + bus_leg_count * BUS_LEG_PENALTY_SECONDS
        + bus_leg_count * BUS_BOARDING_PENALTY_SECONDS
        + bus_to_bus_transfer_count * BUS_TO_BUS_TRANSFER_PENALTY_SECONDS
        + round(bus_duration_seconds * BUS_DURATION_PENALTY_FRACTION)
    )


def parse_tmap_itinerary(itinerary: dict[str, Any]) -> TransitSkeleton | None:
    legs_value = itinerary.get("legs")
    if not isinstance(legs_value, list):
        return None

    transit_legs = [
        leg
        for leg in legs_value
        if isinstance(leg, dict) and normalize_tmap_mode(leg.get("mode")) is not None
    ]
    if not transit_legs:
        return None

    parsed_legs = tuple(
        parsed_leg
        for leg in transit_legs
        if (parsed_leg := parse_tmap_transit_leg(leg)) is not None
    )
    if not parsed_legs:
        return None

    boarding_stop = parsed_legs[0].boarding_stop
    alighting_stop = parsed_legs[-1].alighting_stop
    geometry = combine_tmap_transit_leg_geometries(parsed_legs)
    if len(geometry) < 2:
        return None

    route_names = compact_unique_strings(leg.transit.route_name for leg in parsed_legs)
    duration = sum(leg.duration_seconds for leg in parsed_legs)
    distance = sum(leg.distance_meters for leg in parsed_legs)

    return TransitSkeleton(
        boarding_stop=boarding_stop,
        alighting_stop=alighting_stop,
        geometry=geometry,
        transit=TransitDetails(
            mode=parsed_legs[0].transit.mode,
            route_name=(
                " + ".join(route_names) if route_names else parsed_legs[0].transit.mode.upper()
            ),
            bus_number=build_combined_mode_value(
                parsed_legs,
                mode="bus",
                field_name="bus_number",
            ),
            subway_line=build_combined_mode_value(
                parsed_legs,
                mode="subway",
                field_name="subway_line",
            ),
            boarding_stop=boarding_stop.name,
            alighting_stop=alighting_stop.name,
            headsign=alighting_stop.name,
        ),
        distance_meters=distance if distance > 0 else distance_meters(geometry),
        duration_seconds=duration
        if duration > 0
        else estimate_transit_seconds(distance_meters(geometry)),
        render_color=parsed_legs[0].render_color,
        transit_legs=parsed_legs,
    )


def parse_tmap_transit_leg(leg: dict[str, Any]) -> TransitLegSkeleton | None:
    mode = normalize_tmap_mode(leg.get("mode"))
    boarding_stop = parse_tmap_place(leg.get("start"))
    alighting_stop = parse_tmap_place(leg.get("end"))
    if mode is None or boarding_stop is None or alighting_stop is None:
        return None

    geometry = build_tmap_single_leg_geometry(leg, boarding_stop, alighting_stop)
    if len(geometry) < 2:
        return None

    route_name = stringify(leg.get("route")) or mode.upper()
    route_id = stringify(leg.get("routeId"))
    distance = sum_int_field([leg], "distance")
    duration = sum_int_field([leg], "sectionTime")

    return TransitLegSkeleton(
        boarding_stop=boarding_stop,
        alighting_stop=alighting_stop,
        geometry=geometry,
        transit=TransitDetails(
            mode=mode,
            route_name=route_name,
            bus_number=extract_bus_number(route_name, route_id) if mode == "bus" else None,
            subway_line=extract_subway_line(route_name, route_id) if mode == "subway" else None,
            boarding_stop=boarding_stop.name,
            alighting_stop=alighting_stop.name,
            headsign=alighting_stop.name,
        ),
        distance_meters=distance if distance > 0 else distance_meters(geometry),
        duration_seconds=(
            duration if duration > 0 else estimate_transit_seconds(distance_meters(geometry))
        ),
        render_color=normalize_route_color(leg.get("routeColor")) or default_route_color(mode),
    )


def build_combined_mode_value(
    legs: tuple[TransitLegSkeleton, ...],
    *,
    mode: TmapNormalizedMode,
    field_name: Literal["bus_number", "subway_line"],
) -> str | None:
    values = compact_unique_strings(
        getattr(leg.transit, field_name) for leg in legs if leg.transit.mode == mode
    )
    return " + ".join(values) if values else None


def extract_bus_number(route_name: str, route_id: str | None) -> str:
    route_name_match = re.search(r"[A-Za-z가-힣]*\d+[A-Za-z가-힣\d-]*", route_name)
    if route_name_match is not None:
        return route_name_match.group(0)

    route_id_value = route_id_suffix(route_id)
    return route_id_value or route_name


def extract_subway_line(route_name: str, route_id: str | None) -> str:
    if route_name.upper() != "SUBWAY":
        return route_name

    route_id_value = route_id_suffix(route_id)
    return route_id_value or route_name


def route_id_suffix(route_id: str | None) -> str | None:
    if route_id is None:
        return None

    suffix = route_id.rsplit(":", maxsplit=1)[-1].strip()
    return suffix or None


def default_route_color(mode: TmapNormalizedMode) -> str:
    if mode == "bus":
        return "#0068B7"

    return "#2563EB"


def normalize_tmap_mode(value: object) -> TmapNormalizedMode | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip().upper()
    if normalized == "SUBWAY":
        return "subway"
    if normalized in {"BUS", "EXPRESS BUS"}:
        return "bus"

    return None


def first_valid_route_color(transit_legs: list[dict[str, Any]]) -> str | None:
    for leg in transit_legs:
        color = normalize_route_color(leg.get("routeColor"))
        if color is not None:
            return color

    return None


def normalize_route_color(value: object) -> str | None:
    text = stringify(value)
    if text is None:
        return None

    stripped = text.strip().lstrip("#")
    if len(stripped) != 6:
        return None
    if any(character not in "0123456789abcdefABCDEF" for character in stripped):
        return None

    return f"#{stripped.upper()}"


def parse_tmap_place(value: object) -> Place | None:
    if not isinstance(value, dict):
        return None

    coordinate = parse_tmap_coordinate(value)
    name = stringify(value.get("name"))
    if coordinate is None or name is None:
        return None

    return Place(name=name, coordinate=coordinate)


def parse_tmap_coordinate(value: dict[str, Any]) -> Coordinate | None:
    lon = parse_float(value.get("lon"))
    lat = parse_float(value.get("lat"))
    if lon is None or lat is None:
        return None

    return Coordinate(latitude=lat, longitude=lon)


def build_tmap_single_leg_geometry(
    leg: dict[str, Any],
    boarding_stop: Place,
    alighting_stop: Place,
) -> list[Coordinate]:
    geometry = [boarding_stop.coordinate]
    pass_shape = leg.get("passShape")
    if isinstance(pass_shape, dict):
        geometry.extend(parse_tmap_linestring(pass_shape.get("linestring")))
    geometry.append(alighting_stop.coordinate)
    return dedupe_adjacent_coordinates(geometry)


def combine_tmap_transit_leg_geometries(legs: tuple[TransitLegSkeleton, ...]) -> list[Coordinate]:
    geometry: list[Coordinate] = []
    for leg in legs:
        geometry.extend(leg.geometry)

    return dedupe_adjacent_coordinates(geometry)


def parse_tmap_linestring(value: object) -> list[Coordinate]:
    text = stringify(value)
    if text is None:
        return []

    coordinates: list[Coordinate] = []
    for pair in text.replace(";", " ").split():
        lon_lat = pair.split(",")
        if len(lon_lat) != 2:
            continue

        lon = parse_float(lon_lat[0])
        lat = parse_float(lon_lat[1])
        if lon is not None and lat is not None:
            coordinates.append(Coordinate(latitude=lat, longitude=lon))

    return coordinates


def dedupe_adjacent_coordinates(coordinates: list[Coordinate]) -> list[Coordinate]:
    deduped: list[Coordinate] = []
    for coordinate in coordinates:
        if not deduped or coordinate != deduped[-1]:
            deduped.append(coordinate)

    return deduped


def get_nested_list(payload: dict[str, Any], *path: str) -> list[Any] | None:
    current: object = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)

    return current if isinstance(current, list) else None


def sum_int_field(items: list[dict[str, Any]], field: str) -> int:
    total = 0
    for item in items:
        value = parse_float(item.get(field))
        if value is not None:
            total += round(value)

    return total


def compact_unique_strings(values: Iterable[str | None]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value is not None and value not in unique:
            unique.append(value)

    return unique


def stringify(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, int | float):
        return str(value)

    return None


def parse_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None

    return None


def build_tmap_route_request_body(
    origin: Place,
    destination: Place,
    settings: Settings,
) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "startX": str(origin.coordinate.longitude),
        "startY": str(origin.coordinate.latitude),
        "endX": str(destination.coordinate.longitude),
        "endY": str(destination.coordinate.latitude),
        "lang": settings.tmap_lang,
        "format": settings.tmap_format,
        "count": settings.tmap_count,
    }

    if settings.tmap_search_dttm is not None:
        body["searchDttm"] = settings.tmap_search_dttm

    return body


def build_tmap_route_request_headers(settings: Settings, app_key: str) -> dict[str, str]:
    accept = "application/xml" if settings.tmap_format == "xml" else "application/json"
    return {
        "Accept": accept,
        "Content-Type": "application/json",
        "appKey": app_key,
    }
