from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Device,
    Incident,
    IncidentEvent,
    IncidentEvidence,
    Recommendation,
)
from app.simulator.scenarios import (
    ALL_SCENARIO_NAMES,
    DEVICE_SPECS,
    SCENARIOS,
    SIMULATOR_MARKER,
)
from app.simulator.seed import (
    apply_scenario,
    ensure_devices,
    reset_simulator_data,
)


def _count_sim_incidents(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Incident)
            .where(Incident.summary.like(f"{SIMULATOR_MARKER}%"))
        )
        or 0
    )


def test_ensure_devices_is_idempotent(db_session: Session) -> None:
    # First call may insert all 4 or 0 (if the dev DB already has them from a prior CLI run).
    ensure_devices(db_session)
    # Second call MUST report 0 new — that is the contract.
    second_call = ensure_devices(db_session)
    assert second_call == 0, "ensure_devices should be a no-op on the second call"

    # All four simulator hostnames must now exist.
    for spec in DEVICE_SPECS:
        device = db_session.scalar(
            select(Device).where(Device.hostname == spec["hostname"])
        )
        assert device is not None, f"device {spec['hostname']} should exist after seed"
        assert device.role == spec["role"]
        assert device.management_ip == spec["management_ip"]


def test_one_scenario_creates_exactly_one_incident(db_session: Session) -> None:
    before = _count_sim_incidents(db_session)
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    after = _count_sim_incidents(db_session)

    assert incident.id is not None
    assert incident.severity == "critical"
    assert incident.incident_type == "bgp_neighbor_down"
    assert after - before == 1


def test_all_scenarios_create_at_least_five_incidents(db_session: Session) -> None:
    before = _count_sim_incidents(db_session)
    for name in ALL_SCENARIO_NAMES:
        apply_scenario(db_session, name)
    after = _count_sim_incidents(db_session)

    assert len(ALL_SCENARIO_NAMES) >= 5, "we should ship at least 5 scenarios"
    assert after - before == len(ALL_SCENARIO_NAMES)


def test_scenario_includes_events_evidence_and_recommendation(
    db_session: Session,
) -> None:
    incident = apply_scenario(db_session, "bgp_neighbor_down")

    events = db_session.scalars(
        select(IncidentEvent).where(IncidentEvent.incident_id == incident.id)
    ).all()
    evidence = db_session.scalars(
        select(IncidentEvidence).where(IncidentEvidence.incident_id == incident.id)
    ).all()
    recs = db_session.scalars(
        select(Recommendation).where(Recommendation.incident_id == incident.id)
    ).all()

    assert len(events) >= 1, "scenario should produce at least one event"
    assert len(evidence) >= 1, "scenario should produce at least one evidence row"
    assert len(recs) == 1, "scenario should produce exactly one recommendation"

    # Sanity-check the JSONB payload marker survives the round-trip.
    assert all(ev.payload and ev.payload.get("_origin") == "simulator" for ev in events)
    assert all(
        ev.payload and ev.payload.get("_origin") == "simulator" for ev in evidence
    )


def test_reset_removes_simulator_incidents_only(db_session: Session) -> None:
    # 1. A real (operator-created) incident — its summary has NO simulator marker.
    manual = Incident(
        title="real operator-created incident",
        severity="low",
        incident_type="manual_test",
        summary="operator wrote this; reset must not touch it",
    )
    db_session.add(manual)
    db_session.commit()
    manual_id = manual.id

    # 2. Seed every simulator scenario.
    for name in ALL_SCENARIO_NAMES:
        apply_scenario(db_session, name)

    assert _count_sim_incidents(db_session) >= len(ALL_SCENARIO_NAMES)

    # 3. Reset.
    removed = reset_simulator_data(db_session)
    assert removed >= len(ALL_SCENARIO_NAMES)

    # 4. Every simulator incident is gone.
    assert _count_sim_incidents(db_session) == 0

    # 5. The operator-created incident must still be present.
    assert db_session.get(Incident, manual_id) is not None, (
        "reset must not delete operator-created incidents"
    )


def test_reset_preserves_devices(db_session: Session) -> None:
    ensure_devices(db_session)
    apply_scenario(db_session, "interface_errors_spike")

    reset_simulator_data(db_session)

    for spec in DEVICE_SPECS:
        device = db_session.scalar(
            select(Device).where(Device.hostname == spec["hostname"])
        )
        assert device is not None, f"reset must preserve device {spec['hostname']}"


def test_apply_scenario_rejects_unknown_name(db_session: Session) -> None:
    import pytest

    with pytest.raises(KeyError):
        apply_scenario(db_session, "no_such_scenario_xyz")


def test_scenarios_cover_required_scenarios() -> None:
    """Guardrail: the five scenarios named in the Phase 3 spec must be present."""
    required = {
        "bgp_neighbor_down",
        "interface_errors_spike",
        "latency_spike",
        "route_missing",
        "acl_blocking_traffic",
    }
    assert required.issubset(SCENARIOS.keys()), (
        f"missing required scenarios: {required - SCENARIOS.keys()}"
    )
