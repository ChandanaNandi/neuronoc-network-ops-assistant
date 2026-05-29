from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.anomaly.engine import IncidentNotFoundError
from app.db.session import get_db
from app.llm.ollama import OllamaUnavailableError
from app.rca.explainer import RCAExplanation, generate_rca_explanation

router = APIRouter(prefix="/api/rca", tags=["rca"])


@router.post(
    "/incidents/{incident_id}/explain", response_model=RCAExplanation
)
def explain_incident(
    incident_id: UUID,
    model: str | None = Query(default=None),
    require_llm: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> RCAExplanation:
    try:
        return generate_rca_explanation(
            db, incident_id, model=model, require_llm=require_llm
        )
    except IncidentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except OllamaUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ollama unavailable: {exc}",
        ) from exc
