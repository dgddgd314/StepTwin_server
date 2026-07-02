import pytest

import steptwin_api.services.seoul_walk_network as seoul_walk_network
from steptwin_api.core.config import Settings
from steptwin_api.services.seoul_walk_network import (
    build_edge_from_seoul_row,
    build_pedestrian_graph_dataset_from_seoul_rows,
    build_seoul_walk_network_url,
    fetch_all_seoul_walk_network_rows,
    is_pedestrian_link_code,
    parse_linestring_wkt,
    parse_point_wkt,
    parse_seoul_walk_network_xml,
)

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<TbTraficWlkNet>
<list_total_count>491082</list_total_count>
<RESULT>
<CODE>INFO-000</CODE>
<MESSAGE>정상 처리되었습니다</MESSAGE>
</RESULT>
<row>
<NODE_TYPE>NODE</NODE_TYPE>
<NODE_WKT>POINT(126.98193565588588 37.58271825082822)</NODE_WKT>
<NODE_ID>133372</NODE_ID>
<NODE_TYPE_CD>1</NODE_TYPE_CD>
<LNKG_WKT/>
<LNKG_ID>0</LNKG_ID>
<LNKG_TYPE_CD/>
<BGNG_LNKG_ID/>
<END_LNKG_ID/>
<LNKG_LEN/>
<SGG_CD>1111000000</SGG_CD>
<SGG_NM>종로구</SGG_NM>
<EMD_CD>1111013900</EMD_CD>
<EMD_NM>팔판동</EMD_NM>
<EXPN_CAR_RD/>
<SBWY_NTW/>
<BRG/>
<TNL/>
<OVRP>0</OVRP>
<CRSWK>0</CRSWK>
<PARK/>
<BLDG/>
<WORK_DTTM>2026-07-02 14:41:44.0</WORK_DTTM>
</row>
<row>
<NODE_TYPE>NODE</NODE_TYPE>
<NODE_WKT>POINT(126.98179962397771 37.58273470348685)</NODE_WKT>
<NODE_ID>133389</NODE_ID>
<NODE_TYPE_CD>2</NODE_TYPE_CD>
<LNKG_WKT/>
<LNKG_ID>0</LNKG_ID>
<LNKG_TYPE_CD/>
<BGNG_LNKG_ID/>
<END_LNKG_ID/>
<LNKG_LEN/>
<SGG_CD>1111000000</SGG_CD>
<SGG_NM>종로구</SGG_NM>
<EMD_CD>1111013900</EMD_CD>
<EMD_NM>팔판동</EMD_NM>
<EXPN_CAR_RD/>
<SBWY_NTW/>
<BRG/>
<TNL/>
<OVRP>0</OVRP>
<CRSWK>0</CRSWK>
<PARK/>
<BLDG/>
<WORK_DTTM>2026-07-02 14:41:44.0</WORK_DTTM>
</row>
<row>
<NODE_TYPE>LINK</NODE_TYPE>
<NODE_WKT/>
<NODE_ID>0</NODE_ID>
<NODE_TYPE_CD/>
<LNKG_WKT>
LINESTRING(126.98193565588588 37.58271825082822,126.98179962397771 37.58273470348685)
</LNKG_WKT>
<LNKG_ID>52901</LNKG_ID>
<LNKG_TYPE_CD>1000</LNKG_TYPE_CD>
<BGNG_LNKG_ID>133372</BGNG_LNKG_ID>
<END_LNKG_ID>133389</END_LNKG_ID>
<LNKG_LEN>12.1530</LNKG_LEN>
<SGG_CD>1111000000</SGG_CD>
<SGG_NM>종로구</SGG_NM>
<EMD_CD>1111013900</EMD_CD>
<EMD_NM>팔판동</EMD_NM>
<EXPN_CAR_RD>0</EXPN_CAR_RD>
<SBWY_NTW>0</SBWY_NTW>
<BRG>0</BRG>
<TNL>0</TNL>
<OVRP>0</OVRP>
<CRSWK>1</CRSWK>
<PARK>0</PARK>
<BLDG>1</BLDG>
<WORK_DTTM>2026-07-02 14:36:45.0</WORK_DTTM>
</row>
</TbTraficWlkNet>
"""


def test_build_seoul_walk_network_url_uses_documented_path_shape() -> None:
    settings = Settings(seoul_openapi_key="abc123")

    url = build_seoul_walk_network_url(
        settings,
        start_index=1,
        end_index=1000,
        sgg_name="종로구",
    )

    assert url == (
        "http://openapi.seoul.go.kr:8088/abc123/xml/TbTraficWlkNet/1/1000/"
        "%EC%A2%85%EB%A1%9C%EA%B5%AC/"
    )


def test_build_seoul_walk_network_url_rejects_oversized_page() -> None:
    settings = Settings(seoul_openapi_key="abc123")

    with pytest.raises(ValueError, match="1000 rows"):
        build_seoul_walk_network_url(settings, start_index=1, end_index=1001)


def test_parse_seoul_walk_network_xml_reads_total_result_and_rows() -> None:
    page = parse_seoul_walk_network_xml(SAMPLE_XML)

    assert page.total_count == 491082
    assert page.result_code == "INFO-000"
    assert len(page.rows) == 3
    assert page.rows[0].node_type == "NODE"
    assert page.rows[0].node_type_code == "1"
    assert page.rows[2].node_type == "LINK"
    assert page.rows[2].link_type_code == "1000"
    assert page.rows[2].crosswalk is True


def test_fetch_all_seoul_walk_network_rows_collects_all_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = parse_seoul_walk_network_xml(SAMPLE_XML)
    calls: list[tuple[int, int]] = []
    progress: list[tuple[int, int, int]] = []

    def fake_fetch_page(
        settings: Settings,
        *,
        start_index: int,
        end_index: int,
        sgg_name: str | None = None,
        work_dttm: str | None = None,
    ):
        calls.append((start_index, end_index))
        return type(page)(
            total_count=6,
            result_code=page.result_code,
            result_message=page.result_message,
            rows=page.rows[:2] if start_index == 1 else page.rows[2:],
        )

    monkeypatch.setattr(seoul_walk_network, "fetch_seoul_walk_network_page", fake_fetch_page)

    rows = fetch_all_seoul_walk_network_rows(
        Settings(seoul_openapi_key="abc123", seoul_walk_net_page_size=3),
        progress_callback=lambda page_number, fetched_count, total_count: progress.append(
            (page_number, fetched_count, total_count)
        ),
    )

    assert calls == [(1, 3), (4, 6)]
    assert len(rows) == 3
    assert progress == [(1, 2, 6), (2, 3, 6)]


def test_seoul_rows_normalize_to_pedestrian_graph_dataset() -> None:
    page = parse_seoul_walk_network_xml(SAMPLE_XML)

    dataset = build_pedestrian_graph_dataset_from_seoul_rows(
        page.rows,
        version="2026-07-02",
    )

    assert dataset.name == "seoul-walk-network"
    assert [vertex.kind for vertex in dataset.vertices] == ["station_exit", "bus_stop"]
    assert len(dataset.edges) == 1
    edge = dataset.edges[0]
    assert edge.id == 52901
    assert edge.source == 133372
    assert edge.target == 133389
    assert edge.distance_meters == 12.153
    assert edge.crossing_type == "crosswalk"
    assert edge.surface_type == "paved"
    assert edge.tags["source_service"] == "TbTraficWlkNet"
    assert edge.tags["link_type_code"] == "1000"


def test_seoul_rows_dedupe_repeated_vertices_and_edges() -> None:
    page = parse_seoul_walk_network_xml(SAMPLE_XML)

    dataset = build_pedestrian_graph_dataset_from_seoul_rows(
        [*page.rows, *page.rows],
        version="2026-07-02",
    )

    assert len(dataset.vertices) == 2
    assert len(dataset.edges) == 1


def test_non_pedestrian_link_codes_are_filtered() -> None:
    assert is_pedestrian_link_code("1000") is True
    assert is_pedestrian_link_code("1111") is True
    assert is_pedestrian_link_code("0111") is False
    assert is_pedestrian_link_code(None) is False


def test_self_loop_links_are_filtered_from_routing_graph() -> None:
    page = parse_seoul_walk_network_xml(SAMPLE_XML)
    row = page.rows[2]
    self_loop_row = type(row)(
        node_type=row.node_type,
        node_wkt=row.node_wkt,
        node_id=row.node_id,
        node_type_code=row.node_type_code,
        link_wkt=row.link_wkt,
        link_id=row.link_id,
        link_type_code=row.link_type_code,
        source_node_id=133372,
        target_node_id=133372,
        link_length_meters=row.link_length_meters,
        sgg_code=row.sgg_code,
        sgg_name=row.sgg_name,
        emd_code=row.emd_code,
        emd_name=row.emd_name,
        express_car_road=row.express_car_road,
        subway_network=row.subway_network,
        bridge=row.bridge,
        tunnel=row.tunnel,
        overpass=row.overpass,
        crosswalk=row.crosswalk,
        park=row.park,
        building_inside=row.building_inside,
        work_dttm=row.work_dttm,
    )

    assert build_edge_from_seoul_row(self_loop_row) is None


def test_wkt_parsers_use_wgs84_lon_lat_order() -> None:
    point = parse_point_wkt("POINT(127.00643997613173 37.58098329900806)")
    line = parse_linestring_wkt(
        "LINESTRING(126.98193565588588 37.58271825082822,"
        "126.98179962397771 37.58273470348685)"
    )

    assert point is not None
    assert point.longitude == 127.00643997613173
    assert point.latitude == 37.58098329900806
    assert len(line) == 2
    assert line[0].longitude == 126.98193565588588
    assert line[0].latitude == 37.58271825082822
