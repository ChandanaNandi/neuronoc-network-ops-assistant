"""Phase 13A operator tests.

NOT auth tests - operators are just rows in a table for audit attribution.
"""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Operator
from app.operators import seed as seed_module


def test_list_operators_initially_empty_or_existing(client: TestClient) -> None:
    response = client.get("/api/operators")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_create_operator(client: TestClient) -> None:
    response = client.post(
        "/api/operators",
        json={"display_name": "alice-e2e", "role": "operator"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["display_name"] == "alice-e2e"
    assert body["role"] == "operator"
    assert body["id"]
    assert body["created_at"]


def test_create_operator_defaults_role_to_operator(client: TestClient) -> None:
    response = client.post(
        "/api/operators",
        json={"display_name": "bob-default-role"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "operator"


def test_create_operator_409_on_duplicate(client: TestClient) -> None:
    client.post("/api/operators", json={"display_name": "duplicate-target"})
    response = client.post(
        "/api/operators", json={"display_name": "duplicate-target"}
    )
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_create_operator_validates_role(client: TestClient) -> None:
    response = client.post(
        "/api/operators",
        json={"display_name": "carol", "role": "superadmin"},
    )
    assert response.status_code == 422


def test_seed_cli_creates_operator(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(seed_module, "SessionLocal", fake_session_local)
    exit_code = seed_module.main(
        ["--name", "seed-test-operator", "--role", "admin"]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "created" in out
    op = db_session.scalar(
        select(Operator).where(Operator.display_name == "seed-test-operator")
    )
    assert op is not None
    assert op.role == "admin"


def test_seed_cli_is_idempotent_by_display_name(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(seed_module, "SessionLocal", fake_session_local)

    seed_module.main(["--name", "idempotent-test", "--role", "operator"])
    capsys.readouterr()  # drain
    seed_module.main(["--name", "idempotent-test", "--role", "operator"])
    out = capsys.readouterr().out
    assert "already exists" in out

    count = db_session.scalar(
        select(func.count())
        .select_from(Operator)
        .where(Operator.display_name == "idempotent-test")
    )
    assert count == 1


def test_seed_cli_updates_role_on_rerun(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(seed_module, "SessionLocal", fake_session_local)

    seed_module.main(["--name", "role-change-test", "--role", "operator"])
    capsys.readouterr()
    seed_module.main(["--name", "role-change-test", "--role", "admin"])
    out = capsys.readouterr().out
    # Phase 23 reworded the CLI output (now reports `(role->admin)` etc.
    # so multi-change summaries fit in one line); test the meaningful
    # substring rather than the exact prior phrase.
    assert "role->admin" in out

    op = db_session.scalar(
        select(Operator).where(Operator.display_name == "role-change-test")
    )
    assert op is not None
    assert op.role == "admin"


def test_seed_cli_rejects_invalid_role() -> None:
    with pytest.raises(SystemExit) as exc:
        seed_module.main(["--name", "x", "--role", "root"])
    assert exc.value.code == 2
