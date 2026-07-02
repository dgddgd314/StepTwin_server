from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from steptwin_api.schemas.routing import Coordinate, Place, RoutingPreferences

WalkRouteKind = Literal["weighted", "shortest_fallback", "same_vertex"]


class WalkRouteOptimizeRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: Place
    end: Place
    preferences: RoutingPreferences = Field(default_factory=RoutingPreferences)


class WalkRouteSnappedEndpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    vertex_id: int
    coordinate: Coordinate
    snap_distance_meters: float = Field(ge=0)


class WalkRouteStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    path_seq: int = Field(ge=0)
    node_id: int
    edge_id: int
    cost_seconds: float = Field(ge=0)
    agg_cost_seconds: float = Field(ge=0)
    distance_meters: float = Field(ge=0)
    stairs_count: int = Field(ge=0)
    shade_score: float = Field(ge=0, le=1)
    corner_count: int = Field(ge=0)
    slope_grade: float = Field(ge=0)
    geometry: list[Coordinate] = Field(min_length=2)


class WalkRouteMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_cost_seconds: float = Field(ge=0)
    total_distance_meters: int = Field(ge=0)
    duration_seconds: int = Field(ge=0)
    stairs_count: int = Field(ge=0)
    shade_shelters: int = Field(ge=0)


class WalkRouteOptimizeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    route_kind: WalkRouteKind
    start: WalkRouteSnappedEndpoint
    end: WalkRouteSnappedEndpoint
    geometry: list[Coordinate] = Field(min_length=1)
    metrics: WalkRouteMetrics
    steps: list[WalkRouteStep]
