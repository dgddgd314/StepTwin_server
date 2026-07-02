from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from steptwin_api.core.config import Settings
from steptwin_api.schemas.pedestrian_graph import (
    CrossingType,
    GraphNodeKind,
    PedestrianGraphDataset,
    PedestrianGraphEdge,
    PedestrianGraphVertex,
    SurfaceType,
)
from steptwin_api.schemas.routing import Coordinate

SeoulWalkNodeType = Literal["NODE", "LINK"]

MAX_SEOUL_OPENAPI_PAGE_SIZE = 1000
PEDESTRIAN_LINK_CODE_PREFIX = "1"

_POINT_WKT_PATTERN = re.compile(r"^POINT\s*\(\s*([^)]+?)\s*\)$", re.IGNORECASE)
_LINESTRING_WKT_PATTERN = re.compile(r"^LINESTRING\s*\(\s*(.+?)\s*\)$", re.IGNORECASE)


class SeoulWalkNetworkError(RuntimeError):
    """Raised when Seoul OpenAPI walking-network data cannot be fetched or parsed."""


@dataclass(frozen=True, slots=True)
class SeoulWalkNetworkRow:
    node_type: SeoulWalkNodeType
    node_wkt: str | None
    node_id: int | None
    node_type_code: str | None
    link_wkt: str | None
    link_id: int | None
    link_type_code: str | None
    source_node_id: int | None
    target_node_id: int | None
    link_length_meters: float | None
    sgg_code: str | None
    sgg_name: str | None
    emd_code: str | None
    emd_name: str | None
    express_car_road: bool | None
    subway_network: bool | None
    bridge: bool | None
    tunnel: bool | None
    overpass: bool | None
    crosswalk: bool | None
    park: bool | None
    building_inside: bool | None
    work_dttm: str | None


@dataclass(frozen=True, slots=True)
class SeoulWalkNetworkPage:
    total_count: int
    result_code: str
    result_message: str
    rows: tuple[SeoulWalkNetworkRow, ...]


SeoulWalkNetworkProgressCallback = Callable[[int, int, int], None]


def fetch_seoul_walk_network_page(
    settings: Settings,
    *,
    start_index: int,
    end_index: int,
    sgg_name: str | None = None,
    work_dttm: str | None = None,
) -> SeoulWalkNetworkPage:
    if settings.seoul_openapi_key is None:
        raise SeoulWalkNetworkError("SEOUL_OPENAPI_KEY is required")

    url = build_seoul_walk_network_url(
        settings,
        start_index=start_index,
        end_index=end_index,
        sgg_name=sgg_name,
        work_dttm=work_dttm,
    )
    with httpx.Client(timeout=30) as client:
        response = client.get(url)
        response.raise_for_status()

    return parse_seoul_walk_network_xml(response.text)


def iter_seoul_walk_network_pages(
    settings: Settings,
    *,
    sgg_name: str | None = None,
    work_dttm: str | None = None,
) -> Iterable[SeoulWalkNetworkPage]:
    page_size = settings.seoul_walk_net_page_size
    first_page = fetch_seoul_walk_network_page(
        settings,
        start_index=1,
        end_index=page_size,
        sgg_name=sgg_name,
        work_dttm=work_dttm,
    )
    yield first_page

    next_start = page_size + 1
    while next_start <= first_page.total_count:
        next_end = min(next_start + page_size - 1, first_page.total_count)
        yield fetch_seoul_walk_network_page(
            settings,
            start_index=next_start,
            end_index=next_end,
            sgg_name=sgg_name,
            work_dttm=work_dttm,
        )
        next_start = next_end + 1


def fetch_all_seoul_walk_network_rows(
    settings: Settings,
    *,
    sgg_name: str | None = None,
    work_dttm: str | None = None,
    progress_callback: SeoulWalkNetworkProgressCallback | None = None,
) -> tuple[SeoulWalkNetworkRow, ...]:
    rows: list[SeoulWalkNetworkRow] = []
    for page_number, page in enumerate(
        iter_seoul_walk_network_pages(settings, sgg_name=sgg_name, work_dttm=work_dttm),
        start=1,
    ):
        rows.extend(page.rows)
        if progress_callback is not None:
            progress_callback(page_number, len(rows), page.total_count)

    return tuple(rows)


def build_seoul_walk_network_url(
    settings: Settings,
    *,
    start_index: int,
    end_index: int,
    sgg_name: str | None = None,
    work_dttm: str | None = None,
) -> str:
    validate_seoul_openapi_range(start_index, end_index)
    if settings.seoul_openapi_key is None:
        raise SeoulWalkNetworkError("SEOUL_OPENAPI_KEY is required")

    parts = [
        settings.seoul_openapi_base_url.rstrip("/"),
        quote(settings.seoul_openapi_key, safe=""),
        quote(settings.seoul_walk_net_format, safe=""),
        quote(settings.seoul_walk_net_service, safe=""),
        str(start_index),
        str(end_index),
    ]
    if sgg_name is not None:
        parts.append(quote(sgg_name, safe=""))
    if work_dttm is not None:
        if sgg_name is None:
            parts.append("")
        parts.append(quote(work_dttm, safe=""))

    return "/".join(parts) + "/"


def validate_seoul_openapi_range(start_index: int, end_index: int) -> None:
    if start_index <= 0:
        raise ValueError("start_index must be positive")
    if end_index < start_index:
        raise ValueError("end_index must be greater than or equal to start_index")
    if end_index - start_index + 1 > MAX_SEOUL_OPENAPI_PAGE_SIZE:
        raise ValueError("Seoul OpenAPI supports at most 1000 rows per request")


def parse_seoul_walk_network_xml(payload: str | bytes) -> SeoulWalkNetworkPage:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise SeoulWalkNetworkError("Seoul OpenAPI response is not valid XML") from exc

    result = root.find("RESULT")
    result_code = get_element_text(result, "CODE") or ""
    result_message = get_element_text(result, "MESSAGE") or ""
    if result_code and result_code not in {"INFO-000", "INFO-200"}:
        raise SeoulWalkNetworkError(f"Seoul OpenAPI error {result_code}: {result_message}")

    rows = tuple(parse_seoul_walk_network_row(row) for row in root.findall("row"))
    total_count = parse_int(get_direct_child_text(root, "list_total_count"))
    return SeoulWalkNetworkPage(
        total_count=total_count if total_count is not None else len(rows),
        result_code=result_code,
        result_message=result_message,
        rows=rows,
    )


def parse_seoul_walk_network_row(row: ElementTree.Element) -> SeoulWalkNetworkRow:
    node_type_text = require_text(row, "NODE_TYPE").upper()
    if node_type_text not in {"NODE", "LINK"}:
        raise SeoulWalkNetworkError(f"Unsupported NODE_TYPE: {node_type_text!r}")

    node_type: SeoulWalkNodeType = "NODE" if node_type_text == "NODE" else "LINK"
    return SeoulWalkNetworkRow(
        node_type=node_type,
        node_wkt=get_direct_child_text(row, "NODE_WKT"),
        node_id=parse_int(get_direct_child_text(row, "NODE_ID")),
        node_type_code=get_direct_child_text(row, "NODE_TYPE_CD"),
        link_wkt=get_direct_child_text(row, "LNKG_WKT"),
        link_id=parse_int(get_direct_child_text(row, "LNKG_ID")),
        link_type_code=get_direct_child_text(row, "LNKG_TYPE_CD"),
        source_node_id=parse_int(get_direct_child_text(row, "BGNG_LNKG_ID")),
        target_node_id=parse_int(get_direct_child_text(row, "END_LNKG_ID")),
        link_length_meters=parse_float(get_direct_child_text(row, "LNKG_LEN")),
        sgg_code=get_direct_child_text(row, "SGG_CD"),
        sgg_name=get_direct_child_text(row, "SGG_NM"),
        emd_code=get_direct_child_text(row, "EMD_CD"),
        emd_name=get_direct_child_text(row, "EMD_NM"),
        express_car_road=parse_bool_flag(get_direct_child_text(row, "EXPN_CAR_RD")),
        subway_network=parse_bool_flag(get_direct_child_text(row, "SBWY_NTW")),
        bridge=parse_bool_flag(get_direct_child_text(row, "BRG")),
        tunnel=parse_bool_flag(get_direct_child_text(row, "TNL")),
        overpass=parse_bool_flag(get_direct_child_text(row, "OVRP")),
        crosswalk=parse_bool_flag(get_direct_child_text(row, "CRSWK")),
        park=parse_bool_flag(get_direct_child_text(row, "PARK")),
        building_inside=parse_bool_flag(get_direct_child_text(row, "BLDG")),
        work_dttm=get_direct_child_text(row, "WORK_DTTM"),
    )


def build_pedestrian_graph_dataset_from_seoul_rows(
    rows: Iterable[SeoulWalkNetworkRow],
    *,
    name: str = "seoul-walk-network",
    version: str = "openapi",
    require_known_vertices: bool = True,
) -> PedestrianGraphDataset:
    row_list = list(rows)
    vertices_by_id: dict[int, PedestrianGraphVertex] = {}
    for row in row_list:
        vertex = build_vertex_from_seoul_row(row)
        if vertex is not None:
            vertices_by_id[vertex.id] = vertex

    vertices = list(vertices_by_id.values())
    vertices_by_id = {vertex.id: vertex for vertex in vertices}

    edges_by_id: dict[int, PedestrianGraphEdge] = {}
    for row in row_list:
        edge = build_edge_from_seoul_row(row)
        if edge is None:
            continue
        if require_known_vertices and (
            edge.source not in vertices_by_id or edge.target not in vertices_by_id
        ):
            continue
        edges_by_id[edge.id] = edge

    return PedestrianGraphDataset(
        name=name,
        version=version,
        vertices=vertices,
        edges=list(edges_by_id.values()),
    )


def build_vertex_from_seoul_row(row: SeoulWalkNetworkRow) -> PedestrianGraphVertex | None:
    if row.node_type != "NODE" or row.node_id is None or row.node_id <= 0 or row.node_wkt is None:
        return None

    coordinate = parse_point_wkt(row.node_wkt)
    if coordinate is None:
        return None

    return PedestrianGraphVertex(
        id=row.node_id,
        coordinate=coordinate,
        kind=map_node_kind(row.node_type_code),
        name=build_place_name(row),
        tags=build_common_tags(row) | {"node_type_code": row.node_type_code or ""},
    )


def build_edge_from_seoul_row(row: SeoulWalkNetworkRow) -> PedestrianGraphEdge | None:
    if row.node_type != "LINK":
        return None
    if not is_pedestrian_link_code(row.link_type_code):
        return None
    if (
        row.link_id is None
        or row.link_id <= 0
        or row.source_node_id is None
        or row.target_node_id is None
        or row.source_node_id == row.target_node_id
        or row.link_wkt is None
    ):
        return None

    geometry = parse_linestring_wkt(row.link_wkt)
    if len(geometry) < 2:
        return None

    return PedestrianGraphEdge(
        id=row.link_id,
        source=row.source_node_id,
        target=row.target_node_id,
        geometry=geometry,
        distance_meters=row.link_length_meters,
        crossing_type=map_crossing_type(row),
        surface_type=map_surface_type(row),
        name=build_place_name(row),
        tags=build_common_tags(row) | build_link_tags(row),
    )


def is_pedestrian_link_code(value: str | None) -> bool:
    return value is not None and value.strip().startswith(PEDESTRIAN_LINK_CODE_PREFIX)


def map_node_kind(value: str | None) -> GraphNodeKind:
    match value:
        case "1":
            return "station_exit"
        case "2":
            return "bus_stop"
        case "3":
            return "entrance"
        case _:
            return "waypoint"


def map_crossing_type(row: SeoulWalkNetworkRow) -> CrossingType:
    if row.crosswalk:
        return "crosswalk"
    if row.overpass:
        return "overpass"
    if row.tunnel:
        return "underpass"

    return "none"


def map_surface_type(row: SeoulWalkNetworkRow) -> SurfaceType:
    if row.crosswalk or row.overpass or row.bridge or row.tunnel or row.building_inside:
        return "paved"

    return "unknown"


def parse_point_wkt(value: str) -> Coordinate | None:
    match = _POINT_WKT_PATTERN.match(value.strip())
    if match is None:
        return None

    return parse_wkt_coordinate_pair(match.group(1))


def parse_linestring_wkt(value: str) -> list[Coordinate]:
    match = _LINESTRING_WKT_PATTERN.match(value.strip())
    if match is None:
        return []

    coordinates: list[Coordinate] = []
    for pair in match.group(1).split(","):
        coordinate = parse_wkt_coordinate_pair(pair)
        if coordinate is not None:
            coordinates.append(coordinate)

    return dedupe_adjacent_coordinates(coordinates)


def parse_wkt_coordinate_pair(value: str) -> Coordinate | None:
    parts = value.strip().split()
    if len(parts) < 2:
        return None

    longitude = parse_float(parts[0])
    latitude = parse_float(parts[1])
    if longitude is None or latitude is None:
        return None

    return Coordinate(latitude=latitude, longitude=longitude)


def dedupe_adjacent_coordinates(coordinates: list[Coordinate]) -> list[Coordinate]:
    deduped: list[Coordinate] = []
    for coordinate in coordinates:
        if not deduped or coordinate != deduped[-1]:
            deduped.append(coordinate)

    return deduped


def build_place_name(row: SeoulWalkNetworkRow) -> str | None:
    if row.sgg_name is None and row.emd_name is None:
        return None

    return " ".join(part for part in (row.sgg_name, row.emd_name) if part)


def build_common_tags(row: SeoulWalkNetworkRow) -> dict[str, str]:
    tags = {
        "source": "seoul_openapi",
        "source_service": "TbTraficWlkNet",
        "source_node_type": row.node_type,
    }
    tags.update(
        stringify_tags(
            {
                "sgg_code": row.sgg_code,
                "sgg_name": row.sgg_name,
                "emd_code": row.emd_code,
                "emd_name": row.emd_name,
                "work_dttm": row.work_dttm,
            }
        )
    )
    return tags


def build_link_tags(row: SeoulWalkNetworkRow) -> dict[str, str]:
    return stringify_tags(
        {
            "link_type_code": row.link_type_code,
            "express_car_road": row.express_car_road,
            "subway_network": row.subway_network,
            "bridge": row.bridge,
            "tunnel": row.tunnel,
            "overpass": row.overpass,
            "crosswalk": row.crosswalk,
            "park": row.park,
            "building_inside": row.building_inside,
        }
    )


def stringify_tags(values: Mapping[str, object]) -> dict[str, str]:
    tags: dict[str, str] = {}
    for key, value in values.items():
        if value is not None:
            tags[key] = str(value)

    return tags


def get_element_text(element: ElementTree.Element | None, child_name: str) -> str | None:
    if element is None:
        return None

    return get_direct_child_text(element, child_name)


def get_direct_child_text(element: ElementTree.Element, child_name: str) -> str | None:
    child = element.find(child_name)
    if child is None or child.text is None:
        return None

    stripped = child.text.strip()
    return stripped or None


def require_text(element: ElementTree.Element, child_name: str) -> str:
    value = get_direct_child_text(element, child_name)
    if value is None:
        raise SeoulWalkNetworkError(f"Missing required field {child_name}")

    return value


def parse_bool_flag(value: str | None) -> bool | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped == "1":
        return True
    if stripped == "0":
        return False

    return None


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None
