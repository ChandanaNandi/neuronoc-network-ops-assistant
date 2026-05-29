from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.anomaly.engine import (
    IncidentNotFoundError,
    analyze_incident,
    analyze_open_incidents,
)
from app.anomaly.rules import AnomalyFinding
from app.db.session import get_db

router = APIRouter(prefix="/api/anomalies", tags=["anomalies"])


@router.get("/incidents/{incident_id}", response_model=list[AnomalyFinding])
def findings_for_incident(
    incident_id: UUID, db: Session = Depends(get_db)
) -> list[AnomalyFinding]:
    try:
        return analyze_incident(db, incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("/open", response_model=list[AnomalyFinding])
def findings_for_open_incidents(
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[AnomalyFinding]:
    return analyze_open_incidents(db, limit=limit)
