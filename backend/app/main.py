from fastapi import FastAPI

from app.api.anomalies import router as anomalies_router
from app.api.health import router as health_router
from app.api.incidents import router as incidents_router
from app.api.simulator import router as simulator_router
from app.core.config import settings

app = FastAPI(title=settings.APP_NAME)
app.include_router(health_router)
app.include_router(incidents_router)
app.include_router(simulator_router)
app.include_router(anomalies_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "neuronoc-backend", "app": settings.APP_NAME}
