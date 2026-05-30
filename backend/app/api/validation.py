"""Phase 16A HTTP wrapper for the read-only validation preview.

Single endpoint, GET-only, never mutates database state. The matching
safety test scans this file's imports against the forbidden-execution-
library set; the validation package as a whole must remain plan-only.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.validation import ValidationPreviewRead
from app.validation.preview import (
    NotRemediationPlanError,
    PlanParseError,
    RecommendationNotFoundError,
    build_validation_preview,
)

router = APIRouter(prefix="/api/validation", tags=["validation"])


@router.get(
    "/recommendations/{recommendation_id}/preview",
    response_model=ValidationPreviewRead,
)
def get_validation_preview(
    recommendation_id: UUID, db: Session = Depends(get_db)
) -> ValidationPreviewRead:
    """Return the validation surface for a persisted remediation plan.

    Read-only. Never executes a command, never opens a device connection.
    The returned payload pins `executable=False` and intentionally omits
    `proposed_commands` / `proposed_ansible_playbook` so it can't be
    mistaken for an actionable artifact.
    """
    try:
        return build_validation_preview(db, recommendation_id)
    except RecommendationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except NotRemediationPlanError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except PlanParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
