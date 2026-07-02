from steptwin_api.core.config import Settings
from steptwin_api.services.seoul_sdot_people import (
    build_sdot_people_url,
    parse_sdot_people_json,
    parse_sdot_people_xml,
)


def test_build_sdot_people_url_maps_openapi_request_parameters() -> None:
    settings = Settings(seoul_openapi_key="test-key")

    url = build_sdot_people_url(settings, response_type="xml", start_index=1, end_index=5)

    assert url == "http://openapi.seoul.go.kr:8088/test-key/xml/sDoTPeople/1/5/"


def test_parse_sdot_people_json_normalizes_visitor_count_to_crowding_score() -> None:
    records = parse_sdot_people_json(
        {
            "sDoTPeople": {
                "row": [
                    {
                        "MODELNAME": "model-a",
                        "SERIAL": "serial-a",
                        "SENSING_TIME": "2026-07-03 02:00:00",
                        "REGION": "도심",
                        "AUTONOMOUS_DISTRICT": "중구",
                        "ADMINISTRATIVE_DISTRICT": "명동",
                        "VISITOR_COUNT": "25",
                        "DATE": "20260703",
                        "DATA_NO": "1",
                    },
                    {
                        "MODELNAME": "model-b",
                        "VISITOR_COUNT": "100",
                    },
                ]
            }
        }
    )

    assert [record.visitor_count for record in records] == [25, 100]
    assert [record.crowding_score for record in records] == [0.25, 1.0]
    assert records[0].administrative_district == "명동"


def test_parse_sdot_people_xml_reads_row_fields() -> None:
    records = parse_sdot_people_xml(
        """
<sDoTPeople>
  <row>
    <AUTONOMOUS_DISTRICT>중구</AUTONOMOUS_DISTRICT>
    <ADMINISTRATIVE_DISTRICT>명동</ADMINISTRATIVE_DISTRICT>
    <VISITOR_COUNT>40</VISITOR_COUNT>
  </row>
  <row>
    <AUTONOMOUS_DISTRICT>종로구</AUTONOMOUS_DISTRICT>
    <ADMINISTRATIVE_DISTRICT>사직동</ADMINISTRATIVE_DISTRICT>
    <VISITOR_COUNT>80</VISITOR_COUNT>
  </row>
</sDoTPeople>
"""
    )

    assert [record.autonomous_district for record in records] == ["중구", "종로구"]
    assert [record.crowding_score for record in records] == [0.5, 1.0]
