from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.agents.runner import run_incident_analysis
from app.anomaly.engine import IncidentNotFoundError
from app.db.models import AgentRun, Incident
from app.db.session import get_db
from app.schemas.agents import AgentRunRead

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.post(
    "/incidents/{incident_id}/analyze",
    response_model=AgentRunRead,
    status_code=status.HTTP_201_CREATED,
)
def analyze_incident_endpoint(
    incident_id: UUID, db: Session = Depends(get_db)
) -> AgentRun:
    try:
        run = run_incident_analysis(db, incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    # Re-fetch with eagerly-loaded steps so the response includes them.
    return db.scalar(
        select(AgentRun)
        .options(selectinload(AgentRun.steps))
        .where(AgentRun.id == run.id)
    )


@router.get("/runs/{run_id}", response_model=AgentRunRead)
def get_run(run_id: UUID, db: Session = Depends(get_db)) -> AgentRun:
    run = db.scalar(
        select(AgentRun)
        .options(selectinload(AgentRun.steps))
        .where(AgentRun.id == run_id)
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="run not found"
        )
    return run


@router.get(
    "/incidents/{incident_id}/runs", response_model=list[AgentRunRead]
)
def list_runs_for_incident(
    incident_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[AgentRun]:
    if db.get(Incident, incident_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
        )
    stmt = (
        select(AgentRun)
        .options(selectinload(AgentRun.steps))
        .where(AgentRun.incident_id == incident_id)
        .order_by(desc(AgentRun.created_at))
        .limit(limit)
    )
    return list(db.scalars(stmt).all())
