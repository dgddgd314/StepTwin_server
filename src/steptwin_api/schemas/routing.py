from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TravelMode = Literal["walk", "bus", "subway"]
SegmentKind = Literal["custom_walk", "transit"]
MarkerKind = Literal["shade_shelter", "stairs_avoided", "stop", "origin", "destination"]
LinePattern = Literal["solid", "dashed"]


class Coordinate(BaseModel):
    model_config = ConfigDict(frozen=True)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Place(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=100)
    coordinate: Coordinate


class RoutingPreferences(BaseModel):
    model_config = ConfigDict(frozen=True)

    avoid_stairs: bool = True
    shade_weight: float = Field(default=0.8, ge=0, le=1)
    stair_weight: float = Field(default=1.0, ge=0, le=3)
    slope_weight: float = Field(default=0.7, ge=0, le=3)
    corner_weight: float = Field(default=0.4, ge=0, le=3)
    walking_speed_mps: float = Field(default=1.15, gt=0, le=2.5)
    max_extra_walk_ratio: float = Field(default=0.2, ge=0, le=1)


class RoutePreviewRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    origin: Place
    destination: Place
    preferences: RoutingPreferences = Field(default_factory=RoutingPreferences)


class RenderStyle(BaseModel):
    model_config = ConfigDict(frozen=True)

    color: str
    width: int = Field(ge=1, le=16)
    pattern: LinePattern


class TransitDetails(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["bus", "subway"]
    route_name: str
    bus_number: str | None = None
    subway_line: str | None = None
    boarding_stop: str
    alighting_stop: str
    headsign: str | None = None


class SegmentMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    distance_meters: int = Field(ge=0)
    duration_seconds: int = Field(ge=0)
    shade_shelters: int = Field(ge=0)
    stairs_avoided: int = Field(ge=0)


class RouteSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: SegmentKind
    mode: TravelMode
    title: str
    geometry: list[Coordinate] = Field(min_length=2)
    render: RenderStyle
    metrics: SegmentMetrics
    transit: TransitDetails | None = None


class RouteMarker(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: MarkerKind
    title: str
    coordinate: Coordinate
    segment_id: str | None = None
    icon: str


class RouteSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_distance_meters: int = Field(ge=0)
    total_duration_seconds: int = Field(ge=0)
    walking_distance_meters: int = Field(ge=0)
    transit_distance_meters: int = Field(ge=0)
    shade_shelters: int = Field(ge=0)
    stairs_avoided: int = Field(ge=0)


class Viewport(BaseModel):
    model_config = ConfigDict(frozen=True)

    southwest: Coordinate
    northeast: Coordinate


class RouteDebug(BaseModel):
    model_config = ConfigDict(frozen=True)

    macro_router: str
    micro_router: str
    tmap_live_sync: bool
    note: str


class RoutePreviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    route_id: UUID
    summary: RouteSummary
    segments: list[RouteSegment] = Field(min_length=1)
    markers: list[RouteMarker]
    viewport: Viewport
    debug: RouteDebug
