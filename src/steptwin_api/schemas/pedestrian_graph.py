from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from steptwin_api.schemas.routing import Coordinate

GraphNodeKind = Literal[
    "station_exit",
    "hospital_gate",
    "intersection",
    "crossing",
    "bus_stop",
    "entrance",
    "landmark",
    "waypoint",
]
CrossingType = Literal["none", "crosswalk", "signalized", "unsignalized", "underpass", "overpass"]
SurfaceType = Literal["unknown", "paved", "rough", "gravel", "stairs", "ramp"]


class PedestrianGraphVertex(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    coordinate: Coordinate
    kind: GraphNodeKind = "waypoint"
    name: str | None = Field(default=None, max_length=100)
    tags: dict[str, str] = Field(default_factory=dict)


class PedestrianGraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    source: int = Field(gt=0)
    target: int = Field(gt=0)
    geometry: list[Coordinate] = Field(min_length=2)
    distance_meters: float | None = Field(default=None, gt=0)
    stairs_count: int = Field(default=0, ge=0)
    shade_score: float = Field(default=0, ge=0, le=1)
    slope_grade: float = Field(default=0, ge=0)
    corner_count: int = Field(default=0, ge=0)
    crossing_type: CrossingType = "none"
    surface_type: SurfaceType = "unknown"
    width_meters: float | None = Field(default=None, gt=0)
    curb_cut: bool | None = None
    wheelchair_ok: bool | None = None
    bidirectional: bool = True
    name: str | None = Field(default=None, max_length=100)
    tags: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_endpoints(self) -> Self:
        if self.source == self.target:
            raise ValueError("edge source and target must be different vertices")

        return self


class PedestrianGraphDataset(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=100)
    version: str = Field(default="draft", min_length=1, max_length=40)
    vertices: list[PedestrianGraphVertex] = Field(min_length=2)
    edges: list[PedestrianGraphEdge] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph_references(self) -> Self:
        seen_vertex_ids: set[int] = set()
        duplicate_vertex_ids: set[int] = set()
        for vertex in self.vertices:
            if vertex.id in seen_vertex_ids:
                duplicate_vertex_ids.add(vertex.id)
            seen_vertex_ids.add(vertex.id)

        if duplicate_vertex_ids:
            raise ValueError(f"duplicate vertex ids: {sorted(duplicate_vertex_ids)}")

        seen_edge_ids: set[int] = set()
        duplicate_edge_ids: set[int] = set()
        for edge in self.edges:
            if edge.id in seen_edge_ids:
                duplicate_edge_ids.add(edge.id)
            seen_edge_ids.add(edge.id)

        if duplicate_edge_ids:
            raise ValueError(f"duplicate edge ids: {sorted(duplicate_edge_ids)}")

        missing_vertex_ids = sorted(
            {
                endpoint_id
                for edge in self.edges
                for endpoint_id in (edge.source, edge.target)
                if endpoint_id not in seen_vertex_ids
            }
        )
        if missing_vertex_ids:
            raise ValueError(f"edge references unknown vertex ids: {missing_vertex_ids}")

        return self


class PedestrianGraphValidationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    vertex_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    total_declared_distance_meters: int = Field(ge=0)
    total_computed_distance_meters: int = Field(ge=0)
    stairs_edge_count: int = Field(ge=0)
    shaded_edge_count: int = Field(ge=0)
    crossing_edge_count: int = Field(ge=0)
    wheelchair_blocked_edge_count: int = Field(ge=0)
    missing_distance_edge_count: int = Field(ge=0)
    route_ready: bool


class PedestrianGraphValidationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_name: str
    dataset_version: str
    summary: PedestrianGraphValidationSummary
    warnings: list[str]


class PedestrianGraphImportRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset: PedestrianGraphDataset
    replace_existing: bool = False


class PedestrianGraphImportResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_name: str
    dataset_version: str
    vertex_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    computed_distance_edge_count: int = Field(ge=0)
    replaced_existing: bool
    vertex_table: str
    edge_table: str
    ready_for_routing: bool
    warnings: list[str]
