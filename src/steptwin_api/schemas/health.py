from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ComponentStatus = Literal["ok", "degraded", "disabled"]
OverallStatus = Literal["ok", "degraded"]


class ComponentHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ComponentStatus
    detail: str | None = None


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: OverallStatus
    service: str
    environment: str
    version: str
    timestamp: datetime
    checks: dict[str, ComponentHealth]
