from collections.abc import Generator

import pytest

from steptwin_api.core.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
