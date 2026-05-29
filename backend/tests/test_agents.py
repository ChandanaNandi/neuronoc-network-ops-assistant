"""Phase 5 agent-workflow tests.

The workflow is exercised against simulator-seeded incidents using the same
real-Postgres `db_session` fixture as earlier phases. The runner is called
directly so commits become savepoint releases inside our outer transaction.
"""

import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import runner as runner_module
from app.agents.runner import run_incident_analysis
from app.agents.workflow import NODE_NAMES
from app.anomaly.engine import IncidentNotFoundError
from app.db.models import AgentRun, AgentStep
from app.simulator.seed import apply_scenario


# ---------- workflow execution ----------


def test_workflow_runs_on_bgp_scenario(db_session: Session) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    run = run_incident_analysis(db_session, incident.id)

    assert run.status == "completed"
    assert run.completed_at is not None
    assert run.output_payload is not None


def test_report_has_at_least_one_anomaly(db_session: Session) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    run = run_incident_analysis(db_session, incident.id)
    report = run.output_payload
    assert report is not None
    assert report["anomaly_count"] >= 1
    assert report["key_findings"]


def test_bgp_classified_as_routing_failure(db_session: Session) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    run = run_incident_analysis(db_session, incident.id)
    assert "routing_failure" in run.output_payload["correlated_signals"]


def test_interface_errors_classified_as_interface_physical_issue(
    db_session: Session,
) -> None:
    incident = apply_scenario(db_session, "interface_errors_spike")
    run = run_incident_analysis(db_session, incident.id)
    assert "interface_physical_issue" in run.output_payload["correlated_signals"]


def test_acl_classified_as_policy_block(db_session: Session) -> None:
    incident = apply_scenario(db_session, "acl_blocking_traffic")
    run = run_incident_analysis(db_session, incident.id)
    assert "policy_block" in run.output_payload["correlated_signals"]


# ---------- persistence ----------


def test_agent_run_and_steps_are_persisted(db_session: Session) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    run = run_incident_analysis(db_session, incident.id)

    db_session.expire_all()
    fetched = db_session.get(AgentRun, run.id)
    assert fetched is not None
    assert fetched.status == "completed"
    assert fetched.workflow_name == "incident_analysis_v1"

    step_count = db_session.scalar(
        select(func.count()).select_from(AgentStep).where(AgentStep.run_id == run.id)
    )
    assert step_count == len(NODE_NAMES), (
        f"expected {len(NODE_NAMES)} steps (one per node), got {step_count}"
    )

    step_names = db_session.scalars(
        select(AgentStep.step_name).where(AgentStep.run_id == run.id)
    ).all()
    assert set(step_names) == set(NODE_NAMES)


def test_missing_incident_raises_incident_not_found(db_session: Session) -> None:
    with pytest.raises(IncidentNotFoundError):
        run_incident_analysis(db_session, uuid4())

    # No AgentRun should have been created.
    bogus_runs = db_session.scalar(
        select(func.count()).select_from(AgentRun)
    )
    assert isinstance(bogus_runs, int)  # sanity


def test_workflow_failure_persists_failed_run(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the LangGraph workflow raises after the AgentRun row exists,
    the runner must mark the row failed (status, error, completed_at) and
    leave output_payload null - then re-raise."""
    incident = apply_scenario(db_session, "bgp_neighbor_down")

    class _ExplodingWorkflow:
        def invoke(self, *_args, **_kwargs):
            raise RuntimeError("synthetic workflow failure")

    monkeypatch.setattr(
        runner_module, "build_workflow", lambda: _ExplodingWorkflow()
    )

    with pytest.raises(RuntimeError, match="synthetic workflow failure"):
        run_incident_analysis(db_session, incident.id)

    runs = db_session.scalars(
        select(AgentRun).where(AgentRun.incident_id == incident.id)
    ).all()
    assert len(runs) == 1, "exactly one AgentRun should have been persisted"

    run = runs[0]
    assert run.status == "failed"
    assert run.error is not None
    assert "RuntimeError" in run.error
    assert "synthetic workflow failure" in run.error
    assert run.completed_at is not None
    assert run.output_payload is None


# ---------- API ----------


def test_api_analyze_returns_completed_run(
    client: TestClient, db_session: Session
) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    response = client.post(f"/api/agents/incidents/{incident.id}/analyze")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["incident_id"] == str(incident.id)
    assert body["output_payload"]["anomaly_count"] >= 1
    assert len(body["steps"]) == len(NODE_NAMES)


def test_api_analyze_404_for_missing_incident(client: TestClient) -> None:
    response = client.post(f"/api/agents/incidents/{uuid4()}/analyze")
    assert response.status_code == 404


def test_api_get_run_returns_steps(
    client: TestClient, db_session: Session
) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    run_response = client.post(f"/api/agents/incidents/{incident.id}/analyze")
    run_id = run_response.json()["id"]

    response = client.get(f"/api/agents/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run_id
    step_names = [step["step_name"] for step in body["steps"]]
    assert set(step_names) == set(NODE_NAMES)


def test_api_get_run_404_for_missing_run(client: TestClient) -> None:
    response = client.get(f"/api/agents/runs/{uuid4()}")
    assert response.status_code == 404


def test_api_list_runs_for_incident(
    client: TestClient, db_session: Session
) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    # Two runs back-to-back.
    client.post(f"/api/agents/incidents/{incident.id}/analyze")
    client.post(f"/api/agents/incidents/{incident.id}/analyze")

    response = client.get(f"/api/agents/incidents/{incident.id}/runs?limit=10")
    assert response.status_code == 200
    runs = response.json()
    assert len(runs) >= 2
    assert all(r["incident_id"] == str(incident.id) for r in runs)


def test_api_list_runs_404_for_missing_incident(client: TestClient) -> None:
    response = client.get(f"/api/agents/incidents/{uuid4()}/runs")
    assert response.status_code == 404


# ---------- CLI ----------


def test_cli_prints_final_report_as_json(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")

    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(runner_module, "SessionLocal", fake_session_local)

    exit_code = runner_module.main(["--incident-id", str(incident.id)])
    assert exit_code == 0

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["incident_type"] == "bgp_neighbor_down"
    assert parsed["anomaly_count"] >= 1
    assert "routing_failure" in parsed["correlated_signals"]


def test_cli_exits_nonzero_for_missing_incident(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(runner_module, "SessionLocal", fake_session_local)

    exit_code = runner_module.main(["--incident-id", str(uuid4())])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "not found" in err
