from fastapi import APIRouter

from steptwin_api.api.routes import health, routing

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(routing.router)
