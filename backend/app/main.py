from fastapi import FastAPI

from app.api.agents import router as agents_router
from app.api.anomalies import router as anomalies_router
from app.api.health import router as health_router
from app.api.incidents import router as incidents_router
from app.api.lab import router as lab_router
from app.api.operators import router as operators_router
from app.api.rca import router as rca_router
from app.api.remediation import router as remediation_router
from app.api.simulator import router as simulator_router
from app.api.validation import router as validation_router
from app.core.config import settings

app = FastAPI(title=settings.APP_NAME)
app.include_router(health_router)
app.include_router(incidents_router)
app.include_router(simulator_router)
app.include_router(anomalies_router)
app.include_router(agents_router)
app.include_router(rca_router)
app.include_router(remediation_router)
app.include_router(lab_router)
app.include_router(operators_router)
app.include_router(validation_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "neuronoc-backend", "app": settings.APP_NAME}
