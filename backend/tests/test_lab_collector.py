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


def test_cli_requires_mode_flag() -> None:
    """Mutex group is required: neither --collect nor --watch fails fast."""
    with pytest.raises(SystemExit) as exc:
        collector_module.main([])
    assert exc.value.code == 2


def test_cli_collect_and_watch_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as exc:
        collector_module.main(["--collect", "--watch", "--iterations", "1"])
    assert exc.value.code == 2


# ---------- watch mode ----------


def _stub_session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(collector_module, "SessionLocal", fake_session_local)


def _stub_sleep(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    def fake_sleep(seconds: int) -> None:
        calls.append(seconds)

    monkeypatch.setattr(collector_module.time, "sleep", fake_sleep)
    return calls


def _stub_collect_returning(
    summaries: list[object], monkeypatch: pytest.MonkeyPatch
) -> list[object]:
    """Replace collect_lab_bgp_snapshot with a function that pops from `summaries`
    (in order). A list entry that is an Exception instance is raised instead of
    returned. Returns a captured-calls list for assertions."""
    calls: list[object] = []
    queue = list(summaries)

    def fake_collect(db, runner=None, routers=None):  # noqa: ARG001
        calls.append(True)
        if not queue:
            raise AssertionError("collect called more times than summaries queued")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(collector_module, "collect_lab_bgp_snapshot", fake_collect)
    return calls


def _ok_summary() -> "collector_module.LabBgpCollectionSummary":
    from uuid import uuid4

    return collector_module.LabBgpCollectionSummary(
        incident_id=uuid4(),
        routers_seen=4,
        peers_seen=6,
        established_count=6,
        non_established_count=0,
        events_created=10,
    )


def _summary_with_errors() -> "collector_module.LabBgpCollectionSummary":
    from uuid import uuid4

    return collector_module.LabBgpCollectionSummary(
        incident_id=uuid4(),
        routers_seen=3,
        peers_seen=5,
        established_count=5,
        non_established_count=0,
        events_created=9,
        errors=["branch-1: vtysh exit 1: container not running"],
    )


def test_watch_requires_iterations(db_session: Session) -> None:
    # --watch without --iterations -> argparse exits 2
    with pytest.raises(SystemExit) as exc:
        collector_module.main(["--watch"])
    assert exc.value.code == 2


def test_watch_iterations_lower_bound(db_session: Session) -> None:
    with pytest.raises(SystemExit) as exc:
        collector_module.main(["--watch", "--iterations", "0"])
    assert exc.value.code == 2


def test_watch_iterations_upper_bound(db_session: Session) -> None:
    with pytest.raises(SystemExit) as exc:
        collector_module.main(["--watch", "--iterations", "101"])
    assert exc.value.code == 2


def test_watch_interval_seconds_bounds(db_session: Session) -> None:
    with pytest.raises(SystemExit) as exc:
        collector_module.main(
            ["--watch", "--iterations", "1", "--interval-seconds", "0"]
        )
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        collector_module.main(
            ["--watch", "--iterations", "1", "--interval-seconds", "3601"]
        )
    assert exc.value.code == 2


def test_watch_runs_exactly_N_iterations_with_N_minus_1_sleeps(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_session(db_session, monkeypatch)
    sleeps = _stub_sleep(monkeypatch)
    calls = _stub_collect_returning(
        [_ok_summary(), _ok_summary(), _ok_summary()], monkeypatch
    )

    exit_code = collector_module.main(
        ["--watch", "--iterations", "3", "--interval-seconds", "7"]
    )
    assert exit_code == 0
    assert len(calls) == 3
    assert sleeps == [7, 7]  # N-1 sleeps, each = --interval-seconds

    out_lines = capsys.readouterr().out.strip().split("\n")
    assert len(out_lines) == 3
    parsed = [json.loads(line) for line in out_lines]
    for row in parsed:
        assert row["routers_seen"] == 4
        assert row["established_count"] == 6


def test_watch_continues_past_iteration_with_collection_errors(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a per-iteration summary contains `errors` (e.g. a router was down
    for that scrape), the loop must keep running through all iterations."""
    _stub_session(db_session, monkeypatch)
    _stub_sleep(monkeypatch)
    _stub_collect_returning(
        [_ok_summary(), _summary_with_errors(), _ok_summary()], monkeypatch
    )

    exit_code = collector_module.main(["--watch", "--iterations", "3"])
    assert exit_code == 0

    out_lines = capsys.readouterr().out.strip().split("\n")
    assert len(out_lines) == 3
    parsed = [json.loads(line) for line in out_lines]
    assert parsed[1]["errors"] and "branch-1" in parsed[1]["errors"][0]


def test_watch_continues_past_unexpected_exception(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An UNEXPECTED exception from collect_lab_bgp_snapshot is caught,
    logged as a JSON error row, and the loop continues."""
    _stub_session(db_session, monkeypatch)
    _stub_sleep(monkeypatch)
    _stub_collect_returning(
        [
            _ok_summary(),
            RuntimeError("simulated unexpected blip"),
            _ok_summary(),
        ],
        monkeypatch,
    )

    exit_code = collector_module.main(["--watch", "--iterations", "3"])
    assert exit_code == 0

    out_lines = capsys.readouterr().out.strip().split("\n")
    assert len(out_lines) == 3
    parsed = [json.loads(line) for line in out_lines]
    assert "incident_id" in parsed[0]
    assert "error" in parsed[1]
    assert "RuntimeError" in parsed[1]["error"]
    assert "simulated unexpected blip" in parsed[1]["error"]
    assert "incident_id" in parsed[2]


def test_collect_single_shot_unchanged_after_cli_restructure(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 11A is additive - the original `--collect` flow must keep
    behaving identically."""
    _stub_session(db_session, monkeypatch)
    monkeypatch.setattr(
        collector_module, "_default_runner", _all_healthy_runner()
    )

    exit_code = collector_module.main(["--collect"])
    assert exit_code == 0

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["routers_seen"] == 4
    assert parsed["established_count"] == 6


# ============================================================
# Phase 21A — defense-in-depth guards + umbrella snapshot
# ============================================================


from app.lab.collector import (  # noqa: E402 - test-section import
    EXPECTED_BGP_LOOPBACKS_FOR,
    LAB_FULL_SNAPSHOT_INCIDENT_TYPE,
    LAB_LOOPBACKS,
    _assert_known_router,
    _assert_show_command,
    collect_lab_snapshot,
)
from app.db.models import IncidentEvidence  # noqa: E402


# ---------- runtime guards ----------


def test_assert_known_router_accepts_lab_members() -> None:
    for r in ("edge-1", "edge-2", "core-1", "branch-1"):
        _assert_known_router(r)  # no raise


def test_assert_known_router_rejects_non_lab_names() -> None:
    for bogus in ("rogue-router", "../malicious", "edge-1; rm -rf", ""):
        with pytest.raises(ValueError, match="known NeuroNOC lab"):
            _assert_known_router(bogus)


def test_assert_show_command_accepts_show_calls() -> None:
    for cmd in (
        "show ip bgp summary json",
        "show interface json",
        "show running-config",
        "SHOW IP ROUTE",  # case-insensitive prefix check
    ):
        _assert_show_command(cmd)  # no raise


def test_assert_show_command_rejects_non_show() -> None:
    with pytest.raises(ValueError, match="must start with 'show '"):
        _assert_show_command("clear ip bgp *")


def test_assert_show_command_rejects_forbidden_tokens_in_show_calls() -> None:
    # A `show` call that smuggles a banned token (e.g. piping into a clear,
    # or sneaking 'configure' into the arguments) must fail loudly.
    for bad in (
        "show running-config | clear ip bgp *",
        "show foo ; configure terminal",
        "show debug",
        "show me; reload",
        "show ; write erase",
    ):
        with pytest.raises(ValueError, match="forbidden token"):
            _assert_show_command(bad)


def test_assert_show_command_rejects_punctuation_adjacent_forbidden_tokens() -> None:
    """Phase 21A hardening: a forbidden token must be caught even when
    it's adjacent to shell-style punctuation without surrounding spaces.
    The earlier whole-word check only looked at space-bounded tokens, so
    `show running-config|clear ip bgp *` could slip through. The
    normalization-then-token-match pipeline closes that gap."""
    for bad in (
        "show running-config|clear ip bgp *",
        "show foo;reload",
        "show foo;configure terminal",
        "show foo&&clear ip bgp *",
        "show foo|reload|configure",
        "show running-config;write memory",
    ):
        with pytest.raises(ValueError, match="forbidden token"):
            _assert_show_command(bad)


def test_assert_show_command_keeps_allowing_real_show_commands() -> None:
    """Defense-in-depth must not flag legitimate FRR show commands.
    Pinned alongside the punctuation rejection so a future tightening
    can't silently break the happy path. `show debugging` is the FRR
    read-only way to display current debug settings - it must NOT be
    confused with the `debug` forbidden token."""
    for good in (
        "show running-config",
        "show interface json",
        "show ip bgp summary json",
        "show ip route",
        "show debugging",  # read-only inspector, distinct from `debug ...`
    ):
        _assert_show_command(good)  # no raise


# ---------- snapshot collector: fake runner that knows three commands ----------


def _interface_json(*, ifname: str, admin: str = "up", oper: str = "up",
                    line: str = "is up", in_err: int = 0,
                    out_err: int = 0) -> dict:
    return {
        ifname: {
            "administrativeStatus": admin,
            "operationalStatus": oper,
            "lineProtocol": line,
            "counters": {
                "inputErrors": in_err,
                "outputErrors": out_err,
                "inputBytes": 1234,
                "outputBytes": 5678,
            },
        }
    }


def _route_json(router: str, *, missing: set[str] | None = None) -> dict:
    missing_prefixes = missing or set()
    routes: dict[str, list[dict]] = {
        LAB_LOOPBACKS[router]: [{"protocol": "connected"}],
    }
    for prefix in EXPECTED_BGP_LOOPBACKS_FOR[router]:
        if prefix not in missing_prefixes:
            routes[prefix] = [{"protocol": "bgp", "selected": True}]
    return routes


def _healthy_routes() -> dict[str, dict]:
    return {r: _route_json(r) for r in ("edge-1", "edge-2", "core-1", "branch-1")}


def _snapshot_runner(
    *,
    bgp_summary: dict[str, dict | None],
    interfaces: dict[str, dict | None],
    configs: dict[str, str | None],
    routes: dict[str, dict | None] | None = None,
    fail_bgp: set[str] | None = None,
    fail_interfaces: set[str] | None = None,
    fail_config: set[str] | None = None,
    fail_routes: set[str] | None = None,
) -> Callable:
    """Dispatcher fake. Routes by the vtysh command embedded at cmd[5]."""
    fb = fail_bgp or set()
    fi = fail_interfaces or set()
    fc = fail_config or set()
    fr = fail_routes or set()
    route_payloads = routes if routes is not None else _healthy_routes()

    def _runner(cmd: list[str]) -> tuple[str, str, int]:
        router = _router_from_cmd(cmd)
        vtysh_cmd = cmd[5]  # docker, exec, <ct>, vtysh, -c, "show ..."
        if vtysh_cmd.startswith("show ip bgp summary"):
            if router in fb:
                return "", "container not running", 1
            body = bgp_summary.get(router)
            return (json.dumps(body) if body is not None else "not json"), "", 0
        if vtysh_cmd.startswith("show interface"):
            if router in fi:
                return "", "container not running", 1
            body = interfaces.get(router)
            return (json.dumps(body) if body is not None else "not json"), "", 0
        if vtysh_cmd.startswith("show ip route"):
            if router in fr:
                return "", "route table unavailable", 1
            body = route_payloads.get(router)
            return (json.dumps(body) if body is not None else "not json"), "", 0
        if vtysh_cmd.startswith("show running-config"):
            if router in fc:
                return "", "vtysh: command failed", 1
            text = configs.get(router)
            return (text if text is not None else ""), "", 0
        raise AssertionError(f"unexpected vtysh command in test: {vtysh_cmd!r}")

    return _runner


def _all_healthy_snapshot_runner() -> Callable:
    bgp = {
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
    interfaces = {
        r: _interface_json(ifname=f"eth0-{r}")
        for r in ("edge-1", "edge-2", "core-1", "branch-1")
    }
    configs = {
        r: f"! running-config for {r}\nhostname {r}\nrouter bgp 65000\n"
        for r in ("edge-1", "edge-2", "core-1", "branch-1")
    }
    return _snapshot_runner(
        bgp_summary=bgp, interfaces=interfaces, configs=configs
    )


def test_snapshot_all_healthy_creates_low_severity_incident(
    db_session: Session,
) -> None:
    summary = collect_lab_snapshot(
        db_session, runner=_all_healthy_snapshot_runner()
    )

    assert summary.routers_seen == 4
    assert summary.peers_seen == 6
    assert summary.established_count == 6
    assert summary.non_established_count == 0
    assert summary.interfaces_seen == 4
    assert summary.interfaces_down == 0
    assert summary.interfaces_with_errors == 0
    assert summary.configs_collected == 4
    assert summary.routes_collected == 4
    assert summary.routes_missing == 0
    assert summary.errors == []

    incident = db_session.get(Incident, summary.incident_id)
    assert incident is not None
    assert incident.incident_type == LAB_FULL_SNAPSHOT_INCIDENT_TYPE
    assert incident.summary.startswith(LAB_MARKER)
    assert incident.severity == "low"
    assert "all signals healthy" in incident.title


def test_snapshot_writes_per_router_running_config_evidence(
    db_session: Session,
) -> None:
    summary = collect_lab_snapshot(
        db_session, runner=_all_healthy_snapshot_runner()
    )
    evidence = db_session.scalars(
        select(IncidentEvidence).where(
            IncidentEvidence.incident_id == summary.incident_id
        ).where(
            IncidentEvidence.evidence_type == "running_config_snapshot"
        )
    ).all()
    assert len(evidence) == 4
    assert summary.evidence_created == 8
    for e in evidence:
        assert e.evidence_type == "running_config_snapshot"
        assert e.source.startswith("lab:")
        assert "running-config" in e.content
        assert e.payload is not None
        assert e.payload["_origin"] == "lab-collector"
        assert e.payload["truncated"] is False


def test_snapshot_one_peer_not_established_lifts_to_medium(
    db_session: Session,
) -> None:
    bgp = {
        "edge-1": _summary_for(
            "edge-1",
            peers={"172.30.1.2": _peer("Active", remote_as=65000)},  # NOT Established
        ),
        "edge-2": _summary_for(
            "edge-2",
            peers={"172.30.2.2": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=4)},
        ),
        "core-1": _summary_for("core-1", peers={}),
        "branch-1": _summary_for("branch-1", peers={}),
    }
    interfaces = {
        r: _interface_json(ifname=f"eth0-{r}")
        for r in ("edge-1", "edge-2", "core-1", "branch-1")
    }
    configs = {r: f"hostname {r}\n" for r in ("edge-1", "edge-2", "core-1", "branch-1")}
    runner = _snapshot_runner(
        bgp_summary=bgp, interfaces=interfaces, configs=configs
    )

    summary = collect_lab_snapshot(db_session, runner=runner)
    assert summary.non_established_count == 1
    incident = db_session.get(Incident, summary.incident_id)
    assert incident is not None
    assert incident.severity == "medium"


def test_snapshot_interface_errors_lift_to_medium(db_session: Session) -> None:
    bgp = {
        r: _summary_for(r, peers={"1.1.1.1": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=1)})
        for r in ("edge-1", "edge-2", "core-1", "branch-1")
    }
    # edge-1 has an interface with input errors
    interfaces = {
        "edge-1": _interface_json(ifname="eth0", in_err=42),
        "edge-2": _interface_json(ifname="eth0"),
        "core-1": _interface_json(ifname="eth0"),
        "branch-1": _interface_json(ifname="eth0"),
    }
    configs = {r: f"hostname {r}\n" for r in ("edge-1", "edge-2", "core-1", "branch-1")}
    runner = _snapshot_runner(
        bgp_summary=bgp, interfaces=interfaces, configs=configs
    )

    summary = collect_lab_snapshot(db_session, runner=runner)
    assert summary.interfaces_with_errors == 1
    incident = db_session.get(Incident, summary.incident_id)
    assert incident is not None
    assert incident.severity == "medium"


def test_snapshot_interface_down_lifts_to_high(db_session: Session) -> None:
    bgp = {
        r: _summary_for(r, peers={"1.1.1.1": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=1)})
        for r in ("edge-1", "edge-2", "core-1", "branch-1")
    }
    interfaces = {
        "edge-1": _interface_json(ifname="eth0", admin="up", oper="down", line="is down"),
        "edge-2": _interface_json(ifname="eth0"),
        "core-1": _interface_json(ifname="eth0"),
        "branch-1": _interface_json(ifname="eth0"),
    }
    configs = {r: f"hostname {r}\n" for r in ("edge-1", "edge-2", "core-1", "branch-1")}
    runner = _snapshot_runner(
        bgp_summary=bgp, interfaces=interfaces, configs=configs
    )

    summary = collect_lab_snapshot(db_session, runner=runner)
    assert summary.interfaces_down == 1
    incident = db_session.get(Incident, summary.incident_id)
    assert incident is not None
    assert incident.severity == "high"


def test_snapshot_scrape_failure_lifts_to_high_with_error_event(
    db_session: Session,
) -> None:
    bgp = {
        r: _summary_for(r, peers={"1.1.1.1": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=1)})
        for r in ("edge-1", "edge-2", "core-1", "branch-1")
    }
    interfaces = {
        r: _interface_json(ifname="eth0")
        for r in ("edge-1", "edge-2", "core-1", "branch-1")
    }
    configs = {r: f"hostname {r}\n" for r in ("edge-1", "edge-2", "core-1", "branch-1")}
    # edge-1 BGP scrape fails; everything else succeeds.
    runner = _snapshot_runner(
        bgp_summary=bgp,
        interfaces=interfaces,
        configs=configs,
        fail_bgp={"edge-1"},
    )

    summary = collect_lab_snapshot(db_session, runner=runner)
    incident = db_session.get(Incident, summary.incident_id)
    assert incident is not None
    assert incident.severity == "high"
    assert any("edge-1 bgp:" in e for e in summary.errors)

    # Verify a dedicated lab_bgp_collection_error event was written.
    error_events = db_session.scalars(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == summary.incident_id)
        .where(IncidentEvent.event_type == "lab_bgp_collection_error")
    ).all()
    assert len(error_events) == 1


def test_snapshot_oversize_config_is_truncated(db_session: Session) -> None:
    big = "x" * (100 * 1024)  # 100 KB > 64 KB cap
    bgp = {
        r: _summary_for(r, peers={"1.1.1.1": _peer("Established", remote_as=65000, pfx_rcd=1, pfx_snt=1)})
        for r in ("edge-1", "edge-2", "core-1", "branch-1")
    }
    interfaces = {
        r: _interface_json(ifname="eth0")
        for r in ("edge-1", "edge-2", "core-1", "branch-1")
    }
    configs = {
        "edge-1": big,
        "edge-2": "hostname edge-2\n",
        "core-1": "hostname core-1\n",
        "branch-1": "hostname branch-1\n",
    }
    runner = _snapshot_runner(
        bgp_summary=bgp, interfaces=interfaces, configs=configs
    )

    summary = collect_lab_snapshot(db_session, runner=runner)
    evidence = db_session.scalars(
        select(IncidentEvidence)
        .where(IncidentEvidence.incident_id == summary.incident_id)
        .where(IncidentEvidence.source == "lab:edge-1")
        .where(IncidentEvidence.evidence_type == "running_config_snapshot")
    ).all()
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.payload is not None
    assert ev.payload["truncated"] is True
    assert ev.payload["byte_count"] == 100 * 1024
    # Content is capped at the byte limit (decoding ASCII so byte == char).
    assert len(ev.content) == 64 * 1024


# ---------- Phase 21E: route table snapshots ----------


def test_route_table_all_healthy_emits_no_missing_routes(
    db_session: Session,
) -> None:
    summary = collect_lab_snapshot(
        db_session, runner=_all_healthy_snapshot_runner()
    )
    assert summary.routes_collected == 4
    assert summary.routes_missing == 0

    route_missing_events = db_session.scalars(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == summary.incident_id)
        .where(IncidentEvent.event_type == "lab_route_missing")
    ).all()
    assert route_missing_events == []

    incident = db_session.get(Incident, summary.incident_id)
    assert incident is not None
    assert incident.severity == "low"


def test_route_table_missing_prefix_emits_lab_route_missing(
    db_session: Session,
) -> None:
    missing = "10.0.0.31/32"
    routes = _healthy_routes()
    routes["edge-1"] = _route_json("edge-1", missing={missing})
    runner = _snapshot_runner(
        bgp_summary={
            r: _summary_for(r, peers={})
            for r in ("edge-1", "edge-2", "core-1", "branch-1")
        },
        interfaces={
            r: _interface_json(ifname="eth0")
            for r in ("edge-1", "edge-2", "core-1", "branch-1")
        },
        configs={
            r: f"hostname {r}\n"
            for r in ("edge-1", "edge-2", "core-1", "branch-1")
        },
        routes=routes,
    )

    summary = collect_lab_snapshot(db_session, runner=runner)
    assert summary.routes_collected == 4
    assert summary.routes_missing == 1
    incident = db_session.get(Incident, summary.incident_id)
    assert incident is not None
    assert incident.severity == "medium"

    route_missing_events = db_session.scalars(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == summary.incident_id)
        .where(IncidentEvent.event_type == "lab_route_missing")
    ).all()
    assert len(route_missing_events) == 1
    assert route_missing_events[0].payload is not None
    assert route_missing_events[0].payload["router"] == "edge-1"
    assert route_missing_events[0].payload["prefix"] == missing
    assert route_missing_events[0].payload["expected_protocol"] == "bgp"


def test_route_table_failure_lifts_to_high_with_error_event(
    db_session: Session,
) -> None:
    runner = _snapshot_runner(
        bgp_summary={
            r: _summary_for(r, peers={})
            for r in ("edge-1", "edge-2", "core-1", "branch-1")
        },
        interfaces={
            r: _interface_json(ifname="eth0")
            for r in ("edge-1", "edge-2", "core-1", "branch-1")
        },
        configs={
            r: f"hostname {r}\n"
            for r in ("edge-1", "edge-2", "core-1", "branch-1")
        },
        fail_routes={"edge-1"},
    )

    summary = collect_lab_snapshot(db_session, runner=runner)
    assert summary.routes_collected == 3
    assert any("edge-1 routes:" in e for e in summary.errors)
    incident = db_session.get(Incident, summary.incident_id)
    assert incident is not None
    assert incident.severity == "high"

    error_events = db_session.scalars(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == summary.incident_id)
        .where(IncidentEvent.event_type == "lab_route_collection_error")
    ).all()
    assert len(error_events) == 1


def test_route_table_evidence_row_written_per_healthy_router(
    db_session: Session,
) -> None:
    summary = collect_lab_snapshot(
        db_session, runner=_all_healthy_snapshot_runner()
    )
    route_evidence = db_session.scalars(
        select(IncidentEvidence)
        .where(IncidentEvidence.incident_id == summary.incident_id)
        .where(IncidentEvidence.evidence_type == "route_table_snapshot")
    ).all()
    assert len(route_evidence) == 4
    for ev in route_evidence:
        assert ev.source.startswith("lab:")
        assert "10.0.0." in ev.content
        assert ev.payload is not None
        assert ev.payload["_origin"] == "lab-collector"
        assert ev.payload["truncated"] is False
        assert ev.payload["max_bytes"] == 64 * 1024


def test_route_table_topology_pin() -> None:
    assert set(EXPECTED_BGP_LOOPBACKS_FOR) == set(LAB_ROUTERS)
    assert set(LAB_LOOPBACKS) == set(LAB_ROUTERS)
    for router in LAB_ROUTERS:
        expected = set(EXPECTED_BGP_LOOPBACKS_FOR[router])
        assert expected == {
            prefix
            for other, prefix in LAB_LOOPBACKS.items()
            if other != router
        }
        assert LAB_LOOPBACKS[router] not in expected


# ---------- API + CLI ----------


def test_api_collect_snapshot_returns_201_and_summary(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Patch the default runner so the endpoint doesn't try to talk to a
    # real Docker daemon.
    monkeypatch.setattr(
        collector_module, "_default_runner", _all_healthy_snapshot_runner()
    )

    response = client.post("/api/lab/collect/snapshot")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["routers_seen"] == 4
    assert body["configs_collected"] == 4
    assert body["routes_collected"] == 4
    assert body["routes_missing"] == 0
    assert body["evidence_created"] == 8


def test_api_collect_bgp_unchanged_by_phase21a(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 21A backward-compat pin: POST /api/lab/collect/bgp must keep
    returning the original LabBgpCollectionSummary shape."""
    monkeypatch.setattr(
        collector_module, "_default_runner", _all_healthy_runner()
    )

    response = client.post("/api/lab/collect/bgp")
    assert response.status_code == 201, response.text
    body = response.json()
    # Shape pin: BGP-only fields, NO snapshot-only fields.
    assert body["routers_seen"] == 4
    assert body["established_count"] == 6
    assert "interfaces_seen" not in body
    assert "configs_collected" not in body
    assert "evidence_created" not in body


def test_cli_collect_snapshot_prints_full_summary(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_session(db_session, monkeypatch)
    monkeypatch.setattr(
        collector_module, "_default_runner", _all_healthy_snapshot_runner()
    )

    exit_code = collector_module.main(["--collect-snapshot"])
    assert exit_code == 0

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["routers_seen"] == 4
    assert parsed["configs_collected"] == 4
    assert parsed["routes_collected"] == 4
    assert parsed["routes_missing"] == 0
    assert parsed["evidence_created"] == 8


# ============================================================
# Phase 21B - demo path: lab snapshot -> Phase 5 -> 6 -> 7
# ============================================================


def test_phase21b_lab_snapshot_feeds_full_workflow_end_to_end(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 21B contract: an Incident produced by `collect_lab_snapshot()`
    must be consumable by the existing chain end-to-end with NO new
    plumbing — Phase 5 LangGraph workflow, Phase 6 RCA (deterministic
    fallback), and Phase 7 remediation planner.

    `build_remediation_plan()` cascades through all three: it calls
    `run_incident_analysis()` internally if no completed AgentRun exists,
    then `generate_rca_explanation(require_llm=False)`, then picks a
    template. So one call exercises Phase 5 + 6 + 7 in sequence.

    RCA is forced onto its deterministic fallback by stubbing
    `generate_ollama_json` to raise `OllamaUnavailableError`. The test
    NEVER depends on a live Ollama daemon, and the fallback path is
    actually exercised (not skipped).

    NO external device contact: the fake `_snapshot_runner` intercepts
    every `docker exec` call. NO remediation execution: the planner is
    plan-only, and we assert `requires_approval=True` on the produced
    plan to pin that contract.

    Boundary noted: the lab event types (`lab_bgp_peer_not_established`,
    `lab_interface_status`) don't match the simulator-shaped event types
    that the existing Phase 4 anomaly rules look for, so the workflow
    produces 0 anomaly findings for this Incident today. That's a known
    boundary and a Phase 21C+ candidate; what 21B pins is that the data
    SHAPE is compatible and the chain runs to completion.
    """
    # Imports inside the test keep the existing module-level import list
    # focused on the collector itself.
    from app.agents.runner import run_incident_analysis
    from app.db.models import AgentRun
    from app.llm.ollama import OllamaUnavailableError
    from app.rca import explainer as explainer_module
    from app.remediation.planner import build_remediation_plan

    def _no_ollama(*args: object, **kwargs: object) -> dict:
        raise OllamaUnavailableError("test stub: Ollama disabled")

    monkeypatch.setattr(
        explainer_module, "generate_ollama_json", _no_ollama
    )

    # ---- Fault scenario ----
    # edge-1 has BOTH a not-Established BGP peer AND an interface
    # carrying input errors. Other routers are healthy. Every router
    # returns its running-config cleanly.
    bgp = {
        "edge-1": _summary_for(
            "edge-1",
            peers={"172.30.1.2": _peer("Active", remote_as=65000)},
        ),
        "edge-2": _summary_for(
            "edge-2",
            peers={
                "172.30.2.2": _peer(
                    "Established", remote_as=65000, pfx_rcd=1, pfx_snt=4
                )
            },
        ),
        "core-1": _summary_for("core-1", peers={}),
        "branch-1": _summary_for("branch-1", peers={}),
    }
    interfaces = {
        "edge-1": _interface_json(ifname="Gi0/1", in_err=42),
        "edge-2": _interface_json(ifname="Gi0/1"),
        "core-1": _interface_json(ifname="Gi0/1"),
        "branch-1": _interface_json(ifname="Gi0/1"),
    }
    configs = {
        r: f"hostname {r}\nrouter bgp 65000\n"
        for r in ("edge-1", "edge-2", "core-1", "branch-1")
    }
    runner = _snapshot_runner(
        bgp_summary=bgp, interfaces=interfaces, configs=configs
    )

    # ---- Step 1: Phase 21A collector produces the Incident ----
    summary = collect_lab_snapshot(db_session, runner=runner)
    incident = db_session.get(Incident, summary.incident_id)
    assert incident is not None
    assert incident.incident_type == LAB_FULL_SNAPSHOT_INCIDENT_TYPE
    # Fault scenario: 1 not-Established peer + 1 interface with errors,
    # but NO interface admin/oper down and NO scrape failures, so
    # severity caps at medium per the Phase 21A rules.
    assert incident.severity == "medium"
    assert summary.non_established_count == 1
    assert summary.interfaces_with_errors == 1

    event_types = db_session.scalars(
        select(IncidentEvent.event_type).where(
            IncidentEvent.incident_id == summary.incident_id
        )
    ).all()
    assert "lab_bgp_peer_not_established" in event_types
    assert "lab_interface_status" in event_types

    evidence_types = db_session.scalars(
        select(IncidentEvidence.evidence_type).where(
            IncidentEvidence.incident_id == summary.incident_id
        )
    ).all()
    assert evidence_types.count("running_config_snapshot") == 4

    # ---- Step 2: Phase 5 LangGraph runs to completion ----
    # Call directly so we can pin the AgentRun shape; `build_remediation_plan`
    # below would also have run this internally if we'd skipped it.
    agent_run = run_incident_analysis(db_session, summary.incident_id)
    assert agent_run.status == "completed"
    db_session.refresh(agent_run)
    # Phase 5 declares six deterministic LangGraph nodes; each persists
    # one AgentStep regardless of finding count.
    assert len(agent_run.steps) == 6

    # ---- Step 3: Phase 6 RCA via deterministic fallback ----
    rca = explainer_module.generate_rca_explanation(
        db_session, summary.incident_id, require_llm=False
    )
    # `llm_available=False` confirms the test exercised the fallback
    # branch (not a real Ollama call).
    assert rca.llm_available is False
    assert rca.summary  # non-empty deterministic summary
    assert rca.likely_root_cause  # non-empty

    # ---- Step 4: Phase 7 remediation planner produces a draft plan ----
    plan = build_remediation_plan(db_session, summary.incident_id)
    assert plan.incident_id == summary.incident_id
    assert plan.title  # non-empty
    # **Phase 21C upgrade**: lab events now feed the existing anomaly
    # rules, so a fault scenario produces specific findings (BGP +
    # interface). The Phase 5 workflow's _THEME_MAP routes those onto
    # `routing_failure` + `interface_physical_issue` themes; Phase 7's
    # pick_template iterates themes in sorted order and lands on
    # `template_interface_errors_spike` first (plan_type
    # `interface_physical_investigation`). The critical pin is that the
    # plan is no longer `generic_investigation` - a specific template
    # fired off real lab data.
    assert plan.plan_type != "generic_investigation"

    # Plan-only contract pinned: a plan produced by THIS chain must
    # still require human approval before any imaginary execution path
    # could touch a device. Phase 21B/21C do NOT introduce remediation
    # execution.
    assert plan.requires_approval is True

    # **Phase 21C upgrade**: the lab events now actually produce
    # anomaly findings via the extended rule_bgp_neighbor_down +
    # rule_interface_error_spike + rule_link_down. Pin a non-zero count
    # via a direct analyze_incident() call so a regression that loses
    # the lab mappings would fail here too.
    from app.anomaly.engine import analyze_incident
    direct_findings = analyze_incident(db_session, summary.incident_id)
    assert len(direct_findings) >= 1
    finding_names = {f.rule_name for f in direct_findings}
    # At least one of the two specific-rule signals is present (the
    # fault scenario has both not-Established BGP + interface errors,
    # but pinning ANY-of keeps the test resilient if a future rule
    # rewrite consolidates them).
    assert finding_names & {
        "bgp_neighbor_down_detected",
        "interface_error_spike_detected",
        "link_down_detected",
    }

    # Confirm exactly one AgentRun was created end-to-end (proves the
    # planner reused the existing run rather than triggering a second).
    all_runs = db_session.scalars(
        select(AgentRun).where(AgentRun.incident_id == summary.incident_id)
    ).all()
    assert len(all_runs) == 1
