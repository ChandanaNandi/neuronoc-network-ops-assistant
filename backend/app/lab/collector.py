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
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Incident, IncidentEvent
from app.db.session import SessionLocal

LAB_MARKER = "[lab-collector]"
LAB_INCIDENT_TYPE = "lab_bgp_collection"

LAB_ROUTERS: list[str] = ["edge-1", "edge-2", "core-1", "branch-1"]


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
    The vtysh command MUST end in ` json` - the collector enforces no config
    operations by only ever passing show-* json variants below.
    """
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
        help="Run a single collection and exit.",
    )
    mode.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Run --iterations collections on a fixed interval. Dev-only; "
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

    # --collect (single shot) - kept exactly as before.
    with SessionLocal() as db:
        summary = collect_lab_bgp_snapshot(db)

    print(json.dumps(summary.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
