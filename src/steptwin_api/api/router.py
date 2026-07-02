from fastapi import APIRouter

from steptwin_api.api.routes import health, pedestrian_graph, routing, walk_routing

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(pedestrian_graph.router)
api_router.include_router(routing.router)
api_router.include_router(walk_routing.router)
