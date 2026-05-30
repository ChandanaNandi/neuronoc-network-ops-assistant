from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.anomaly.engine import IncidentNotFoundError
from app.auth.dependencies import require_role
from app.db.models import ApprovalStatus, Incident, Operator, Recommendation
from app.db.session import get_db
from app.remediation.planner import (
    RecommendationNotFoundError,
    WrongRecommendationTypeError,
    build_remediation_plan,
    persist_remediation_recommendation,
    set_recommendation_approval,
)
from app.schemas.incidents import ApprovalRequest, RecommendationRead
from app.schemas.remediation import RemediationPlan

router = APIRouter(prefix="/api/remediation", tags=["remediation"])

# Phase 23: approval and rejection are admin-only. Other endpoints in
# this router (plan generation, plan listing) remain unauthenticated
# per scope — only the write-state actions on existing plans require
# auth.
_require_admin = require_role("admin")


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
    operator: Operator,
) -> Recommendation:
    try:
        return set_recommendation_approval(
            db,
            recommendation_id,
            status_value,
            operator=operator,
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


@router.post(
    "/recommendations/{recommendation_id}/approve",
    response_model=RecommendationRead,
)
def approve_recommendation(
    recommendation_id: UUID,
    payload: ApprovalRequest,
    db: Session = Depends(get_db),
    current_operator: Operator = Depends(_require_admin),
) -> Recommendation:
    """Mark a remediation plan as APPROVED. Records intent only — no
    command is run, no device is contacted. Idempotent same-state calls
    update the operator/timestamp/note so the latest decision is
    captured.

    Phase 23: requires bearer-token auth (401 if missing/invalid) AND
    role `admin` (403 if authenticated as `operator`). The approving
    operator's identity comes from the authenticated session, NOT from
    the request body (body carries `note` only).
    """
    return _apply_approval(
        db,
        recommendation_id,
        ApprovalStatus.approved,
        payload,
        current_operator,
    )


@router.post(
    "/recommendations/{recommendation_id}/reject",
    response_model=RecommendationRead,
)
def reject_recommendation(
    recommendation_id: UUID,
    payload: ApprovalRequest,
    db: Session = Depends(get_db),
    current_operator: Operator = Depends(_require_admin),
) -> Recommendation:
    """Mark a remediation plan as REJECTED. Same safety contract as
    approve: records intent only, never executes anything. Same Phase 23
    auth/RBAC requirements."""
    return _apply_approval(
        db,
        recommendation_id,
        ApprovalStatus.rejected,
        payload,
        current_operator,
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
