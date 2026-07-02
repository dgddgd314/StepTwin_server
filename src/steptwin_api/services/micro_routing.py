from collections.abc import Callable
from dataclasses import dataclass, field
from heapq import heappop, heappush

from steptwin_api.schemas.routing import (
    Coordinate,
    Place,
    RenderStyle,
    RouteMarker,
    RouteSegment,
    RoutingPreferences,
    SegmentMetrics,
)
from steptwin_api.services.geometry import (
    distance_meters,
    interpolate,
    offset_coordinate,
    perpendicular_offset,
)


@dataclass(frozen=True)
class WalkingRoute:
    segment: RouteSegment
    markers: list[RouteMarker]


@dataclass(frozen=True)
class PedestrianNode:
    id: str
    coordinate: Coordinate


@dataclass(frozen=True)
class PedestrianEdge:
    start_id: str
    end_id: str
    geometry: list[Coordinate]
    distance_meters: int
    stairs_count: int = 0
    shade_score: float = 0
    corner_count: int = 0
    slope_grade: float = 0
    crowding_score: float = 0
    feature: str | None = None


@dataclass(frozen=True)
class PedestrianGraph:
    nodes: dict[str, PedestrianNode]
    edges: list[PedestrianEdge]
    adjacency: dict[str, list[PedestrianEdge]] = field(init=False)

    def __post_init__(self) -> None:
        adjacency: dict[str, list[PedestrianEdge]] = {node_id: [] for node_id in self.nodes}
        for edge in self.edges:
            adjacency[edge.start_id].append(edge)
        object.__setattr__(self, "adjacency", adjacency)


@dataclass(frozen=True)
class PedestrianPath:
    edges: list[PedestrianEdge]

    @property
    def geometry(self) -> list[Coordinate]:
        points: list[Coordinate] = []
        for edge in self.edges:
            for point in edge.geometry:
                if not points or points[-1] != point:
                    points.append(point)

        return points

    @property
    def distance_meters(self) -> int:
        return sum(edge.distance_meters for edge in self.edges)

    @property
    def shade_shelters(self) -> int:
        return min(sum(1 for edge in self.edges if edge.shade_score >= 0.45), 2)

    @property
    def stairs_count(self) -> int:
        return sum(edge.stairs_count for edge in self.edges)


@dataclass(frozen=True)
class PedestrianCostProfile:
    avoid_stairs: bool
    shade_weight: float
    stair_weight: float
    slope_weight: float
    corner_weight: float
    crowding_weight: float
    walking_speed_mps: float
    max_extra_walk_ratio: float

    @classmethod
    def from_preferences(cls, preferences: RoutingPreferences) -> "PedestrianCostProfile":
        return cls(
            avoid_stairs=preferences.avoid_stairs,
            shade_weight=preferences.shade_weight,
            stair_weight=preferences.stair_weight,
            slope_weight=preferences.slope_weight,
            corner_weight=preferences.corner_weight,
            crowding_weight=preferences.crowding_weight,
            walking_speed_mps=preferences.walking_speed_mps,
            max_extra_walk_ratio=preferences.max_extra_walk_ratio,
        )


class DemoMicroRouter:
    """Custom pedestrian routing engine boundary.

    This in-process graph router uses the same cost model that should later move into pgRouting:
    edge base walking time plus stairs, slope, and corner penalties minus shade rewards.
    """

    def build_custom_walk(
        self,
        *,
        segment_id: str,
        start: Place,
        end: Place,
        title: str,
        preferences: RoutingPreferences,
    ) -> WalkingRoute:
        profile = PedestrianCostProfile.from_preferences(preferences)
        graph = build_segment_graph(start.coordinate, end.coordinate)
        path = find_weighted_path(graph, "start", "end", profile)
        shortest_distance = shortest_distance_meters(graph, "start", "end")

        if path.distance_meters > shortest_distance * (1 + profile.max_extra_walk_ratio):
            path = find_shortest_distance_path(graph, "start", "end")

        geometry = path.geometry
        distance = distance_meters(geometry)
        stairs_avoided = max(0, direct_stairs_count(graph) - path.stairs_count)

        return WalkingRoute(
            segment=RouteSegment(
                id=segment_id,
                kind="custom_walk",
                mode="walk",
                title=title,
                geometry=geometry,
                render=RenderStyle(color="#16A34A", width=6, pattern="dashed"),
                metrics=SegmentMetrics(
                    distance_meters=distance,
                    duration_seconds=estimate_walking_seconds(distance, profile.walking_speed_mps),
                    shade_shelters=path.shade_shelters,
                    stairs_avoided=stairs_avoided,
                ),
            ),
            markers=build_walk_markers(segment_id, path, graph, stairs_avoided),
        )


def build_segment_graph(start: Coordinate, end: Coordinate) -> PedestrianGraph:
    north_offset, east_offset = perpendicular_offset(start, end, meters=70)
    shade_a = offset_coordinate(interpolate(start, end, 0.34), north_offset, east_offset)
    shade_b = offset_coordinate(
        interpolate(start, end, 0.68),
        north_offset * 0.75,
        east_offset * 0.75,
    )
    direct_mid = offset_coordinate(
        interpolate(start, end, 0.5),
        -north_offset * 0.35,
        -east_offset * 0.35,
    )
    smooth_mid = offset_coordinate(
        interpolate(start, end, 0.52),
        north_offset * 0.25,
        east_offset * 0.25,
    )

    nodes = {
        "start": PedestrianNode(id="start", coordinate=start),
        "shade-a": PedestrianNode(id="shade-a", coordinate=shade_a),
        "shade-b": PedestrianNode(id="shade-b", coordinate=shade_b),
        "direct-mid": PedestrianNode(id="direct-mid", coordinate=direct_mid),
        "smooth-mid": PedestrianNode(id="smooth-mid", coordinate=smooth_mid),
        "end": PedestrianNode(id="end", coordinate=end),
    }
    edge_specs = [
        ("start", "direct-mid", 1, 0.05, 1, 0.085, 0.75, "stairs"),
        ("direct-mid", "end", 0, 0.05, 1, 0.07, 0.65, None),
        ("start", "shade-a", 0, 0.85, 1, 0.025, 0.15, "shade"),
        ("shade-a", "shade-b", 0, 0.95, 1, 0.018, 0.1, "shade"),
        ("shade-b", "end", 0, 0.65, 1, 0.02, 0.2, "shade"),
        ("start", "smooth-mid", 0, 0.25, 0, 0.04, 0.35, None),
        ("smooth-mid", "end", 0, 0.25, 0, 0.04, 0.35, None),
    ]
    edges = [
        build_edge(nodes[start_id], nodes[end_id], stairs, shade, corners, slope, crowding, feature)
        for start_id, end_id, stairs, shade, corners, slope, crowding, feature in edge_specs
    ]

    return PedestrianGraph(nodes=nodes, edges=edges)


def build_edge(
    start: PedestrianNode,
    end: PedestrianNode,
    stairs_count: int,
    shade_score: float,
    corner_count: int,
    slope_grade: float,
    crowding_score: float,
    feature: str | None,
) -> PedestrianEdge:
    geometry = [start.coordinate, end.coordinate]
    return PedestrianEdge(
        start_id=start.id,
        end_id=end.id,
        geometry=geometry,
        distance_meters=distance_meters(geometry),
        stairs_count=stairs_count,
        shade_score=shade_score,
        corner_count=corner_count,
        slope_grade=slope_grade,
        crowding_score=crowding_score,
        feature=feature,
    )


def find_weighted_path(
    graph: PedestrianGraph,
    start_id: str,
    end_id: str,
    profile: PedestrianCostProfile,
) -> PedestrianPath:
    return find_path(graph, start_id, end_id, lambda edge: edge_cost_seconds(edge, profile))


def find_shortest_distance_path(
    graph: PedestrianGraph,
    start_id: str,
    end_id: str,
) -> PedestrianPath:
    return find_path(graph, start_id, end_id, lambda edge: float(edge.distance_meters))


def find_path(
    graph: PedestrianGraph,
    start_id: str,
    end_id: str,
    cost_function: Callable[[PedestrianEdge], float],
) -> PedestrianPath:
    queue: list[tuple[float, str]] = [(0, start_id)]
    costs: dict[str, float] = {start_id: 0}
    previous: dict[str, tuple[str, PedestrianEdge]] = {}

    while queue:
        current_cost, node_id = heappop(queue)
        if node_id == end_id:
            break
        if current_cost > costs[node_id]:
            continue

        for edge in graph.adjacency[node_id]:
            next_cost = current_cost + cost_function(edge)
            if next_cost < costs.get(edge.end_id, float("inf")):
                costs[edge.end_id] = next_cost
                previous[edge.end_id] = (node_id, edge)
                heappush(queue, (next_cost, edge.end_id))

    if end_id not in previous:
        raise ValueError(f"No pedestrian path from {start_id} to {end_id}")

    edges: list[PedestrianEdge] = []
    cursor = end_id
    while cursor != start_id:
        previous_node_id, edge = previous[cursor]
        edges.append(edge)
        cursor = previous_node_id

    edges.reverse()
    return PedestrianPath(edges=edges)


def edge_cost_seconds(edge: PedestrianEdge, profile: PedestrianCostProfile) -> float:
    base_seconds = edge.distance_meters / profile.walking_speed_mps
    stair_penalty = edge.stairs_count * 240 * profile.stair_weight
    if profile.avoid_stairs and edge.stairs_count > 0:
        stair_penalty += 360 * edge.stairs_count

    slope_penalty = edge.distance_meters * edge.slope_grade * 4.5 * profile.slope_weight
    corner_penalty = edge.corner_count * 18 * profile.corner_weight
    crowding_penalty = base_seconds * edge.crowding_score * 0.6 * profile.crowding_weight
    shade_reward = base_seconds * edge.shade_score * 0.35 * profile.shade_weight
    weighted_seconds = (
        base_seconds
        + stair_penalty
        + slope_penalty
        + corner_penalty
        + crowding_penalty
        - shade_reward
    )
    return max(base_seconds * 0.35, weighted_seconds)


def shortest_distance_meters(graph: PedestrianGraph, start_id: str, end_id: str) -> int:
    return find_shortest_distance_path(graph, start_id, end_id).distance_meters


def direct_stairs_count(graph: PedestrianGraph) -> int:
    direct_edges = [
        edge for edge in graph.edges if edge.start_id == "start" and edge.end_id == "direct-mid"
    ]
    return sum(edge.stairs_count for edge in direct_edges)


def build_walk_markers(
    segment_id: str,
    path: PedestrianPath,
    graph: PedestrianGraph,
    stairs_avoided: int,
) -> list[RouteMarker]:
    markers: list[RouteMarker] = []
    shade_edges = [edge for edge in path.edges if edge.feature == "shade"]
    for index, edge in enumerate(shade_edges[:2], start=1):
        markers.append(
            RouteMarker(
                id=f"{segment_id}-shade-{index}",
                kind="shade_shelter",
                title="Shade shelter" if index == 1 else "Tree shade",
                coordinate=edge.geometry[-1],
                segment_id=segment_id,
                icon="parasol" if index == 1 else "tree",
            )
        )

    if stairs_avoided > 0:
        stairs_edge = next((edge for edge in graph.edges if edge.feature == "stairs"), None)
        if stairs_edge is not None:
            markers.append(
                RouteMarker(
                    id=f"{segment_id}-stairs-avoided",
                    kind="stairs_avoided",
                    title="Stairs avoided",
                    coordinate=stairs_edge.geometry[-1],
                    segment_id=segment_id,
                    icon="stairs-off",
                )
            )

    return markers


def estimate_walking_seconds(distance: int, walking_speed_mps: float = 1.15) -> int:
    return max(round(distance / walking_speed_mps), 60)
