"""Phase 8C lab collector tests.

ALL tests mock the docker/vtysh runner - no live FRR lab is required.
"""

import json
from collections.abc import Callable
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Incident, IncidentEvent
from app.lab import collector as collector_module
from app.lab.collector import (
    LAB_INCIDENT_TYPE,
    LAB_MARKER,
    LAB_ROUTERS,
    collect_lab_bgp_snapshot,
)


# ---------- fake-runner helpers ----------


def _summary_for(router: str, *, peers: dict) -> dict:
    """Build the FRR JSON shape `show ip bgp summary json` would emit."""
    return {
        "ipv4Unicast": {
            "routerId": {
                "edge-1": "10.0.0.11",
                "edge-2": "10.0.0.12",
                "core-1": "10.0.0.21",
                "branch-1": "10.0.0.31",
            }.get(router, "0.0.0.0"),
            "as": {
                "edge-1": 65011,
                "edge-2": 65012,
                "core-1": 65000,
                "branch-1": 65031,
            }.get(router, 0),
            "peers": peers,
        }
    }


def _peer(state: str, *, remote_as: int, pfx_rcd: int = 0, pfx_snt: int = 0) -> dict:
    return {
        "state": state,
        "remoteAs": remote_as,
        "pfxRcd": pfx_rcd,
        "pfxSnt": pfx_snt,
    }


def _router_from_cmd(cmd: list[str]) -> str:
    """`docker exec neuronoc-lab-<router> vtysh -c "show ..."` -> <router>."""
    container = cmd[2]  # docker, exec, <container>, vtysh, -c, ...
    return container.removeprefix("neuronoc-lab-")


def _make_runner(per_router: dict[str, dict | None], *, fail: set[str] | None = None) -> Callable:
    """Build a CommandRunner that returns canned JSON per router and simulates
    failure for any router in `fail`."""
    failed = fail or set()

    def _runner(cmd: list[str]) -> tuple[str, str, int]:
        router = _router_from_cmd(cmd)
        if router in failed:
            return "", "Error response from daemon: container not running", 1
        body = per_router.get(router)
        if body is None:
            return "this is not JSON", "", 0  # exercise the parse-error branch
        return json.dumps(body), "", 0

    return _runner


# ---------- happy path: all four healthy ----------


def _all_healthy_runner() -> Callable:
    return _make_runner(
        {
            "edge-1": _summary_for(
                "edge-1",
                peers={"172.30.1.2": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=4)},
            ),
            "edge-2": _summary_for(
                "edge-2",
                peers={"172.30.2.2": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=4)},
            ),
            "core-1": _summary_for(
                "core-1",
                peers={
                    "172.30.1.1": _peer("Established", remote_as=65011, pfx_rcd=1, pfx_snt=4),
                    "172.30.2.1": _peer("Established", remote_as=65012, pfx_rcd=1, pfx_snt=4),
                    "172.30.3.1": _peer("Established", remote_as=65031, pfx_rcd=1, pfx_snt=4),
                },
            ),
            "branch-1": _summary_for(
                "branch-1",
                peers={"172.30.3.2": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=4)},
            ),
        }
    )


def test_all_healthy_creates_established_events(db_session: Session) -> None:
    summary = collect_lab_bgp_snapshot(db_session, runner=_all_healthy_runner())

    assert summary.routers_seen == 4
    assert summary.peers_seen == 6  # 1 + 1 + 3 + 1
    assert summary.established_count == 6
    assert summary.non_established_count == 0
    assert summary.errors == []
    # 6 peer events + 4 per-router snapshot events
    assert summary.events_created == 10

    incident = db_session.get(Incident, summary.incident_id)
    assert incident is not None
    assert incident.incident_type == LAB_INCIDENT_TYPE
    assert incident.summary.startswith(LAB_MARKER)
    assert incident.severity == "low"  # all healthy

    types = db_session.scalars(
        select(IncidentEvent.event_type).where(
            IncidentEvent.incident_id == summary.incident_id
        )
    ).all()
    assert types.count("lab_bgp_peer_established") == 6
    assert types.count("lab_bgp_prefix_snapshot") == 4
    assert "lab_bgp_peer_not_established" not in types
    assert "lab_bgp_collection_error" not in types


def test_peer_payload_carries_required_fields(db_session: Session) -> None:
    summary = collect_lab_bgp_snapshot(db_session, runner=_all_healthy_runner())
    events = db_session.scalars(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == summary.incident_id)
        .where(IncidentEvent.event_type == "lab_bgp_peer_established")
    ).all()
    assert events
    sample = events[0].payload
    for required in (
        "router",
        "peer",
        "peer_as",
        "state",
        "prefixes_received",
        "prefixes_sent",
    ):
        assert required in sample, f"payload missing {required}: {sample}"
    assert sample["_origin"] == "lab-collector"


# ---------- a not-Established peer ----------


def test_non_established_peer_creates_dedicated_event(db_session: Session) -> None:
    runner = _make_runner(
        {
            "edge-1": _summary_for(
                "edge-1",
                peers={"172.30.1.2": _peer("Active", remote_as=65000)},
            ),
            "edge-2": _summary_for(
                "edge-2",
                peers={"172.30.2.2": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=4)},
            ),
            "core-1": _summary_for(
                "core-1",
                peers={
                    "172.30.1.1": _peer("Idle", remote_as=65011),
                    "172.30.2.1": _peer("Established", remote_as=65012, pfx_rcd=1, pfx_snt=4),
                    "172.30.3.1": _peer("Established", remote_as=65031, pfx_rcd=1, pfx_snt=4),
                },
            ),
            "branch-1": _summary_for(
                "branch-1",
                peers={"172.30.3.2": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=4)},
            ),
        }
    )

    summary = collect_lab_bgp_snapshot(db_session, runner=runner)
    assert summary.non_established_count == 2
    assert summary.established_count == 4
    assert summary.errors == []

    incident = db_session.get(Incident, summary.incident_id)
    assert incident.severity == "medium"

    not_est_events = db_session.scalars(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == summary.incident_id)
        .where(IncidentEvent.event_type == "lab_bgp_peer_not_established")
    ).all()
    assert len(not_est_events) == 2
    states = {ev.payload["state"] for ev in not_est_events}
    assert states == {"Active", "Idle"}


# ---------- unreachable router ----------


def test_unreachable_router_records_collection_error(db_session: Session) -> None:
    runner = _make_runner(
        {
            "edge-1": _summary_for(
                "edge-1",
                peers={"172.30.1.2": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=4)},
            ),
            "edge-2": _summary_for(
                "edge-2",
                peers={"172.30.2.2": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=4)},
            ),
            "core-1": _summary_for(
                "core-1",
                peers={
                    "172.30.1.1": _peer("Established", remote_as=65011, pfx_rcd=1, pfx_snt=4),
                    "172.30.2.1": _peer("Established", remote_as=65012, pfx_rcd=1, pfx_snt=4),
                    "172.30.3.1": _peer("Established", remote_as=65031, pfx_rcd=1, pfx_snt=4),
                },
            ),
            # branch-1 absent in the dict; the runner's fail=set takes care of it.
        },
        fail={"branch-1"},
    )

    summary = collect_lab_bgp_snapshot(db_session, runner=runner)
    assert summary.routers_seen == 3
    assert summary.peers_seen == 5  # 1 + 1 + 3
    assert summary.established_count == 5
    assert summary.non_established_count == 0
    assert summary.errors and "branch-1" in summary.errors[0]

    incident = db_session.get(Incident, summary.incident_id)
    assert incident.severity == "high"  # any error -> high

    err_events = db_session.scalars(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == summary.incident_id)
        .where(IncidentEvent.event_type == "lab_bgp_collection_error")
    ).all()
    assert len(err_events) == 1
    assert err_events[0].payload["router"] == "branch-1"


# ---------- malformed JSON ----------


def test_malformed_json_is_recorded_as_collection_error(db_session: Session) -> None:
    # Routers that map to None in the per_router dict cause the runner to
    # return non-JSON garbage; we expect each to surface as a collection error.
    runner = _make_runner({r: None for r in LAB_ROUTERS})
    summary = collect_lab_bgp_snapshot(db_session, runner=runner)

    assert summary.routers_seen == 0
    assert summary.peers_seen == 0
    assert len(summary.errors) == 4
    assert all("non-JSON" in e for e in summary.errors)

    incident = db_session.get(Incident, summary.incident_id)
    assert incident.severity == "high"
    err_count = db_session.scalar(
        select(func.count())
        .select_from(IncidentEvent)
        .where(IncidentEvent.incident_id == summary.incident_id)
        .where(IncidentEvent.event_type == "lab_bgp_collection_error")
    )
    assert err_count == 4


# ---------- API ----------


def test_api_collect_returns_summary(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        collector_module, "_default_runner", _all_healthy_runner()
    )
    response = client.post("/api/lab/collect/bgp")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["routers_seen"] == 4
    assert body["established_count"] == 6
    assert body["non_established_count"] == 0
    assert body["events_created"] == 10
    assert body["errors"] == []


# ---------- CLI ----------


def test_cli_collect_prints_summary_json(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(collector_module, "SessionLocal", fake_session_local)
    monkeypatch.setattr(
        collector_module, "_default_runner", _all_healthy_runner()
    )

    exit_code = collector_module.main(["--collect"])
    assert exit_code == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["routers_seen"] == 4
    assert parsed["established_count"] == 6


def test_cli_requires_collect_flag() -> None:
    with pytest.raises(SystemExit) as exc:
        collector_module.main([])
    assert exc.value.code == 2
