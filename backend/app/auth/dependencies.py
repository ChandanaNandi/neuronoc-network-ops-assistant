"""Phase 23 FastAPI auth dependencies.

`get_current_operator` reads `Authorization: Bearer <token>`, resolves
the session, and returns the bound `Operator`. Missing/invalid token
raises 401. `require_role(role)` wraps that with a 403 if the
authenticated operator's role doesn't match.

These are the ONLY enforcement primitives in the project. Endpoints
that previously took no auth still take no auth — Phase 23 wires these
into the remediation approve/reject endpoints only.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.sessions import lookup_session
from app.db.models import Operator
from app.db.session import get_db


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2:
        return None
    if parts[0].lower() != "bearer":
        return None
    return parts[1]


def get_current_operator(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Operator:
    """Resolve the bearer token to an Operator or raise 401.

    Single source of truth for "is this request authenticated as a known
    operator". Used directly when ANY authenticated operator is
    acceptable; wrapped by `require_role` when an additional RBAC
    check is needed.
    """
    token = _extract_bearer(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    resolved = lookup_session(db, token)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _, operator = resolved
    return operator


def require_role(role: str):
    """Dependency factory: require the authenticated operator's role
    to equal `role`. Authenticated-but-wrong-role returns 403."""

    def _checker(
        current: Operator = Depends(get_current_operator),
    ) -> Operator:
        if current.role != role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"operator role '{current.role}' is not authorized; "
                    f"this action requires role '{role}'"
                ),
            )
        return current

    return _checker
