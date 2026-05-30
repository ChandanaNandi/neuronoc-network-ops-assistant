"""Phase 23 auth + hashing tests.

Coverage:
  - hashing: round-trip success, wrong-password rejection, missing/bad-hash
    inputs reject without raising, custom iteration count round-trips.
  - login: success, unknown-user 401, wrong-password 401, operator without
    a password_hash 401 (all three return the same generic "invalid
    credentials" detail so the API doesn't leak user existence).
  - GET /api/auth/me: returns the authenticated operator; 401 when
    unauthenticated; 401 when token is malformed.
  - logout: invalidates the token (subsequent /me returns 401).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.hashing import hash_password, verify_password
from app.db.models import Operator


def _seed(db_session: Session, *, display_name: str, role: str,
          password: str | None = "demo-password") -> Operator:
    op = Operator(
        display_name=display_name,
        role=role,
        password_hash=hash_password(password) if password else None,
    )
    db_session.add(op)
    db_session.flush()
    return op


# ---------- hashing primitives ----------


def test_hash_password_roundtrips() -> None:
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_hash_password_rejects_wrong_password() -> None:
    h = hash_password("correct")
    assert verify_password("wrong", h) is False


def test_hash_password_format_is_self_describing() -> None:
    h = hash_password("anything")
    # algorithm$iterations$salt$hash, no whitespace
    parts = h.split("$")
    assert len(parts) == 4
    assert parts[0] == "pbkdf2_sha256"
    assert parts[1].isdigit() and int(parts[1]) >= 100_000
    assert len(parts[2]) > 0 and len(parts[3]) > 0


def test_verify_password_rejects_malformed_hash_without_raising() -> None:
    """A None or garbage stored hash MUST return False, not crash —
    important so the login handler can give a uniform 401."""
    assert verify_password("anything", None) is False
    assert verify_password("anything", "") is False
    assert verify_password("anything", "not-a-real-hash") is False
    assert verify_password("anything", "pbkdf2_sha256$bad$x$y") is False
    assert verify_password("anything", "wrong_algo$1$x$y") is False


def test_hash_password_rejects_empty() -> None:
    import pytest
    with pytest.raises(ValueError, match="non-empty"):
        hash_password("")


def test_hash_password_rejects_weak_iterations() -> None:
    import pytest
    with pytest.raises(ValueError, match="below minimum"):
        hash_password("anything", iterations=1000)


# ---------- POST /api/auth/login ----------


def test_login_success_returns_token_and_operator(
    client: TestClient, db_session: Session
) -> None:
    _seed(db_session, display_name="logger-1", role="admin")
    resp = client.post(
        "/api/auth/login",
        json={"display_name": "logger-1", "password": "demo-password"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"]
    assert body["operator"]["display_name"] == "logger-1"
    assert body["operator"]["role"] == "admin"


def test_login_wrong_password_returns_401(
    client: TestClient, db_session: Session
) -> None:
    _seed(db_session, display_name="logger-2", role="admin")
    resp = client.post(
        "/api/auth/login",
        json={"display_name": "logger-2", "password": "WRONG"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid credentials"


def test_login_unknown_user_returns_401_with_same_detail(
    client: TestClient,
) -> None:
    """Unknown user vs wrong password vs no-password-set must all return
    the same 401 message — otherwise the API leaks which usernames
    exist."""
    resp = client.post(
        "/api/auth/login",
        json={"display_name": "no-such-user", "password": "demo"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid credentials"


def test_login_operator_without_password_returns_401(
    client: TestClient, db_session: Session
) -> None:
    _seed(
        db_session, display_name="no-pw", role="admin", password=None
    )
    resp = client.post(
        "/api/auth/login",
        json={"display_name": "no-pw", "password": "demo"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid credentials"


# ---------- GET /api/auth/me ----------


def test_me_unauthenticated_returns_401(client: TestClient) -> None:
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_with_malformed_header_returns_401(client: TestClient) -> None:
    # Wrong scheme.
    resp = client.get(
        "/api/auth/me",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert resp.status_code == 401
    # Missing token.
    resp = client.get(
        "/api/auth/me", headers={"Authorization": "Bearer"}
    )
    assert resp.status_code == 401


def test_me_returns_authenticated_operator(
    client: TestClient, db_session: Session
) -> None:
    _seed(db_session, display_name="me-tester", role="operator")
    token = client.post(
        "/api/auth/login",
        json={"display_name": "me-tester", "password": "demo-password"},
    ).json()["token"]
    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "me-tester"
    assert body["role"] == "operator"


def test_me_with_unknown_token_returns_401(client: TestClient) -> None:
    resp = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


# ---------- POST /api/auth/logout ----------


def test_logout_invalidates_token(
    client: TestClient, db_session: Session
) -> None:
    _seed(db_session, display_name="logout-tester", role="admin")
    token = client.post(
        "/api/auth/login",
        json={"display_name": "logout-tester", "password": "demo-password"},
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # /me works first.
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    # Logout.
    logout_resp = client.post("/api/auth/logout", headers=headers)
    assert logout_resp.status_code == 204

    # /me now fails 401.
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_logout_unauthenticated_returns_401(client: TestClient) -> None:
    """Calling logout without a bearer token should 401, not silently
    no-op — otherwise a confused client would think it had logged out."""
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 401
