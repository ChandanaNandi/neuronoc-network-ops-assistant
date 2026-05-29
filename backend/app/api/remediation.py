from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.anomaly.engine import IncidentNotFoundError
from app.db.models import Incident, Recommendation
from app.db.session import get_db
from app.remediation.planner import (
    build_remediation_plan,
    persist_remediation_recommendation,
)
from app.schemas.incidents import RecommendationRead
from app.schemas.remediation import RemediationPlan

router = APIRouter(prefix="/api/remediation", tags=["remediation"])


@router.post(
    "/incidents/{incident_id}/plan",
    response_model=RemediationPlan,
    status_code=status.HTTP_201_CREATED,
)
def generate_plan(
    incident_id: UUID, db: Session = Depends(get_db)
) -> RemediationPlan:
    try:
        plan = build_remediation_plan(db, incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    persist_remediation_recommendation(db, plan)
    return plan


@router.get(
    "/incidents/{incident_id}/plans",
    response_model=list[RecommendationRead],
)
def list_plans(
    incident_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[Recommendation]:
    if db.get(Incident, incident_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
        )
    stmt = (
        select(Recommendation)
        .where(Recommendation.incident_id == incident_id)
        .where(Recommendation.recommendation_type == "remediation_plan")
        .order_by(desc(Recommendation.created_at))
        .limit(limit)
    )
    return list(db.scalars(stmt).all())
