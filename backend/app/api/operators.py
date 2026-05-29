from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import asc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Operator
from app.db.session import get_db
from app.schemas.operators import OperatorCreate, OperatorRead

router = APIRouter(prefix="/api/operators", tags=["operators"])


@router.get("", response_model=list[OperatorRead])
def list_operators(db: Session = Depends(get_db)) -> list[Operator]:
    stmt = select(Operator).order_by(asc(Operator.display_name))
    return list(db.scalars(stmt).all())


@router.post("", response_model=OperatorRead, status_code=status.HTTP_201_CREATED)
def create_operator(
    payload: OperatorCreate, db: Session = Depends(get_db)
) -> Operator:
    """Create a new operator. 409 if `display_name` is already taken.

    Phase 13A: minimal local identity, no auth enforcement. This endpoint
    exists so the operator console (and CI / scripts) can add operators
    without dropping into psql."""
    op = Operator(
        display_name=payload.display_name,
        role=payload.role.value,
    )
    db.add(op)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"operator with display_name '{payload.display_name}' already exists",
        ) from exc
    db.refresh(op)
    return op
