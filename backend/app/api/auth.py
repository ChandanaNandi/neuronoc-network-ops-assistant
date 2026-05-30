"""Phase 23 auth HTTP endpoints.

Local bearer-token auth backed by the `operator_sessions` table:
  - POST /api/auth/login   { display_name, password } -> { token, operator }
  - POST /api/auth/logout  (bearer-auth required)     -> 204
  - GET  /api/auth/me      (bearer-auth required)     -> operator

Login failure (bad password OR unknown user OR operator has no
password set) returns 401 with a generic "invalid credentials"
message; we deliberately don't distinguish the cases so the API
doesn't leak which usernames exist.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_operator
from app.auth.hashing import verify_password
from app.auth.sessions import create_session, delete_session
from app.db.models import Operator
from app.db.session import get_db
from app.schemas.auth import LoginRequest, LoginResponse
from app.schemas.operators import OperatorRead

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    operator = db.scalar(
        select(Operator).where(Operator.display_name == payload.display_name)
    )
    # Generic 401 covers all three failure modes (unknown user, no
    # password set, wrong password) — we don't leak which one.
    if operator is None or not verify_password(
        payload.password, operator.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    session_row = create_session(db, operator)
    return LoginResponse(
        token=session_row.token,
        operator=OperatorRead.model_validate(operator),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    authorization: str | None = Header(default=None),
    # Force-resolve the operator before we delete the row so an
    # unauthenticated logout returns 401 rather than a silent no-op.
    _current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
) -> Response:
    # `get_current_operator` already validated the bearer; extract the
    # raw token here to delete the session row.
    assert authorization is not None
    token = authorization.split(maxsplit=1)[1]
    delete_session(db, token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=OperatorRead)
def me(
    current: Operator = Depends(get_current_operator),
) -> Operator:
    """Return the authenticated operator — useful for the frontend to
    rehydrate login state on page load."""
    return current
