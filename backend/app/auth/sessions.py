"""Phase 23 session token store.

Bearer-token sessions backed by the `operator_sessions` table. Token is
an opaque `secrets.token_urlsafe(32)` value (43 chars URL-safe base64).
Default TTL is 7 days; expired rows fail `lookup_session` lookup but
are not auto-pruned (a future phase can add a cleanup helper if needed
— a few stale rows per dev cycle don't matter).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Operator, OperatorSession

DEFAULT_SESSION_TTL = timedelta(days=7)


def create_session(
    db: Session,
    operator: Operator,
    *,
    ttl: timedelta = DEFAULT_SESSION_TTL,
) -> OperatorSession:
    """Mint a new bearer-token session row for `operator`.

    Returns the persisted `OperatorSession` (token in `.token`). The
    caller is responsible for committing if it's not relying on a
    request-scoped session that commits at handler return.
    """
    now = datetime.now(timezone.utc)
    session_row = OperatorSession(
        operator_id=operator.id,
        token=secrets.token_urlsafe(32),
        expires_at=now + ttl,
    )
    db.add(session_row)
    db.commit()
    db.refresh(session_row)
    return session_row


def lookup_session(db: Session, token: str) -> tuple[OperatorSession, Operator] | None:
    """Resolve a bearer token to its (session, operator) pair.

    Returns `None` if the token is missing, unknown, or expired. Does
    NOT extend the session expiry on lookup — sliding-window expiry is
    deliberately out of scope for the local-dev auth story.
    """
    if not token:
        return None
    row = db.scalar(
        select(OperatorSession).where(OperatorSession.token == token)
    )
    if row is None:
        return None
    if row.expires_at <= datetime.now(timezone.utc):
        return None
    operator = db.get(Operator, row.operator_id)
    if operator is None:
        return None
    return row, operator


def delete_session(db: Session, token: str) -> bool:
    """Best-effort logout. Returns True if a row was deleted, False if
    the token was unknown. Called from the `/api/auth/logout` handler."""
    row = db.scalar(
        select(OperatorSession).where(OperatorSession.token == token)
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def delete_sessions_for_operator(db: Session, operator_id: UUID) -> int:
    """Force-logout every session for one operator. Returns row count
    deleted. Provided for the rare admin-revocation case; not wired to
    an endpoint in Phase 23."""
    rows = list(
        db.scalars(
            select(OperatorSession).where(
                OperatorSession.operator_id == operator_id
            )
        ).all()
    )
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)
