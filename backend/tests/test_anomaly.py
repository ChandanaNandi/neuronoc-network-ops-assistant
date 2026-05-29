"""Phase 4 anomaly engine tests.

Uses the existing real-Postgres test fixtures. Scenarios are produced via the
Phase 3 simulator so a single source of truth (the scenario dicts) drives both
the seeded data and the rule expectations.
"""

import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.anomaly import engine as engine_module
from app.anomaly.engine import (
    IncidentNotFoundError,
    analyze_incident,
    analyze_open_incidents,
)
from app.anomaly.rules import AnomalyFinding
from app.simulator.scenarios import ALL_SCENARIO_NAMES
from app.simulator.seed import apply_scenario


def _rule_names(findings: list[AnomalyFinding]) -> set[str]:
    return {f.rule_name for f in findings}


# ---------- rule trigger tests, one per scenario ----------


def test_bgp_scenario_triggers_bgp_down_rule(db_session: Session) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    names = _rule_names(analyze_incident(db_session, incident.id))
    assert "bgp_neighbor_down_detected" in names


def test_route_missing_scenario_triggers_route_missing_rule(
    db_session: Session,
) -> None:
    incident = apply_scenario(db_session, "route_missing")
    names = _rule_names(analyze_incident(db_session, incident.id))
    assert "route_missing_detected" in names


def test_interface_errors_scenario_triggers_interface_error_spike_rule(
    db_session: Session,
) -> None:
    incident = apply_scenario(db_session, "interface_errors_spike")
    names = _rule_names(analyze_incident(db_session, incident.id))
    assert "interface_error_spike_detected" in names


def test_latency_scenario_triggers_latency_and_packet_loss(
    db_session: Session,
) -> None:
    incident = apply_scenario(db_session, "latency_spike")
    names = _rule_names(analyze_incident(db_session, incident.id))
    assert "latency_spike_detected" in names
    assert "packet_loss_detected" in names


def test_acl_scenario_triggers_acl_deny_spike(db_session: Session) -> None:
    incident = apply_scenario(db_session, "acl_blocking_traffic")
    names = _rule_names(analyze_incident(db_session, incident.id))
    assert "acl_deny_spike_detected" in names


# ---------- engine surface ----------


def test_missing_incident_raises_incident_not_found(db_session: Session) -> None:
    with pytest.raises(IncidentNotFoundError):
        analyze_incident(db_session, uuid4())


def test_findings_carry_required_fields(db_session: Session) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    findings = analyze_incident(db_session, incident.id)
    assert findings, "expected at least one finding"
    for f in findings:
        assert f.rule_id
        assert f.rule_name
        assert f.severity in {"informational", "low", "medium", "high", "critical"}
        assert 0.0 <= f.confidence <= 1.0
        assert f.incident_id == incident.id
        assert f.summary
        assert f.recommended_next_step


def test_analyze_open_incidents_returns_findings_for_seeded(
    db_session: Session,
) -> None:
    for name in ALL_SCENARIO_NAMES:
        apply_scenario(db_session, name)
    findings = analyze_open_incidents(db_session, limit=50)
    assert len(findings) >= len(ALL_SCENARIO_NAMES), (
        "expected at least one finding per seeded open incident"
    )


# ---------- HTTP API ----------


def test_api_returns_404_for_missing_incident(client: TestClient) -> None:
    response = client.get(f"/api/anomalies/incidents/{uuid4()}")
    assert response.status_code == 404


def test_api_findings_for_specific_incident(
    client: TestClient, db_session: Session
) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    response = client.get(f"/api/anomalies/incidents/{incident.id}")
    assert response.status_code == 200, response.text
    findings = response.json()
    assert any(f["rule_name"] == "bgp_neighbor_down_detected" for f in findings)


def test_api_open_returns_findings_for_seeded(
    client: TestClient, db_session: Session
) -> None:
    for name in ALL_SCENARIO_NAMES:
        apply_scenario(db_session, name)
    response = client.get("/api/anomalies/open?limit=50")
    assert response.status_code == 200, response.text
    findings = response.json()
    assert len(findings) >= len(ALL_SCENARIO_NAMES)
    assert all("rule_name" in f for f in findings)


def test_api_open_rejects_oversized_limit(client: TestClient) -> None:
    response = client.get("/api/anomalies/open?limit=500")
    assert response.status_code == 422


# ---------- CLI (JSON output) ----------


def test_cli_rejects_no_mode(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        engine_module.main([])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    # argparse stock message for a required mutex group
    assert "one of the arguments" in err and "--incident-id" in err and "--open" in err


def test_cli_rejects_both_modes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        engine_module.main(
            ["--incident-id", "00000000-0000-0000-0000-000000000000", "--open"]
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "not allowed with argument" in err


def test_cli_prints_findings_as_json(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")

    # The CLI normally creates its own SessionLocal-bound Session.
    # Replace that with a no-op context manager around our test session so the
    # CLI sees the seeded-but-uncommitted data inside this test transaction.
    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(engine_module, "SessionLocal", fake_session_local)

    exit_code = engine_module.main(["--incident-id", str(incident.id)])
    assert exit_code == 0

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert isinstance(parsed, list)
    assert any(f["rule_name"] == "bgp_neighbor_down_detected" for f in parsed)
