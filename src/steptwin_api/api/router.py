from fastapi import APIRouter

from steptwin_api.api.routes import health, routing, walk_routing

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(routing.router)
api_router.include_router(walk_routing.router)
