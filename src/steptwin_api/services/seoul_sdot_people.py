from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from steptwin_api.core.config import Settings

SeoulOpenApiType = Literal["json", "xml"]


class SeoulSdotPeopleError(RuntimeError):
    """Raised when Seoul S-DoT floating population data cannot be fetched or parsed."""


@dataclass(frozen=True, slots=True)
class SeoulSdotPeopleRecord:
    model_name: str | None
    serial: str | None
    sensing_time: str | None
    region: str | None
    autonomous_district: str | None
    administrative_district: str | None
    visitor_count: int
    date: str | None
    data_no: str | None
    crowding_score: float


def build_sdot_people_url(
    settings: Settings,
    *,
    response_type: SeoulOpenApiType | None = None,
    start_index: int | None = None,
    end_index: int | None = None,
) -> str:
    if settings.seoul_openapi_key is None:
        raise SeoulSdotPeopleError("SEOUL_OPENAPI_KEY is required for S-DoT people data")

    request_type = response_type or settings.seoul_sdot_type
    start = start_index or settings.seoul_sdot_start_index
    end = end_index or settings.seoul_sdot_end_index
    if start <= 0 or end < start:
        raise ValueError("S-DoT START_INDEX and END_INDEX must be a positive ascending range")

    base_url = settings.seoul_openapi_base_url.rstrip("/")
    return (
        f"{base_url}/{settings.seoul_openapi_key}/{request_type}/"
        f"{settings.seoul_sdot_service}/{start}/{end}/"
    )


def fetch_sdot_people_records(settings: Settings) -> tuple[SeoulSdotPeopleRecord, ...]:
    url = build_sdot_people_url(settings)
    with httpx.Client(timeout=10) as client:
        response = client.get(url, headers={"Accept": "application/json, application/xml"})
        response.raise_for_status()

    if settings.seoul_sdot_type == "xml":
        return parse_sdot_people_xml(response.text)

    payload = response.json()
    if not isinstance(payload, dict):
        raise SeoulSdotPeopleError("Seoul S-DoT JSON response must be an object")
    return parse_sdot_people_json(payload)


def parse_sdot_people_json(payload: dict[str, Any]) -> tuple[SeoulSdotPeopleRecord, ...]:
    service_payload = payload.get("sDoTPeople")
    if not isinstance(service_payload, dict):
        raise SeoulSdotPeopleError("Seoul S-DoT JSON response must contain sDoTPeople")

    rows = service_payload.get("row")
    if not isinstance(rows, list):
        return ()

    return build_records([row for row in rows if isinstance(row, dict)])


def parse_sdot_people_xml(payload: str) -> tuple[SeoulSdotPeopleRecord, ...]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise SeoulSdotPeopleError("Seoul S-DoT XML response is invalid") from exc

    rows: list[dict[str, str]] = []
    for row in root.findall(".//row"):
        values = {child.tag: child.text or "" for child in row}
        rows.append(values)

    return build_records(rows)


def build_records(rows: list[dict[str, Any]]) -> tuple[SeoulSdotPeopleRecord, ...]:
    visitor_counts = [parse_int(row.get("VISITOR_COUNT")) or 0 for row in rows]
    max_visitor_count = max(visitor_counts, default=0)

    records = [
        build_record(row, max_visitor_count=max_visitor_count)
        for row in rows
        if parse_int(row.get("VISITOR_COUNT")) is not None
    ]
    return tuple(records)


def build_record(row: dict[str, Any], *, max_visitor_count: int) -> SeoulSdotPeopleRecord:
    visitor_count = parse_int(row.get("VISITOR_COUNT"))
    if visitor_count is None:
        raise SeoulSdotPeopleError("VISITOR_COUNT must be an integer")

    return SeoulSdotPeopleRecord(
        model_name=parse_string(row.get("MODELNAME")),
        serial=parse_string(row.get("SERIAL")),
        sensing_time=parse_string(row.get("SENSING_TIME")),
        region=parse_string(row.get("REGION")),
        autonomous_district=parse_string(row.get("AUTONOMOUS_DISTRICT")),
        administrative_district=parse_string(row.get("ADMINISTRATIVE_DISTRICT")),
        visitor_count=visitor_count,
        date=parse_string(row.get("DATE")),
        data_no=parse_string(row.get("DATA_NO")),
        crowding_score=normalize_visitor_count(visitor_count, max_visitor_count),
    )


def normalize_visitor_count(visitor_count: int, max_visitor_count: int) -> float:
    if max_visitor_count <= 0:
        return 0.0

    return min(max(visitor_count / max_visitor_count, 0.0), 1.0)


def parse_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    return stripped or None


def parse_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None
