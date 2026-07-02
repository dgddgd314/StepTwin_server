from fastapi import APIRouter

from steptwin_api.schemas.routing import RoutePreviewRequest, RoutePreviewResponse
from steptwin_api.services.routing import RoutePreviewService

router = APIRouter(prefix="/routes", tags=["routes"])


@router.post(
    "/preview",
    response_model=RoutePreviewResponse,
    summary="Build a demo hybrid route preview",
)
async def create_route_preview(request: RoutePreviewRequest) -> RoutePreviewResponse:
    service = RoutePreviewService()
    return service.build_preview(request)
