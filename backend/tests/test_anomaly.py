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


# ============================================================
# Phase 21C - lab-event mappings into existing rule outputs
# ============================================================


from app.db.models import Incident, IncidentEvent  # noqa: E402


def _lab_incident(db: Session) -> Incident:
    """Bare lab_full_snapshot Incident; tests attach events directly."""
    incident = Incident(
        title="Phase 21C test - lab snapshot",
        severity="medium",
        incident_type="lab_full_snapshot",
        summary="[lab-collector] test fixture",
    )
    db.add(incident)
    db.flush()
    return incident


def _attach(
    db: Session,
    incident: Incident,
    *,
    event_type: str,
    payload: dict,
    source: str = "lab:edge-1",
    message: str = "test event",
) -> IncidentEvent:
    e = IncidentEvent(
        incident_id=incident.id,
        event_type=event_type,
        source=source,
        message=message,
        payload=payload,
    )
    db.add(e)
    db.flush()
    return e


def test_lab_bgp_not_established_produces_bgp_finding(
    db_session: Session,
) -> None:
    """Phase 21C: lab_bgp_peer_not_established events flow through the
    existing rule_bgp_neighbor_down and produce bgp_neighbor_down_detected
    findings - same rule_id, same downstream theme/template mapping."""
    incident = _lab_incident(db_session)
    _attach(
        db_session,
        incident,
        event_type="lab_bgp_peer_not_established",
        payload={
            "router": "edge-1",
            "peer": "172.30.1.2",
            "peer_as": 65000,
            "state": "Active",
            "_origin": "lab-collector",
        },
    )
    findings = analyze_incident(db_session, incident.id)
    bgp = [f for f in findings if f.rule_name == "bgp_neighbor_down_detected"]
    assert len(bgp) == 1
    assert bgp[0].rule_id == "R001"
    assert "edge-1" in bgp[0].summary
    assert "172.30.1.2" in bgp[0].summary  # peer surfaced via `peer` payload key


def test_lab_interface_has_errors_produces_interface_error_finding(
    db_session: Session,
) -> None:
    """Phase 21C: lab_interface_status with payload.has_errors=True flows
    through the existing rule_interface_error_spike. Same rule_id, same
    downstream theme/template mapping."""
    incident = _lab_incident(db_session)
    _attach(
        db_session,
        incident,
        event_type="lab_interface_status",
        payload={
            "router": "edge-1",
            "interface": "Gi0/1",
            "admin_status": "up",
            "oper_status": "up",
            "line_protocol": "is up",
            "input_errors": 42,
            "output_errors": 0,
            "down": False,
            "has_errors": True,
            "_origin": "lab-collector",
        },
    )
    findings = analyze_incident(db_session, incident.id)
    iface = [
        f for f in findings if f.rule_name == "interface_error_spike_detected"
    ]
    assert len(iface) == 1
    assert iface[0].rule_id == "R003"
    assert "Gi0/1" in iface[0].summary
    assert "edge-1" in iface[0].summary
    # Lab-shape summary mentions input/output counters, not the per-min rate.
    assert "input=42" in iface[0].summary


def test_lab_interface_down_produces_link_down_finding(
    db_session: Session,
) -> None:
    """Phase 21C: lab_interface_status with payload.down=True produces a
    new link_down_detected finding via rule_link_down (R008). Routes to
    the interface_physical_issue theme so Phase 7 selects the existing
    interface template - not generic_investigation."""
    incident = _lab_incident(db_session)
    _attach(
        db_session,
        incident,
        event_type="lab_interface_status",
        payload={
            "router": "edge-1",
            "interface": "Gi0/2",
            "admin_status": "up",
            "oper_status": "down",
            "line_protocol": "is down",
            "input_errors": 0,
            "output_errors": 0,
            "down": True,
            "has_errors": False,
            "_origin": "lab-collector",
        },
    )
    findings = analyze_incident(db_session, incident.id)
    link = [f for f in findings if f.rule_name == "link_down_detected"]
    assert len(link) == 1
    assert link[0].rule_id == "R008"
    assert "Gi0/2" in link[0].summary
    assert "down" in link[0].summary.lower()


def test_healthy_lab_interface_event_produces_no_finding(
    db_session: Session,
) -> None:
    """Negative pin: an `up` lab interface with zero errors must NOT trip
    any rule. Otherwise the umbrella snapshot would spam findings for
    every healthy interface in the lab."""
    incident = _lab_incident(db_session)
    _attach(
        db_session,
        incident,
        event_type="lab_interface_status",
        payload={
            "router": "edge-1",
            "interface": "Gi0/3",
            "admin_status": "up",
            "oper_status": "up",
            "line_protocol": "is up",
            "input_errors": 0,
            "output_errors": 0,
            "down": False,
            "has_errors": False,
            "_origin": "lab-collector",
        },
    )
    findings = analyze_incident(db_session, incident.id)
    assert findings == []


def test_simulator_bgp_summary_text_preserved_after_phase21c() -> None:
    """Belt-and-suspenders: the existing simulator BGP path keeps its
    original `'transitioned to Idle'` summary text BYTE-FOR-BYTE. Phase
    21C added a lab-shape summary for `lab_bgp_peer_not_established`
    events but must NOT have changed wording for `bgp_state_change`
    events - existing RCA output, screenshots, and downstream consumers
    expect the original phrasing."""
    from app.anomaly.rules import rule_bgp_neighbor_down
    from uuid import uuid4 as _uuid4

    class _Incident:  # minimal duck-typed stand-in
        id = _uuid4()
        incident_type = "bgp_neighbor_down"

    class _Event:
        id = _uuid4()
        event_type = "bgp_state_change"
        payload = {"device": "core-1", "neighbor": "10.0.0.21", "after": "Idle"}

    findings = rule_bgp_neighbor_down(_Incident(), [_Event()], [])  # type: ignore[arg-type]
    assert len(findings) == 1
    summary = findings[0].summary
    # The exact pre-Phase-21C wording must still be there. If a future
    # refactor reworded this, downstream RCA / snapshots / docs would
    # silently drift; this assertion makes that fail loudly.
    assert "transitioned to Idle" in summary
    assert "core-1" in summary
    assert "10.0.0.21" in summary  # `neighbor` payload key
    # And the new lab-only phrasing must NOT have leaked into the
    # simulator path.
    assert "is not Established" not in summary


def test_lab_bgp_summary_uses_lab_specific_phrasing() -> None:
    """Companion pin: the lab event path uses `is not Established`
    (because lab state may be Active/Connect/etc., not only Idle).
    Splits cleanly from the simulator path so future regressions in
    either direction fail loudly."""
    from app.anomaly.rules import rule_bgp_neighbor_down
    from uuid import uuid4 as _uuid4

    class _Incident:
        id = _uuid4()
        incident_type = "lab_full_snapshot"

    class _LabEvent:
        id = _uuid4()
        event_type = "lab_bgp_peer_not_established"
        payload = {
            "router": "edge-1",
            "peer": "172.30.1.2",
            "state": "Active",  # not Idle - lab can land here
            "_origin": "lab-collector",
        }

    findings = rule_bgp_neighbor_down(_Incident(), [_LabEvent()], [])  # type: ignore[arg-type]
    assert len(findings) == 1
    summary = findings[0].summary
    assert "is not Established" in summary
    # And the simulator phrasing must NOT have leaked into the lab path.
    assert "transitioned to Idle" not in summary
