"""Phase 8C one-shot FRR lab collector.

Scrapes the four routers from the Phase 8B Compose lab and persists the BGP
snapshot into the existing `Incident` / `IncidentEvent` tables - no schema
change. One `Incident` is created per `--collect` invocation, tagged with
`LAB_MARKER` in its `summary` so future cleanup or filtering can target lab
data without touching operator-created incidents or Phase 3 simulator data.

Hard contract for this module:
- One-shot only. No background loop, no scheduler, no daemon.
- Read-only towards the lab: only `vtysh -c "show ... json"` commands.
  No config edits, no `clear ...`, no `conf t`.
- Safe to call repeatedly: every run produces a fresh `Incident`.
- Failures on a single router do NOT abort the whole collection - they
  record a dedicated `lab_bgp_collection_error` event instead.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Incident, IncidentEvent, IncidentEvidence
from app.db.session import SessionLocal

LAB_MARKER = "[lab-collector]"
LAB_INCIDENT_TYPE = "lab_bgp_collection"

# Phase 21A umbrella incident type for the BGP + interfaces + config snapshot.
LAB_FULL_SNAPSHOT_INCIDENT_TYPE = "lab_full_snapshot"

LAB_ROUTERS: list[str] = ["edge-1", "edge-2", "core-1", "branch-1"]
LAB_ROUTERS_SET: frozenset[str] = frozenset(LAB_ROUTERS)

# Defense-in-depth: tokens that must never appear in a vtysh command issued
# by this module. Even though every command MUST already start with "show ",
# this list catches "show running-config | clear ..." pipeline tricks and
# anything else that smuggles a mutation in.
_FORBIDDEN_VTYSH_TOKENS: frozenset[str] = frozenset(
    {
        "clear",
        "reset",
        "debug",
        "no debug",
        "configure",
        "conf t",
        "write",
        "copy",
        "reload",
        "delete",
        "enable",
    }
)

# Per-router running-config evidence is capped so a misbehaving lab can't
# blow up the IncidentEvidence row. The Phase 8B FRR configs are ~50 lines
# each; 64 KB leaves a lot of headroom while still bounded.
_RUNNING_CONFIG_MAX_BYTES = 64 * 1024


def _assert_known_router(router: str) -> None:
    """Reject any router name that isn't in the Phase 8B lab allow-list.

    Defense-in-depth so this module cannot be coaxed into pointing
    `docker exec` at an arbitrary container (e.g. via a wild `routers=`
    kwarg). Tests use real router names so the existing suite still passes.
    """
    if router not in LAB_ROUTERS_SET:
        raise ValueError(
            f"router {router!r} is not a known NeuroNOC lab container "
            f"(allowed: {sorted(LAB_ROUTERS_SET)})"
        )


_VTYSH_TOKEN_SEP_RE = re.compile(r"[^a-z0-9]+")


def _assert_show_command(vtysh_cmd: str) -> None:
    """Reject any vtysh command that isn't a read-only `show ...` call.

    Belt-and-suspenders alongside the convention that every caller in
    this module passes a `show ...` command. Catches accidental future
    drift (a developer typing `clear ip bgp ...` would fail loudly here
    rather than mutating the lab).

    Normalization: every run of non-alphanumeric characters in the input
    collapses to a single space. This makes punctuation (`;`, `|`, `&`,
    `-`, etc.) a token boundary, so a forbidden word can't hide adjacent
    to shell-style separators like `show running-config|clear ip bgp` or
    `show foo;reload`. After normalization, the space-padded substring
    check is whole-word safe (and `show debugging` is still allowed,
    because `" debug "` is not a substring of `" show debugging "`).
    """
    lowered = vtysh_cmd.lower()
    normalized = " " + _VTYSH_TOKEN_SEP_RE.sub(" ", lowered).strip() + " "
    if not normalized.startswith(" show "):
        raise ValueError(
            f"vtysh command must start with 'show '; got {vtysh_cmd!r}"
        )
    for token in _FORBIDDEN_VTYSH_TOKENS:
        if f" {token} " in normalized:
            raise ValueError(
                f"vtysh command contains forbidden token {token!r}; "
                f"got {vtysh_cmd!r}"
            )


# A CommandRunner takes a docker exec command (already split into argv) and
# returns (stdout, stderr, returncode). Default uses subprocess; tests inject
# a fake to avoid touching live Docker.
CommandResult = tuple[str, str, int]
CommandRunner = Callable[[list[str]], CommandResult]


def _default_runner(cmd: list[str]) -> CommandResult:
    try:
        proc = subprocess.run(  # noqa: S603 - argv is fully controlled below
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return proc.stdout, proc.stderr, proc.returncode
    except FileNotFoundError as exc:
        return "", f"docker CLI not found: {exc}", 127
    except subprocess.TimeoutExpired as exc:
        return "", f"timeout after {exc.timeout}s", 124


def _container_for(router: str) -> str:
    return f"neuronoc-lab-{router}"


def _vtysh_json(
    runner: CommandRunner, router: str, vtysh_cmd: str
) -> tuple[dict | None, str | None]:
    """Run a single read-only vtysh command and parse its JSON output.

    Returns (parsed_dict, None) on success or (None, error_string) on failure.
    The vtysh command MUST start with `show ` (enforced at runtime via
    `_assert_show_command`); the JSON-aware variants below also end in
    ` json` by convention.
    """
    _assert_known_router(router)
    _assert_show_command(vtysh_cmd)
    cmd = ["docker", "exec", _container_for(router), "vtysh", "-c", vtysh_cmd]
    stdout, stderr, rc = runner(cmd)
    if rc != 0:
        snippet = (stderr or stdout or "").strip().splitlines()
        tail = snippet[-1] if snippet else "(no output)"
        return None, f"vtysh exit {rc}: {tail}"
    try:
        return json.loads(stdout), None
    except json.JSONDecodeError as exc:
        return None, f"vtysh returned non-JSON: {exc}"


def _vtysh_text(
    runner: CommandRunner, router: str, vtysh_cmd: str
) -> tuple[str | None, str | None]:
    """Run a single read-only vtysh command and return its raw text output.

    Mirror of `_vtysh_json` for commands that don't have a `json` variant
    (notably `show running-config`). Same read-only guarantees apply.
    Returns (text, None) on success or (None, error_string) on failure.
    """
    _assert_known_router(router)
    _assert_show_command(vtysh_cmd)
    cmd = ["docker", "exec", _container_for(router), "vtysh", "-c", vtysh_cmd]
    stdout, stderr, rc = runner(cmd)
    if rc != 0:
        snippet = (stderr or stdout or "").strip().splitlines()
        tail = snippet[-1] if snippet else "(no output)"
        return None, f"vtysh exit {rc}: {tail}"
    return stdout, None


# ---------- single-router scrape ----------


@dataclass(frozen=True)
class RouterScrape:
    router: str
    summary: dict | None  # parsed `show ip bgp summary json`
    error: str | None  # populated when the scrape failed


def _scrape_router(runner: CommandRunner, router: str) -> RouterScrape:
    summary, err = _vtysh_json(runner, router, "show ip bgp summary json")
    if err is not None:
        return RouterScrape(router=router, summary=None, error=err)
    return RouterScrape(router=router, summary=summary, error=None)


# ---------- payload builders ----------


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _peers_from(summary: dict) -> dict[str, dict]:
    """Extract the per-peer dict from FRR's `show ip bgp summary json` payload.

    FRR 8.x nests peers under `ipv4Unicast.peers`; older shapes have been seen
    with a top-level `peers` key. Try both, default to empty.
    """
    if not isinstance(summary, dict):
        return {}
    af = summary.get("ipv4Unicast")
    if isinstance(af, dict) and isinstance(af.get("peers"), dict):
        return af["peers"]
    if isinstance(summary.get("peers"), dict):
        return summary["peers"]
    return {}


def _router_id_and_as(summary: dict) -> tuple[str | None, int | None]:
    af = summary.get("ipv4Unicast") if isinstance(summary, dict) else None
    if isinstance(af, dict):
        return af.get("routerId"), _safe_int(af.get("as"))
    return None, None


# ---------- public summary returned by collect / CLI / API ----------


class LabBgpCollectionSummary(BaseModel):
    incident_id: UUID
    routers_seen: int
    peers_seen: int
    established_count: int
    non_established_count: int
    events_created: int
    errors: list[str] = Field(default_factory=list)


# ---------- Phase 21A: interface + config scrape helpers ----------


@dataclass(frozen=True)
class InterfaceObservation:
    """One row of normalized interface state extracted from FRR's
    `show interface json`. Field set is the smallest superset that lets
    the existing Phase 4 anomaly rules + Phase 6 RCA reason about
    interface_down / interface_errors_spike incidents."""

    router: str
    ifname: str
    admin_status: str
    oper_status: str
    line_protocol: str
    input_errors: int | None
    output_errors: int | None
    input_bytes: int | None
    output_bytes: int | None


def _normalize_interface(router: str, ifname: str, info: dict) -> InterfaceObservation:
    """Defensively extract interface fields from FRR's `show interface json`.

    FRR's JSON shapes shift slightly between versions; this normalizer
    handles the two we've seen in the Phase 8B lab. Anything missing maps
    to a stable default ('unknown' / None) so the row is always populated
    and the downstream events stay schema-stable.
    """
    counters = info.get("counters")
    if not isinstance(counters, dict):
        counters = {}
    return InterfaceObservation(
        router=router,
        ifname=ifname,
        admin_status=str(info.get("administrativeStatus", "unknown")),
        oper_status=str(info.get("operationalStatus", "unknown")),
        line_protocol=str(info.get("lineProtocol", "unknown")),
        input_errors=_safe_int(counters.get("inputErrors")),
        output_errors=_safe_int(counters.get("outputErrors")),
        input_bytes=_safe_int(counters.get("inputBytes")),
        output_bytes=_safe_int(counters.get("outputBytes")),
    )


def _scrape_interfaces(
    runner: CommandRunner, router: str
) -> tuple[list[InterfaceObservation] | None, str | None]:
    """`show interface json` → list of normalized interface observations.

    Returns (observations, None) on success or (None, error_string) on
    failure. An empty interface dict is a successful scrape with zero
    observations, not a failure.
    """
    parsed, err = _vtysh_json(runner, router, "show interface json")
    if err is not None:
        return None, err
    if not isinstance(parsed, dict):
        return [], None
    observations = [
        _normalize_interface(router, ifname, info)
        for ifname, info in parsed.items()
        if isinstance(info, dict)
    ]
    return observations, None


@dataclass(frozen=True)
class ConfigScrape:
    router: str
    text: str | None  # populated on success, capped at _RUNNING_CONFIG_MAX_BYTES
    truncated: bool
    raw_byte_count: int
    error: str | None


def _scrape_running_config(runner: CommandRunner, router: str) -> ConfigScrape:
    """`show running-config` → capped raw text, plus a `truncated` flag.

    Running-config is the snapshot operators most want when triaging a
    BGP/interface incident ("what was the config when this fired?"). FRR's
    `show running-config` is a `show` command - read-only.
    """
    text, err = _vtysh_text(runner, router, "show running-config")
    if err is not None:
        return ConfigScrape(
            router=router,
            text=None,
            truncated=False,
            raw_byte_count=0,
            error=err,
        )
    raw_bytes = (text or "").encode("utf-8")
    if len(raw_bytes) > _RUNNING_CONFIG_MAX_BYTES:
        return ConfigScrape(
            router=router,
            # Slice on bytes then decode best-effort so we never split a
            # multi-byte sequence in the middle.
            text=raw_bytes[:_RUNNING_CONFIG_MAX_BYTES].decode(
                "utf-8", errors="replace"
            ),
            truncated=True,
            raw_byte_count=len(raw_bytes),
            error=None,
        )
    return ConfigScrape(
        router=router,
        text=text or "",
        truncated=False,
        raw_byte_count=len(raw_bytes),
        error=None,
    )


# ---------- Phase 21A umbrella snapshot summary ----------


class LabSnapshotSummary(BaseModel):
    """Return shape for `collect_lab_snapshot()` / `POST /api/lab/collect/snapshot`.

    Single Incident per call (umbrella), carrying:
    - BGP events (same shape as the Phase 8C BGP-only collector)
    - One `lab_interface_status` event per (router, interface)
    - One `running_config_snapshot` evidence row per router
    - Per-router collection-error events when a scrape failed
    """

    incident_id: UUID
    routers_seen: int
    peers_seen: int
    established_count: int
    non_established_count: int
    interfaces_seen: int
    interfaces_with_errors: int
    interfaces_down: int
    configs_collected: int
    events_created: int
    evidence_created: int
    errors: list[str] = Field(default_factory=list)


# ---------- main entry point ----------


def collect_lab_bgp_snapshot(
    db: Session,
    *,
    runner: CommandRunner | None = None,
    routers: Sequence[str] | None = None,
) -> LabBgpCollectionSummary:
    """Run a one-shot BGP snapshot collection against the Phase 8B lab.

    Always creates a new `Incident` (tagged with `LAB_MARKER`) and writes one
    `IncidentEvent` per (router, peer) plus per-router collection events and
    one error event per unreachable router. Severity is derived from what
    we saw: 'low' if everything is Established, 'medium' if any peer is
    not Established, 'high' if any router could not be scraped at all.
    """
    cmd_runner = runner or _default_runner
    target_routers = list(routers) if routers is not None else list(LAB_ROUTERS)

    scrapes = [_scrape_router(cmd_runner, r) for r in target_routers]

    routers_seen = sum(1 for s in scrapes if s.summary is not None)
    errors = [f"{s.router}: {s.error}" for s in scrapes if s.error]

    peers_seen = 0
    established = 0
    non_established = 0
    pending_events: list[dict] = []

    for scrape in scrapes:
        if scrape.error is not None:
            pending_events.append(
                {
                    "event_type": "lab_bgp_collection_error",
                    "source": f"lab:{scrape.router}",
                    "message": (
                        f"Failed to scrape BGP state on {scrape.router}: {scrape.error}"
                    ),
                    "payload": {
                        "router": scrape.router,
                        "error": scrape.error,
                        "_origin": "lab-collector",
                    },
                }
            )
            continue

        assert scrape.summary is not None
        router_id, local_as = _router_id_and_as(scrape.summary)
        peers = _peers_from(scrape.summary)

        # Per-router snapshot event - one row that "this router reported in".
        pending_events.append(
            {
                "event_type": "lab_bgp_prefix_snapshot",
                "source": f"lab:{scrape.router}",
                "message": (
                    f"{scrape.router}: router_id={router_id} local_as={local_as} "
                    f"peers={len(peers)}"
                ),
                "payload": {
                    "router": scrape.router,
                    "router_id": router_id,
                    "local_as": local_as,
                    "peer_count": len(peers),
                    "_origin": "lab-collector",
                },
            }
        )

        for peer_addr, peer_info in peers.items():
            if not isinstance(peer_info, dict):
                continue
            peers_seen += 1
            state = peer_info.get("state") or "Unknown"
            remote_as = _safe_int(peer_info.get("remoteAs"))
            pfx_rcd = _safe_int(peer_info.get("pfxRcd"))
            pfx_snt = _safe_int(peer_info.get("pfxSnt"))

            if state == "Established":
                established += 1
                event_type = "lab_bgp_peer_established"
                msg = (
                    f"{scrape.router} <-> {peer_addr} (AS {remote_as}): "
                    f"Established, pfx_rcd={pfx_rcd} pfx_snt={pfx_snt}"
                )
            else:
                non_established += 1
                event_type = "lab_bgp_peer_not_established"
                msg = (
                    f"{scrape.router} <-> {peer_addr} (AS {remote_as}): "
                    f"state={state}"
                )

            pending_events.append(
                {
                    "event_type": event_type,
                    "source": f"lab:{scrape.router}",
                    "message": msg,
                    "payload": {
                        "router": scrape.router,
                        "peer": peer_addr,
                        "peer_as": remote_as,
                        "state": state,
                        "prefixes_received": pfx_rcd,
                        "prefixes_sent": pfx_snt,
                        "_origin": "lab-collector",
                    },
                }
            )

    severity = "low"
    if non_established > 0:
        severity = "medium"
    if errors:
        severity = "high"

    title_state = (
        "all peers Established"
        if non_established == 0 and not errors
        else f"{non_established} not-Established peer(s), {len(errors)} error(s)"
    )

    incident = Incident(
        title=f"Lab BGP snapshot - {title_state}",
        severity=severity,
        incident_type=LAB_INCIDENT_TYPE,
        summary=(
            f"{LAB_MARKER} One-shot BGP snapshot of the Phase 8B FRR lab. "
            f"routers_seen={routers_seen} peers_seen={peers_seen} "
            f"established={established} non_established={non_established} "
            f"errors={len(errors)}."
        ),
    )
    db.add(incident)
    db.flush()

    for event_spec in pending_events:
        db.add(IncidentEvent(incident_id=incident.id, **event_spec))

    db.commit()
    db.refresh(incident)

    return LabBgpCollectionSummary(
        incident_id=incident.id,
        routers_seen=routers_seen,
        peers_seen=peers_seen,
        established_count=established,
        non_established_count=non_established,
        events_created=len(pending_events),
        errors=errors,
    )


# ---------- Phase 21A umbrella collector ----------


def _interface_is_down(obs: InterfaceObservation) -> bool:
    """Treat as 'down' if either admin or oper status is anything other
    than 'up' (case-insensitive). FRR's lineProtocol strings like 'is up'
    and 'is down' are checked separately - if any of the three signal
    'down', flag it."""
    if obs.admin_status.lower() != "up":
        return True
    if obs.oper_status.lower() != "up":
        return True
    if "down" in obs.line_protocol.lower():
        return True
    return False


def _interface_has_errors(obs: InterfaceObservation) -> bool:
    in_err = obs.input_errors or 0
    out_err = obs.output_errors or 0
    return in_err > 0 or out_err > 0


def collect_lab_snapshot(
    db: Session,
    *,
    runner: CommandRunner | None = None,
    routers: Sequence[str] | None = None,
) -> LabSnapshotSummary:
    """One-shot collection: BGP + interfaces + running-config per router.

    Creates ONE Incident tagged `[lab-collector]` with incident_type
    `lab_full_snapshot`. Per-router observations land as:
    - BGP events (`lab_bgp_peer_established` / `lab_bgp_peer_not_established`
      / `lab_bgp_prefix_snapshot`) - same shape as the Phase 8C collector
    - Interface events (`lab_interface_status`) - one per interface
    - Running-config evidence (`running_config_snapshot`) - one per router
    - Per-scrape collection-error events when any vtysh call fails:
      `lab_bgp_collection_error`, `lab_interface_collection_error`,
      `lab_config_collection_error`

    Severity escalation:
    - low: every peer Established, every interface up + zero errors,
      every config retrieved
    - medium: any peer not Established OR any interface with errors > 0
    - high: any per-scrape failure OR any interface admin/oper down

    Read-only. Plan-only. Does NOT contact anything outside the local
    NeuroNOC FRR lab containers - `_assert_known_router` + `_assert_show_command`
    enforce that at runtime.
    """
    cmd_runner = runner or _default_runner
    target_routers = list(routers) if routers is not None else list(LAB_ROUTERS)
    for r in target_routers:
        _assert_known_router(r)

    pending_events: list[dict] = []
    pending_evidence: list[dict] = []
    errors: list[str] = []

    routers_seen = 0
    peers_seen = 0
    established = 0
    non_established = 0
    interfaces_seen = 0
    interfaces_with_errors = 0
    interfaces_down = 0
    configs_collected = 0
    any_scrape_failed = False

    for router in target_routers:
        router_id_observed = False

        # ---- BGP ----
        bgp_summary, bgp_err = _vtysh_json(
            cmd_runner, router, "show ip bgp summary json"
        )
        if bgp_err is not None:
            errors.append(f"{router} bgp: {bgp_err}")
            any_scrape_failed = True
            pending_events.append(
                {
                    "event_type": "lab_bgp_collection_error",
                    "source": f"lab:{router}",
                    "message": (
                        f"Failed to scrape BGP state on {router}: {bgp_err}"
                    ),
                    "payload": {
                        "router": router,
                        "error": bgp_err,
                        "_origin": "lab-collector",
                    },
                }
            )
        else:
            assert bgp_summary is not None
            router_id, local_as = _router_id_and_as(bgp_summary)
            peers = _peers_from(bgp_summary)
            router_id_observed = True
            routers_seen += 1
            pending_events.append(
                {
                    "event_type": "lab_bgp_prefix_snapshot",
                    "source": f"lab:{router}",
                    "message": (
                        f"{router}: router_id={router_id} local_as={local_as} "
                        f"peers={len(peers)}"
                    ),
                    "payload": {
                        "router": router,
                        "router_id": router_id,
                        "local_as": local_as,
                        "peer_count": len(peers),
                        "_origin": "lab-collector",
                    },
                }
            )
            for peer_addr, peer_info in peers.items():
                if not isinstance(peer_info, dict):
                    continue
                peers_seen += 1
                state = peer_info.get("state") or "Unknown"
                remote_as = _safe_int(peer_info.get("remoteAs"))
                pfx_rcd = _safe_int(peer_info.get("pfxRcd"))
                pfx_snt = _safe_int(peer_info.get("pfxSnt"))
                if state == "Established":
                    established += 1
                    event_type = "lab_bgp_peer_established"
                    msg = (
                        f"{router} <-> {peer_addr} (AS {remote_as}): "
                        f"Established, pfx_rcd={pfx_rcd} pfx_snt={pfx_snt}"
                    )
                else:
                    non_established += 1
                    event_type = "lab_bgp_peer_not_established"
                    msg = (
                        f"{router} <-> {peer_addr} (AS {remote_as}): "
                        f"state={state}"
                    )
                pending_events.append(
                    {
                        "event_type": event_type,
                        "source": f"lab:{router}",
                        "message": msg,
                        "payload": {
                            "router": router,
                            "peer": peer_addr,
                            "peer_as": remote_as,
                            "state": state,
                            "prefixes_received": pfx_rcd,
                            "prefixes_sent": pfx_snt,
                            "_origin": "lab-collector",
                        },
                    }
                )

        # ---- Interfaces ----
        observations, iface_err = _scrape_interfaces(cmd_runner, router)
        if iface_err is not None:
            errors.append(f"{router} interfaces: {iface_err}")
            any_scrape_failed = True
            pending_events.append(
                {
                    "event_type": "lab_interface_collection_error",
                    "source": f"lab:{router}",
                    "message": (
                        f"Failed to scrape interfaces on {router}: {iface_err}"
                    ),
                    "payload": {
                        "router": router,
                        "error": iface_err,
                        "_origin": "lab-collector",
                    },
                }
            )
        else:
            assert observations is not None
            for obs in observations:
                interfaces_seen += 1
                down = _interface_is_down(obs)
                has_errs = _interface_has_errors(obs)
                if down:
                    interfaces_down += 1
                if has_errs:
                    interfaces_with_errors += 1
                pending_events.append(
                    {
                        "event_type": "lab_interface_status",
                        "source": f"lab:{router}",
                        "message": (
                            f"{router} {obs.ifname}: admin={obs.admin_status} "
                            f"oper={obs.oper_status} input_errors={obs.input_errors} "
                            f"output_errors={obs.output_errors}"
                        ),
                        "payload": {
                            "router": router,
                            "interface": obs.ifname,
                            "admin_status": obs.admin_status,
                            "oper_status": obs.oper_status,
                            "line_protocol": obs.line_protocol,
                            "input_errors": obs.input_errors,
                            "output_errors": obs.output_errors,
                            "input_bytes": obs.input_bytes,
                            "output_bytes": obs.output_bytes,
                            "down": down,
                            "has_errors": has_errs,
                            "_origin": "lab-collector",
                        },
                    }
                )

        # ---- Running-config (evidence) ----
        cfg = _scrape_running_config(cmd_runner, router)
        if cfg.error is not None:
            errors.append(f"{router} config: {cfg.error}")
            any_scrape_failed = True
            pending_events.append(
                {
                    "event_type": "lab_config_collection_error",
                    "source": f"lab:{router}",
                    "message": (
                        f"Failed to scrape running-config on {router}: {cfg.error}"
                    ),
                    "payload": {
                        "router": router,
                        "error": cfg.error,
                        "_origin": "lab-collector",
                    },
                }
            )
        else:
            assert cfg.text is not None
            configs_collected += 1
            pending_evidence.append(
                {
                    "evidence_type": "running_config_snapshot",
                    "source": f"lab:{router}",
                    "content": cfg.text,
                    "payload": {
                        "router": router,
                        "truncated": cfg.truncated,
                        "byte_count": cfg.raw_byte_count,
                        "max_bytes": _RUNNING_CONFIG_MAX_BYTES,
                        "_origin": "lab-collector",
                    },
                }
            )

        # If BGP scrape failed but we somehow still counted the router via
        # an interface success, account for that here so routers_seen is
        # never under-reported by the BGP-only success path.
        if not router_id_observed and (
            (observations is not None and observations) or cfg.error is None
        ):
            routers_seen += 1

    # ---- Severity ----
    severity = "low"
    if non_established > 0 or interfaces_with_errors > 0:
        severity = "medium"
    if any_scrape_failed or interfaces_down > 0:
        severity = "high"

    # ---- Title ----
    if severity == "low":
        title_state = "all signals healthy"
    else:
        title_state_parts: list[str] = []
        if non_established:
            title_state_parts.append(f"{non_established} not-Established peer(s)")
        if interfaces_down:
            title_state_parts.append(f"{interfaces_down} interface(s) down")
        if interfaces_with_errors:
            title_state_parts.append(
                f"{interfaces_with_errors} interface(s) with errors"
            )
        if errors:
            title_state_parts.append(f"{len(errors)} scrape error(s)")
        title_state = (
            ", ".join(title_state_parts) if title_state_parts else "issues observed"
        )

    incident = Incident(
        title=f"Lab snapshot - {title_state}",
        severity=severity,
        incident_type=LAB_FULL_SNAPSHOT_INCIDENT_TYPE,
        summary=(
            f"{LAB_MARKER} One-shot lab snapshot (BGP + interfaces + config). "
            f"routers_seen={routers_seen} peers_seen={peers_seen} "
            f"established={established} non_established={non_established} "
            f"interfaces_seen={interfaces_seen} "
            f"interfaces_down={interfaces_down} "
            f"interfaces_with_errors={interfaces_with_errors} "
            f"configs_collected={configs_collected} "
            f"errors={len(errors)}."
        ),
    )
    db.add(incident)
    db.flush()

    for event_spec in pending_events:
        db.add(IncidentEvent(incident_id=incident.id, **event_spec))
    for evidence_spec in pending_evidence:
        db.add(IncidentEvidence(incident_id=incident.id, **evidence_spec))

    db.commit()
    db.refresh(incident)

    return LabSnapshotSummary(
        incident_id=incident.id,
        routers_seen=routers_seen,
        peers_seen=peers_seen,
        established_count=established,
        non_established_count=non_established,
        interfaces_seen=interfaces_seen,
        interfaces_with_errors=interfaces_with_errors,
        interfaces_down=interfaces_down,
        configs_collected=configs_collected,
        events_created=len(pending_events),
        evidence_created=len(pending_evidence),
        errors=errors,
    )


# ---------- CLI ----------


# Bounded-loop guardrails. The `--watch` mode is dev-only; the upper bounds
# are deliberately small to make it impossible to accidentally turn this into
# a long-running background ingester.
MAX_ITERATIONS = 100
MAX_INTERVAL_SECONDS = 3600


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.lab.collector",
        description=(
            "Run BGP snapshot collection(s) against the Phase 8B FRR Compose "
            "lab. Read-only; never executes a config change. Two modes: "
            "--collect (one shot) or --watch (a bounded dev loop, max "
            f"{MAX_ITERATIONS} iterations)."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--collect",
        action="store_true",
        help="Run a single BGP-only collection and exit (Phase 8C).",
    )
    mode.add_argument(
        "--collect-snapshot",
        action="store_true",
        help=(
            "Run a single full lab snapshot (BGP + interfaces + running-config) "
            "and exit (Phase 21A)."
        ),
    )
    mode.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Run --iterations BGP-only collections on a fixed interval. Dev-only; "
            "bounded so this cannot turn into a background ingester. "
            "REQUIRES --iterations."
        ),
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help=(
            f"Required with --watch. Number of collections to perform "
            f"(1..{MAX_ITERATIONS})."
        ),
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=10,
        help=(
            f"Seconds to wait between iterations (1..{MAX_INTERVAL_SECONDS}). "
            "Only used with --watch."
        ),
    )
    return parser


def _run_watch(iterations: int, interval_seconds: int) -> int:
    """Dev-only bounded watch loop.

    Behavior on errors:
    - Recoverable per-router scrape failures already surface as
      `errors` inside each iteration's `LabBgpCollectionSummary`. The watch
      loop logs them and keeps going.
    - An UNEXPECTED exception from `collect_lab_bgp_snapshot` (e.g. the DB
      drops out, the LAB_ROUTERS list is malformed) is caught per-iteration,
      logged as a JSON error row to stdout, and the loop continues. This
      makes the loop resilient to a transient blip without hiding it - every
      iteration produces exactly one newline-delimited JSON line on stdout
      so a watcher can `tail -f` or pipe to `jq`.
    """
    for i in range(iterations):
        try:
            with SessionLocal() as db:
                summary = collect_lab_bgp_snapshot(db)
            print(
                json.dumps(summary.model_dump(mode="json")), flush=True
            )
        except Exception as exc:  # noqa: BLE001 - we want resilient loop
            print(
                json.dumps(
                    {
                        "iteration": i + 1,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                ),
                flush=True,
            )
        if i < iterations - 1:
            time.sleep(interval_seconds)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.watch:
        if args.iterations is None:
            parser.error("--watch requires --iterations N")
        if not 1 <= args.iterations <= MAX_ITERATIONS:
            parser.error(
                f"--iterations must be between 1 and {MAX_ITERATIONS}"
            )
        if not 1 <= args.interval_seconds <= MAX_INTERVAL_SECONDS:
            parser.error(
                "--interval-seconds must be between 1 and "
                f"{MAX_INTERVAL_SECONDS}"
            )
        return _run_watch(args.iterations, args.interval_seconds)

    if args.collect_snapshot:
        with SessionLocal() as db:
            snapshot = collect_lab_snapshot(db)
        print(json.dumps(snapshot.model_dump(mode="json"), indent=2))
        return 0

    # --collect (BGP-only, single shot) - kept exactly as before.
    with SessionLocal() as db:
        summary = collect_lab_bgp_snapshot(db)

    print(json.dumps(summary.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
