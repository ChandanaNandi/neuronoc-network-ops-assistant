from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, select
from sqlalchemy.orm import Session

from app.db import models
from app.db.session import get_db
from app.schemas import incidents as schemas

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


def _get_incident_or_404(incident_id: UUID, db: Session) -> models.Incident:
    incident = db.get(models.Incident, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
        )
    return incident


@router.post("", response_model=schemas.IncidentRead, status_code=status.HTTP_201_CREATED)
def create_incident(
    payload: schemas.IncidentCreate, db: Session = Depends(get_db)
) -> models.Incident:
    incident = models.Incident(**payload.model_dump(mode="json"))
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


@router.get("", response_model=list[schemas.IncidentRead])
def list_incidents(
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[models.Incident]:
    stmt = (
        select(models.Incident)
        .order_by(desc(models.Incident.created_at))
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


@router.get("/{incident_id}", response_model=schemas.IncidentRead)
def get_incident(
    incident_id: UUID, db: Session = Depends(get_db)
) -> models.Incident:
    return _get_incident_or_404(incident_id, db)


@router.get(
    "/{incident_id}/events",
    response_model=list[schemas.IncidentEventRead],
)
def list_events_for_incident(
    incident_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[models.IncidentEvent]:
    """List events for an incident, oldest first (timeline order).

    Returns 404 if the incident does not exist.
    """
    _get_incident_or_404(incident_id, db)
    # Secondary sort by `id` so two rows that share a timestamp still have a
    # stable order between requests.
    stmt = (
        select(models.IncidentEvent)
        .where(models.IncidentEvent.incident_id == incident_id)
        .order_by(asc(models.IncidentEvent.created_at), asc(models.IncidentEvent.id))
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


@router.get(
    "/{incident_id}/evidence",
    response_model=list[schemas.IncidentEvidenceRead],
)
def list_evidence_for_incident(
    incident_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[models.IncidentEvidence]:
    """List evidence for an incident, oldest first (timeline order).

    Returns 404 if the incident does not exist.
    """
    _get_incident_or_404(incident_id, db)
    # Secondary sort by `id` for the same stability reason as events above.
    stmt = (
        select(models.IncidentEvidence)
        .where(models.IncidentEvidence.incident_id == incident_id)
        .order_by(
            asc(models.IncidentEvidence.created_at),
            asc(models.IncidentEvidence.id),
        )
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


@router.post(
    "/{incident_id}/events",
    response_model=schemas.IncidentEventRead,
    status_code=status.HTTP_201_CREATED,
)
def add_event(
    incident_id: UUID,
    payload: schemas.IncidentEventCreate,
    db: Session = Depends(get_db),
) -> models.IncidentEvent:
    _get_incident_or_404(incident_id, db)
    event = models.IncidentEvent(
        incident_id=incident_id, **payload.model_dump(mode="json")
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.post(
    "/{incident_id}/evidence",
    response_model=schemas.IncidentEvidenceRead,
    status_code=status.HTTP_201_CREATED,
)
def add_evidence(
    incident_id: UUID,
    payload: schemas.IncidentEvidenceCreate,
    db: Session = Depends(get_db),
) -> models.IncidentEvidence:
    _get_incident_or_404(incident_id, db)
    evidence = models.IncidentEvidence(
        incident_id=incident_id, **payload.model_dump(mode="json")
    )
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    return evidence


@router.post(
    "/{incident_id}/recommendations",
    response_model=schemas.RecommendationRead,
    status_code=status.HTTP_201_CREATED,
)
def add_recommendation(
    incident_id: UUID,
    payload: schemas.RecommendationCreate,
    db: Session = Depends(get_db),
) -> models.Recommendation:
    _get_incident_or_404(incident_id, db)
    rec = models.Recommendation(
        incident_id=incident_id, **payload.model_dump(mode="json")
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec
