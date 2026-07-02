from math import atan2, cos, radians, sin, sqrt

from steptwin_api.schemas.routing import Coordinate

EARTH_RADIUS_METERS = 6_371_000


def interpolate(start: Coordinate, end: Coordinate, ratio: float) -> Coordinate:
    return Coordinate(
        latitude=start.latitude + (end.latitude - start.latitude) * ratio,
        longitude=start.longitude + (end.longitude - start.longitude) * ratio,
    )


def offset_coordinate(point: Coordinate, meters_north: float, meters_east: float) -> Coordinate:
    latitude_delta = meters_north / 111_320
    longitude_delta = meters_east / (111_320 * cos(radians(point.latitude)))

    return Coordinate(
        latitude=point.latitude + latitude_delta,
        longitude=point.longitude + longitude_delta,
    )


def perpendicular_offset(start: Coordinate, end: Coordinate, meters: float) -> tuple[float, float]:
    north = end.latitude - start.latitude
    east = end.longitude - start.longitude
    length = sqrt(north * north + east * east)

    if length == 0:
        return meters, 0

    return -east / length * meters, north / length * meters


def distance_meters(points: list[Coordinate]) -> int:
    total = 0.0
    for start, end in zip(points, points[1:], strict=False):
        total += haversine_meters(start, end)

    return round(total)


def haversine_meters(start: Coordinate, end: Coordinate) -> float:
    start_lat = radians(start.latitude)
    end_lat = radians(end.latitude)
    lat_delta = radians(end.latitude - start.latitude)
    lon_delta = radians(end.longitude - start.longitude)

    a = sin(lat_delta / 2) ** 2 + cos(start_lat) * cos(end_lat) * sin(lon_delta / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return EARTH_RADIUS_METERS * c


def viewport_for(
    points: list[Coordinate],
    padding_ratio: float = 0.08,
) -> tuple[Coordinate, Coordinate]:
    min_latitude = min(point.latitude for point in points)
    max_latitude = max(point.latitude for point in points)
    min_longitude = min(point.longitude for point in points)
    max_longitude = max(point.longitude for point in points)

    latitude_padding = max((max_latitude - min_latitude) * padding_ratio, 0.001)
    longitude_padding = max((max_longitude - min_longitude) * padding_ratio, 0.001)

    return (
        Coordinate(
            latitude=min_latitude - latitude_padding,
            longitude=min_longitude - longitude_padding,
        ),
        Coordinate(
            latitude=max_latitude + latitude_padding,
            longitude=max_longitude + longitude_padding,
        ),
    )
