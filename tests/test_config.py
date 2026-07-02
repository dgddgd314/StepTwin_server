from pathlib import Path

from pytest import MonkeyPatch

from steptwin_api.core.config import ENV_FILE, PROJECT_ROOT, get_settings


def test_settings_env_file_is_project_root_relative(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    settings = get_settings()

    assert ENV_FILE.is_absolute()
    assert ENV_FILE == PROJECT_ROOT / ".env"
    assert settings.tmap_base_url == "https://apis.openapi.sk.com"
    assert settings.pedestrian_graph_vertex_table == "osm_pedestrian_vertices"
    assert settings.pedestrian_graph_edge_table == "osm_pedestrian_edges"
    assert settings.seoul_sdot_service == "sDoTPeople"
