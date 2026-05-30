"""Phase 16A validation preview tests.

Two contracts to defend:
1. The validation preview surfaces ONLY the validation surface of a
   persisted remediation plan; non-plans / malformed plans get specific
   400/404 errors.
2. The validation package + its HTTP wrapper must not import a remote
   execution library. A parallel AST safety scan (mirror of the one in
   `test_remediation.py`) fails the build if any do.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import Recommendation
from app.remediation.planner import (
    REMEDIATION_PLAN_TYPE,
    build_remediation_plan,
    persist_remediation_recommendation,
)
from app.remediation import planner as planner_module
from app.simulator.seed import apply_scenario
from app.validation.preview import (
    NotRemediationPlanError,
    PlanParseError,
    RecommendationNotFoundError,
    build_validation_preview,
    extract_fenced_json,
)


@pytest.fixture(autouse=True)
def _stub_rca(monkeypatch: pytest.MonkeyPatch) -> None:
    """Match test_remediation.py's pattern - keep the planner deterministic
    and Ollama-independent during tests so persisted plans round-trip
    cleanly without needing a live model daemon."""
    monkeypatch.setattr(
        planner_module, "generate_rca_explanation", lambda *a, **k: None
    )


def _seed_and_persist(db: Session, scenario: str = "bgp_neighbor_down"):
    incident = apply_scenario(db, scenario)
    plan = build_remediation_plan(db, incident.id)
    rec = persist_remediation_recommendation(db, plan)
    return incident, plan, rec


# ---------- helper: extract_fenced_json ----------


def test_extract_fenced_json_parses_planner_format() -> None:
    """The planner's _render_details produces a `\\n\\n```json\\n{...}\\n``` `
    suffix; the helper must round-trip that verbatim."""
    payload = {"a": 1, "list": ["x", "y"], "nested": {"k": True}}
    blob = f"some prose\n\n```json\n{json.dumps(payload, indent=2)}\n```"
    parsed = extract_fenced_json(blob)
    assert parsed == payload


def test_extract_fenced_json_returns_none_on_missing_fence() -> None:
    assert extract_fenced_json("no fenced block here") is None
    assert extract_fenced_json("") is None


def test_extract_fenced_json_returns_none_on_malformed_json() -> None:
    assert extract_fenced_json("```json\n{not valid json,}\n```") is None


def test_extract_fenced_json_returns_none_on_non_object_root() -> None:
    # JSON parses fine but isn't a dict - validation preview only knows
    # how to project an object.
    assert extract_fenced_json('```json\n["a", "b"]\n```') is None


# ---------- happy path (helper + API) ----------


def test_build_validation_preview_returns_plan_validation_fields(
    db_session: Session,
) -> None:
    _, plan, rec = _seed_and_persist(db_session)
    preview = build_validation_preview(db_session, rec.id)

    assert preview.recommendation_id == rec.id
    assert preview.incident_id == rec.incident_id
    assert preview.plan_title == rec.title
    assert preview.plan_risk == rec.risk
    assert preview.validation_source == "remediation_plan"
    assert preview.executable is False

    # Same lists the planner wrote, in the same order. We don't pin exact
    # content because templates evolve, but the shape contract is firm.
    assert preview.pre_checks == plan.pre_checks
    assert preview.post_checks == plan.post_checks
    assert preview.validation_criteria == plan.validation_criteria
    assert preview.rollback_steps == plan.rollback_steps
    assert preview.safety_notes == plan.safety_notes


def test_api_get_validation_preview_happy_path(
    client: TestClient, db_session: Session
) -> None:
    _, _, rec = _seed_and_persist(db_session)
    response = client.get(
        f"/api/validation/recommendations/{rec.id}/preview"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recommendation_id"] == str(rec.id)
    assert body["validation_source"] == "remediation_plan"
    assert body["executable"] is False
    # generated_at must be a parseable ISO timestamp.
    from datetime import datetime
    datetime.fromisoformat(body["generated_at"])
    # The actionable fields stay OUT of the preview - this is the contract
    # that keeps the response unmistakably non-executable.
    assert "proposed_commands" not in body
    assert "proposed_ansible_playbook" not in body


# ---------- error paths ----------


def test_api_get_validation_preview_404_for_missing_recommendation(
    client: TestClient,
) -> None:
    response = client.get(
        f"/api/validation/recommendations/{uuid4()}/preview"
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_build_validation_preview_raises_for_missing_recommendation(
    db_session: Session,
) -> None:
    with pytest.raises(RecommendationNotFoundError):
        build_validation_preview(db_session, uuid4())


def test_api_get_validation_preview_400_for_non_plan_recommendation(
    client: TestClient, db_session: Session
) -> None:
    """A recommendation that exists but isn't a remediation plan has no
    validation surface; the API must reject with 400 (not 404)."""
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    create = client.post(
        f"/api/incidents/{incident.id}/recommendations",
        json={
            "recommendation_type": "informational_note",
            "title": "fyi",
            "details": "no plan json here",
            "risk": "low",
        },
    )
    assert create.status_code == 201, create.text
    rec_id = create.json()["id"]

    response = client.get(
        f"/api/validation/recommendations/{rec_id}/preview"
    )
    assert response.status_code == 400
    assert REMEDIATION_PLAN_TYPE in response.json()["detail"]


def test_build_validation_preview_raises_for_non_plan_recommendation(
    db_session: Session,
) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    rec = Recommendation(
        incident_id=incident.id,
        recommendation_type="informational_note",
        title="fyi",
        details="no plan",
        risk="low",
        requires_approval=False,
    )
    db_session.add(rec)
    db_session.commit()
    db_session.refresh(rec)

    with pytest.raises(NotRemediationPlanError):
        build_validation_preview(db_session, rec.id)


def test_api_get_validation_preview_400_for_plan_missing_fenced_json(
    client: TestClient, db_session: Session
) -> None:
    """A row tagged `remediation_plan` whose `details` doesn't contain a
    fenced JSON block must produce a 400 with a clear parse-error message.
    Mocks a hand-edited / corrupted row by writing directly via SQLAlchemy."""
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    rec = Recommendation(
        incident_id=incident.id,
        recommendation_type=REMEDIATION_PLAN_TYPE,
        title="hand-edited plan",
        details="DRAFT remediation plan (broken). No fenced JSON below.",
        risk="medium",
        requires_approval=True,
    )
    db_session.add(rec)
    db_session.commit()
    db_session.refresh(rec)

    response = client.get(
        f"/api/validation/recommendations/{rec.id}/preview"
    )
    assert response.status_code == 400
    assert "fenced" in response.json()["detail"].lower() or "json" in response.json()["detail"].lower()


def test_api_get_validation_preview_400_for_plan_failing_schema_validation(
    client: TestClient, db_session: Session
) -> None:
    """A row tagged `remediation_plan` whose JSON parses but is missing
    fields the `RemediationPlan` model requires (e.g. `confidence`) must
    surface the field-level validation error as 400, not 500."""
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    bad_plan = {
        # missing: confidence (required), summary (required), risk, title, etc.
        "incident_id": str(incident.id),
        "plan_type": "bgp_neighbor_recovery",
    }
    rec = Recommendation(
        incident_id=incident.id,
        recommendation_type=REMEDIATION_PLAN_TYPE,
        title="invalid plan body",
        details=(
            "DRAFT remediation plan (broken). REQUIRES HUMAN APPROVAL.\n\n"
            f"```json\n{json.dumps(bad_plan, indent=2)}\n```"
        ),
        risk="medium",
        requires_approval=True,
    )
    db_session.add(rec)
    db_session.commit()
    db_session.refresh(rec)

    response = client.get(
        f"/api/validation/recommendations/{rec.id}/preview"
    )
    assert response.status_code == 400
    assert "schema" in response.json()["detail"].lower() or "validation" in response.json()["detail"].lower()


def test_build_validation_preview_raises_planparse_for_bad_json(
    db_session: Session,
) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    rec = Recommendation(
        incident_id=incident.id,
        recommendation_type=REMEDIATION_PLAN_TYPE,
        title="bad json plan",
        details="```json\n{not-json,}\n```",
        risk="medium",
        requires_approval=True,
    )
    db_session.add(rec)
    db_session.commit()
    db_session.refresh(rec)

    with pytest.raises(PlanParseError):
        build_validation_preview(db_session, rec.id)


# ---------- safety: app/validation must not import an execution library ----------


_FORBIDDEN_EXECUTION_LIBS = frozenset(
    {
        "subprocess",
        "ansible_runner",
        "netmiko",
        "napalm",
        "paramiko",
        "pexpect",
        "fabric",
        "scrapli",
    }
)


def _root_module(name: str) -> str:
    return name.split(".", 1)[0]


def _scan_files_for_execution_imports(files: list[Path]) -> list[str]:
    offenders: list[str] = []
    for py_file in files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _root_module(alias.name) in _FORBIDDEN_EXECUTION_LIBS:
                        offenders.append(
                            f"{py_file.name}:{node.lineno}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if _root_module(node.module) in _FORBIDDEN_EXECUTION_LIBS:
                    offenders.append(
                        f"{py_file.name}:{node.lineno}: "
                        f"from {node.module} import ..."
                    )
    return offenders


def test_validation_package_blocks_execution_library_imports() -> None:
    """Mirror of test_remediation.py's safety scan. The validation preview
    is plan-only and read-only; any import of a remote-execution library
    inside `app/validation/` or `app/api/validation.py` fails the build.

    Strings inside docstrings or comments are ignored on purpose - only
    actual `Import` / `ImportFrom` nodes count."""
    app_root = Path(__file__).resolve().parents[1] / "app"
    files: list[Path] = list((app_root / "validation").rglob("*.py"))
    files.append(app_root / "api" / "validation.py")
    assert files, "no validation files found"

    offenders = _scan_files_for_execution_imports(files)
    assert not offenders, (
        "Validation code must never import a remote-execution library:\n  "
        + "\n  ".join(offenders)
    )
