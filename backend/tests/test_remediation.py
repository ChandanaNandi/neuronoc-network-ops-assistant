"""Phase 7 remediation planner tests.

Plan-only contract is enforced two ways:
1. Every generated plan must have `requires_approval=True` and rollback steps.
2. A safety test scans the remediation package source for forbidden execution
   imports (subprocess / ansible_runner / netmiko / napalm / paramiko / pexpect).
"""

import ast
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.anomaly.engine import IncidentNotFoundError
from app.db.models import Incident, Recommendation
from app.remediation import planner as planner_module
from app.remediation.planner import (
    build_remediation_plan,
    persist_remediation_recommendation,
)
from app.remediation.templates import TEMPLATES, pick_template, template_default
from app.simulator.seed import apply_scenario


@pytest.fixture(autouse=True)
def _stub_rca(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the planner deterministic and Ollama-independent.

    The planner calls generate_rca_explanation for narrative context but the
    templates do not actually consume the RCA payload yet (Pyright marks
    `_rca` unused in every template). Patching this here keeps the suite
    fast and stops it from depending on a live Ollama daemon."""
    monkeypatch.setattr(
        planner_module, "generate_rca_explanation", lambda *a, **k: None
    )


def _seed(db: Session, scenario: str):
    return apply_scenario(db, scenario)


# ---------- per-template assertions ----------


def test_bgp_plan_requires_approval_and_has_rollback(db_session: Session) -> None:
    incident = _seed(db_session, "bgp_neighbor_down")
    plan = build_remediation_plan(db_session, incident.id)

    assert plan.requires_approval is True
    assert plan.rollback_steps, "BGP plan must include rollback steps"
    # The risky command must be tagged as requiring approval.
    risky = "\n".join(plan.proposed_commands)
    assert "REQUIRES APPROVAL" in risky or "clear ip bgp" in risky
    # Ansible draft must be marked non-executable.
    assert "DRAFT ONLY" in plan.proposed_ansible_playbook
    assert "when: false" in plan.proposed_ansible_playbook


def test_acl_plan_mentions_policy_diff_and_approval(db_session: Session) -> None:
    incident = _seed(db_session, "acl_blocking_traffic")
    plan = build_remediation_plan(db_session, incident.id)

    text_blob = (
        "\n".join(plan.pre_checks)
        + "\n".join(plan.proposed_commands)
        + plan.proposed_ansible_playbook
        + "\n".join(plan.safety_notes)
    ).lower()
    assert "diff" in text_blob or "change record" in text_blob, (
        "ACL plan must reference diffing the policy / change record"
    )
    assert "approval" in text_blob, "ACL plan must explicitly say approval is required"
    assert plan.requires_approval is True


def test_interface_errors_plan_does_not_propose_config_change_first(
    db_session: Session,
) -> None:
    incident = _seed(db_session, "interface_errors_spike")
    plan = build_remediation_plan(db_session, incident.id)

    assert plan.proposed_commands, "interface plan should propose something"
    first = plan.proposed_commands[0].lower()
    # First action must be observational, NOT a config-edit command.
    forbidden_first = ("conf t", "configure terminal", "shutdown", "no shutdown")
    assert not any(tok in first for tok in forbidden_first), (
        f"first proposed action should not be a config change: {first!r}"
    )
    # Show / inspect commands are expected.
    assert first.startswith("show") or "inspect" in first or "#" in first


def test_route_missing_plan_includes_route_validation_in_pre_and_post(
    db_session: Session,
) -> None:
    incident = _seed(db_session, "route_missing")
    plan = build_remediation_plan(db_session, incident.id)

    pre_blob = "\n".join(plan.pre_checks).lower()
    post_blob = "\n".join(plan.post_checks).lower()
    assert "show ip route" in pre_blob, "pre-checks must query the route table"
    assert "show ip route" in post_blob or "rib" in post_blob, (
        "post-checks must re-verify the route table"
    )


def test_unknown_incident_gets_safe_generic_plan(db_session: Session) -> None:
    # Synthesise an incident with a type that doesn't match any specific template.
    incident = Incident(
        title="exotic event",
        severity="low",
        incident_type="exotic_unmapped_type",
        summary="operator wrote this",
    )
    db_session.add(incident)
    db_session.commit()
    plan = build_remediation_plan(db_session, incident.id)

    assert plan.plan_type == "generic_investigation"
    assert plan.requires_approval is True
    assert any("manual" in n.lower() for n in plan.safety_notes), (
        "default plan must clearly say manual investigation is required"
    )
    # No config-changing commands should be proposed by the default template.
    cmd_blob = "\n".join(plan.proposed_commands).lower()
    assert "configure terminal" not in cmd_blob
    assert "no shutdown" not in cmd_blob


# ---------- persistence ----------


def test_persist_creates_recommendation_tagged_remediation_plan(
    db_session: Session,
) -> None:
    incident = _seed(db_session, "bgp_neighbor_down")
    plan = build_remediation_plan(db_session, incident.id)
    rec = persist_remediation_recommendation(db_session, plan)

    fresh = db_session.get(Recommendation, rec.id)
    assert fresh is not None
    assert fresh.recommendation_type == "remediation_plan"
    assert fresh.requires_approval is True
    assert fresh.incident_id == incident.id
    # The details blob must contain the readable summary AND the JSON block.
    assert "DRAFT remediation plan" in fresh.details
    assert "```json" in fresh.details


def test_missing_incident_raises(db_session: Session) -> None:
    with pytest.raises(IncidentNotFoundError):
        build_remediation_plan(db_session, uuid4())


# ---------- pick_template fallback ----------


def test_pick_template_uses_theme_fallback_when_incident_type_unknown() -> None:
    fn = pick_template("never_seen_before", ["routing_failure"])
    assert fn is TEMPLATES["bgp_neighbor_down"]

    fn = pick_template(None, ["policy_block"])
    assert fn is TEMPLATES["acl_blocking_traffic"]

    fn = pick_template(None, [])
    assert fn is template_default


# ---------- API ----------


def test_api_post_returns_plan_and_persists_recommendation(
    client: TestClient, db_session: Session
) -> None:
    incident = _seed(db_session, "bgp_neighbor_down")
    response = client.post(f"/api/remediation/incidents/{incident.id}/plan")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["requires_approval"] is True
    assert body["plan_type"] == "bgp_neighbor_recovery"

    persisted = db_session.scalar(
        select(func.count())
        .select_from(Recommendation)
        .where(Recommendation.incident_id == incident.id)
        .where(Recommendation.recommendation_type == "remediation_plan")
    )
    assert persisted == 1


def test_api_post_404_for_missing_incident(client: TestClient) -> None:
    response = client.post(f"/api/remediation/incidents/{uuid4()}/plan")
    assert response.status_code == 404


def test_api_get_lists_persisted_plans(
    client: TestClient, db_session: Session
) -> None:
    incident = _seed(db_session, "bgp_neighbor_down")
    # Two plans so we can verify "newest first" ordering trivially.
    client.post(f"/api/remediation/incidents/{incident.id}/plan")
    client.post(f"/api/remediation/incidents/{incident.id}/plan")

    response = client.get(
        f"/api/remediation/incidents/{incident.id}/plans?limit=10"
    )
    assert response.status_code == 200, response.text
    plans = response.json()
    assert len(plans) >= 2
    assert all(p["recommendation_type"] == "remediation_plan" for p in plans)
    assert all(p["requires_approval"] is True for p in plans)


def test_api_get_404_for_missing_incident(client: TestClient) -> None:
    response = client.get(f"/api/remediation/incidents/{uuid4()}/plans")
    assert response.status_code == 404


# ---------- approval workflow (Phase 10A, stub - no execution) ----------


def test_new_plan_defaults_to_pending_approval(db_session: Session) -> None:
    incident = _seed(db_session, "bgp_neighbor_down")
    plan = build_remediation_plan(db_session, incident.id)
    rec = persist_remediation_recommendation(db_session, plan)

    assert rec.approval_status == "pending"
    assert rec.approved_by is None
    assert rec.approved_at is None
    assert rec.approval_note is None


def _seed_operator_with_password(
    db_session: Session,
    *,
    display_name: str,
    role: str,
    password: str = "demo-password",
):
    """Insert an Operator row directly with a hashed password. Returns
    the row; caller can then POST /api/auth/login to obtain a token."""
    from app.auth.hashing import hash_password
    from app.db.models import Operator

    op = Operator(
        display_name=display_name,
        role=role,
        password_hash=hash_password(password),
    )
    db_session.add(op)
    db_session.flush()
    return op


def _login_as(client: TestClient, display_name: str, password: str = "demo-password") -> dict[str, str]:
    """Log in via the real /api/auth/login endpoint and return the
    bearer-token Authorization header dict."""
    resp = client.post(
        "/api/auth/login",
        json={"display_name": display_name, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _admin_headers(client: TestClient, db_session: Session, *, name: str = "admin-tester") -> dict[str, str]:
    """Convenience: create an admin operator with a password + return its
    bearer auth headers. Each call mints a fresh operator inside the
    test's savepoint, so re-use across tests in the same suite is fine."""
    _seed_operator_with_password(db_session, display_name=name, role="admin")
    return _login_as(client, name)


def test_api_approve_plan_records_intent(
    client: TestClient, db_session: Session
) -> None:
    """Phase 23 update: approve via authenticated admin. Audit fields
    come from the bearer-token session, not the request body."""
    headers = _admin_headers(client, db_session, name="alice")
    incident = _seed(db_session, "bgp_neighbor_down")
    plan_resp = client.post(f"/api/remediation/incidents/{incident.id}/plan")
    assert plan_resp.status_code == 201
    plans = client.get(
        f"/api/remediation/incidents/{incident.id}/plans"
    ).json()
    rec_id = plans[0]["id"]

    response = client.post(
        f"/api/remediation/recommendations/{rec_id}/approve",
        json={"note": "approved during change window"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approval_status"] == "approved"
    # Audit fields populated from the authenticated session.
    assert body["approved_by"] == "alice"
    assert body["approved_by_operator_id"] is not None
    assert body["approval_note"] == "approved during change window"
    assert body["approved_at"] is not None
    # requires_approval flag is unchanged - it's a contract, not a state.
    assert body["requires_approval"] is True


def test_api_reject_plan_records_intent(
    client: TestClient, db_session: Session
) -> None:
    headers = _admin_headers(client, db_session, name="bob")
    incident = _seed(db_session, "bgp_neighbor_down")
    client.post(f"/api/remediation/incidents/{incident.id}/plan")
    rec_id = client.get(
        f"/api/remediation/incidents/{incident.id}/plans"
    ).json()[0]["id"]

    response = client.post(
        f"/api/remediation/recommendations/{rec_id}/reject",
        json={"note": "wrong scope, see ticket NN-42"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approval_status"] == "rejected"
    assert body["approved_by"] == "bob"


def test_api_idempotent_reapprove_updates_metadata(
    client: TestClient, db_session: Session
) -> None:
    """Same auth re-approving keeps `approved_by` stable but updates
    the timestamp + note. Different authenticated admin overwrites
    `approved_by` to the new identity."""
    alice_headers = _admin_headers(client, db_session, name="alice")
    carol_headers = _admin_headers(client, db_session, name="carol")
    incident = _seed(db_session, "bgp_neighbor_down")
    client.post(f"/api/remediation/incidents/{incident.id}/plan")
    rec_id = client.get(
        f"/api/remediation/incidents/{incident.id}/plans"
    ).json()[0]["id"]

    client.post(
        f"/api/remediation/recommendations/{rec_id}/approve",
        json={"note": "first"},
        headers=alice_headers,
    )
    response = client.post(
        f"/api/remediation/recommendations/{rec_id}/approve",
        json={"note": "second"},
        headers=carol_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["approval_status"] == "approved"
    assert body["approved_by"] == "carol"
    assert body["approval_note"] == "second"


def test_api_approve_404_for_missing_recommendation(
    client: TestClient, db_session: Session
) -> None:
    headers = _admin_headers(client, db_session)
    response = client.post(
        f"/api/remediation/recommendations/{uuid4()}/approve",
        json={},
        headers=headers,
    )
    assert response.status_code == 404


def test_api_approve_400_for_non_remediation_recommendation(
    client: TestClient, db_session: Session
) -> None:
    headers = _admin_headers(client, db_session)
    incident = _seed(db_session, "bgp_neighbor_down")
    rec_resp = client.post(
        f"/api/incidents/{incident.id}/recommendations",
        json={
            "recommendation_type": "informational_note",
            "title": "just an FYI",
            "details": "operator should know this",
            "risk": "low",
        },
    )
    assert rec_resp.status_code == 201, rec_resp.text
    rec_id = rec_resp.json()["id"]

    response = client.post(
        f"/api/remediation/recommendations/{rec_id}/approve",
        json={},
        headers=headers,
    )
    assert response.status_code == 400
    assert "remediation_plan" in response.json()["detail"]


# ---------- Phase 23: auth + RBAC enforcement on approval/reject ----------


def test_api_approve_unauthenticated_returns_401(
    client: TestClient, db_session: Session
) -> None:
    """No `Authorization` header -> 401, regardless of body shape."""
    incident = _seed(db_session, "bgp_neighbor_down")
    client.post(f"/api/remediation/incidents/{incident.id}/plan")
    rec_id = client.get(
        f"/api/remediation/incidents/{incident.id}/plans"
    ).json()[0]["id"]

    response = client.post(
        f"/api/remediation/recommendations/{rec_id}/approve",
        json={"note": "trying to slip in"},
    )
    assert response.status_code == 401


def test_api_reject_unauthenticated_returns_401(
    client: TestClient, db_session: Session
) -> None:
    incident = _seed(db_session, "bgp_neighbor_down")
    client.post(f"/api/remediation/incidents/{incident.id}/plan")
    rec_id = client.get(
        f"/api/remediation/incidents/{incident.id}/plans"
    ).json()[0]["id"]

    response = client.post(
        f"/api/remediation/recommendations/{rec_id}/reject",
        json={"note": "trying to slip in"},
    )
    assert response.status_code == 401


def test_api_approve_with_non_admin_role_returns_403(
    client: TestClient, db_session: Session
) -> None:
    """Authenticated as role=operator (not admin) -> 403, NOT 401."""
    _seed_operator_with_password(
        db_session, display_name="non-admin", role="operator"
    )
    headers = _login_as(client, "non-admin")
    incident = _seed(db_session, "bgp_neighbor_down")
    client.post(f"/api/remediation/incidents/{incident.id}/plan")
    rec_id = client.get(
        f"/api/remediation/incidents/{incident.id}/plans"
    ).json()[0]["id"]

    response = client.post(
        f"/api/remediation/recommendations/{rec_id}/approve",
        json={"note": "by an operator role"},
        headers=headers,
    )
    assert response.status_code == 403
    assert "admin" in response.json()["detail"]


def test_api_reject_with_non_admin_role_returns_403(
    client: TestClient, db_session: Session
) -> None:
    _seed_operator_with_password(
        db_session, display_name="non-admin-rej", role="operator"
    )
    headers = _login_as(client, "non-admin-rej")
    incident = _seed(db_session, "bgp_neighbor_down")
    client.post(f"/api/remediation/incidents/{incident.id}/plan")
    rec_id = client.get(
        f"/api/remediation/incidents/{incident.id}/plans"
    ).json()[0]["id"]

    response = client.post(
        f"/api/remediation/recommendations/{rec_id}/reject",
        json={},
        headers=headers,
    )
    assert response.status_code == 403


def test_api_approve_body_with_legacy_identity_fields_returns_422(
    client: TestClient, db_session: Session
) -> None:
    """Phase 23 hardening: stale callers sending `operator_name` or
    `operator_id` in the body must be rejected by Pydantic's
    extra='forbid' on `ApprovalRequest`. The approving identity comes
    from the authenticated session, period."""
    headers = _admin_headers(client, db_session)
    incident = _seed(db_session, "bgp_neighbor_down")
    client.post(f"/api/remediation/incidents/{incident.id}/plan")
    rec_id = client.get(
        f"/api/remediation/incidents/{incident.id}/plans"
    ).json()[0]["id"]

    legacy_name_resp = client.post(
        f"/api/remediation/recommendations/{rec_id}/approve",
        json={"operator_name": "legacy-cli", "note": "from a stale script"},
        headers=headers,
    )
    assert legacy_name_resp.status_code == 422

    legacy_id_resp = client.post(
        f"/api/remediation/recommendations/{rec_id}/approve",
        json={"operator_id": str(uuid4()), "note": "from a stale script"},
        headers=headers,
    )
    assert legacy_id_resp.status_code == 422

    # No mutation occurred — row stays pending.
    db_session.expire_all()
    rec = db_session.get(Recommendation, UUID(rec_id))
    assert rec is not None
    assert rec.approval_status == "pending"
    assert rec.approved_by is None


def test_api_approve_audit_fields_come_from_auth_not_body(
    client: TestClient, db_session: Session
) -> None:
    """Even a perfectly-shaped body cannot override the authenticated
    operator. Phase 13A's legacy free-form `operator_name` path is
    closed: the row's audit fields reflect the bearer-token identity."""
    headers = _admin_headers(client, db_session, name="real-admin")
    incident = _seed(db_session, "bgp_neighbor_down")
    client.post(f"/api/remediation/incidents/{incident.id}/plan")
    rec_id = client.get(
        f"/api/remediation/incidents/{incident.id}/plans"
    ).json()[0]["id"]

    response = client.post(
        f"/api/remediation/recommendations/{rec_id}/approve",
        json={"note": "happy path"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["approved_by"] == "real-admin"
    assert response.json()["approved_by_operator_id"] is not None


# ---------- CLI ----------


def test_cli_prints_plan_as_json_and_persists_by_default(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = _seed(db_session, "bgp_neighbor_down")

    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(planner_module, "SessionLocal", fake_session_local)

    exit_code = planner_module.main(["--incident-id", str(incident.id)])
    assert exit_code == 0

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["requires_approval"] is True
    assert parsed["plan_type"] == "bgp_neighbor_recovery"

    persisted = db_session.scalar(
        select(func.count())
        .select_from(Recommendation)
        .where(Recommendation.recommendation_type == "remediation_plan")
        .where(Recommendation.incident_id == incident.id)
    )
    assert persisted == 1  # default --persist


def test_cli_no_persist_skips_recommendation_row(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = _seed(db_session, "bgp_neighbor_down")

    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(planner_module, "SessionLocal", fake_session_local)

    exit_code = planner_module.main(
        ["--incident-id", str(incident.id), "--no-persist"]
    )
    assert exit_code == 0

    persisted = db_session.scalar(
        select(func.count())
        .select_from(Recommendation)
        .where(Recommendation.recommendation_type == "remediation_plan")
        .where(Recommendation.incident_id == incident.id)
    )
    assert persisted == 0


# ---------- safety: app/remediation must not import an execution library ----------


# Kept ONLY in the test file by design - the production code under
# app/remediation must not contain these strings.
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


def _scan_files_for_execution_imports(
    files: list[Path],
) -> list[str]:
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


def test_remediation_package_blocks_execution_library_imports() -> None:
    """The remediation planner + its HTTP wrapper are plan-only. This test
    parses every .py file under app/remediation AND the api/remediation.py
    wrapper with the Python AST and fails the build if any real import
    statement pulls in a remote-execution library.

    Strings inside docstrings or comments are ignored on purpose - only
    actual `Import` / `ImportFrom` nodes count."""
    app_root = Path(__file__).resolve().parents[1] / "app"
    files: list[Path] = list((app_root / "remediation").rglob("*.py"))
    files.append(app_root / "api" / "remediation.py")
    assert files, "no remediation files found"

    offenders = _scan_files_for_execution_imports(files)
    assert not offenders, (
        "Remediation code must never import a remote-execution library:\n  "
        + "\n  ".join(offenders)
    )
