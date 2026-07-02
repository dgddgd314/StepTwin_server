from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from steptwin_api.core.config import get_settings
from steptwin_api.core.logging import configure_logging
from steptwin_api.db.session import close_database, init_database


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_database(settings)
    try:
        yield
    finally:
        await close_database()
