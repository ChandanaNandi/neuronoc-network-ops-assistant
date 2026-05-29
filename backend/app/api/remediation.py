from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.anomaly.engine import IncidentNotFoundError
from app.db.models import ApprovalStatus, Incident, Recommendation
from app.db.session import get_db
from app.remediation.planner import (
    OperatorNotFoundError,
    RecommendationNotFoundError,
    WrongRecommendationTypeError,
    build_remediation_plan,
    persist_remediation_recommendation,
    set_recommendation_approval,
)
from app.schemas.incidents import ApprovalRequest, RecommendationRead
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


def _apply_approval(
    db: Session,
    recommendation_id: UUID,
    status_value: ApprovalStatus,
    payload: ApprovalRequest,
) -> Recommendation:
    try:
        return set_recommendation_approval(
            db,
            recommendation_id,
            status_value,
            operator_name=payload.operator_name,
            operator_id=payload.operator_id,
            note=payload.note,
        )
    except RecommendationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except WrongRecommendationTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except OperatorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.post(
    "/recommendations/{recommendation_id}/approve",
    response_model=RecommendationRead,
)
def approve_recommendation(
    recommendation_id: UUID,
    payload: ApprovalRequest,
    db: Session = Depends(get_db),
) -> Recommendation:
    """Mark a remediation plan as APPROVED. Records intent only - no command
    is run, no device is contacted. Idempotent same-state calls update the
    operator/timestamp/note so the latest decision is captured."""
    return _apply_approval(
        db, recommendation_id, ApprovalStatus.approved, payload
    )


@router.post(
    "/recommendations/{recommendation_id}/reject",
    response_model=RecommendationRead,
)
def reject_recommendation(
    recommendation_id: UUID,
    payload: ApprovalRequest,
    db: Session = Depends(get_db),
) -> Recommendation:
    """Mark a remediation plan as REJECTED. Same safety contract as approve:
    records intent only, never executes anything."""
    return _apply_approval(
        db, recommendation_id, ApprovalStatus.rejected, payload
    )


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
