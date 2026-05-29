from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.incidents import router as incidents_router
from app.core.config import settings

app = FastAPI(title=settings.APP_NAME)
app.include_router(health_router)
app.include_router(incidents_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "neuronoc-backend", "app": settings.APP_NAME}
