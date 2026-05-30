# NeuroNOC FRR mini-lab (Phase 8B)

A small Docker Compose stack of four FRR v8.4.1 routers running eBGP. The stack is **independent** of the main NeuroNOC backend / Postgres / Ollama and can be brought up and down without affecting them.

> **Phase 8B scope:** topology + BGP only. This lab is **not yet wired into the NeuroNOC backend.** Collector / telemetry integration lands in Phase 8C.

## Topology

```
   edge-1 (AS 65011)              edge-2 (AS 65012)
      |  172.30.1.0/29               |  172.30.2.0/29
      |  .1            .2            |  .1            .2
      +--------- core-1 ---------+   +--------- core-1
                 (AS 65000)
                  ^
                  | branch-1 (AS 65031)
                  | 172.30.3.0/29
                  | .2            .1
                  +------------- branch-1
```

Three eBGP point-to-point links, all terminating on `core-1`:

| Link | Subnet | edge-side IP | core-1 IP |
|---|---|---|---|
| edge-1 ↔ core-1 | 172.30.1.0/29 | edge-1 = 172.30.1.1 | core-1 = 172.30.1.2 |
| edge-2 ↔ core-1 | 172.30.2.0/29 | edge-2 = 172.30.2.1 | core-1 = 172.30.2.2 |
| branch-1 ↔ core-1 | 172.30.3.0/29 | branch-1 = 172.30.3.1 | core-1 = 172.30.3.2 |

Each link is a /29 instead of a /30 to leave Docker's bridge gateway out of our way; the gateway is parked at `.6` of each subnet so `.1` and `.2` are free for the routers.

Loopbacks (advertised into BGP):

| Router | Loopback | AS |
|---|---|---|
| edge-1 | 10.0.0.11/32 | 65011 |
| edge-2 | 10.0.0.12/32 | 65012 |
| core-1 | 10.0.0.21/32 | 65000 |
| branch-1 | 10.0.0.31/32 | 65031 |

Hostnames + loopback IPs intentionally match `app/simulator/scenarios.py::DEVICE_SPECS` so a future collector can correlate live BGP state against simulated incidents without remapping.

Docker networks (dedicated `neuronoc_lab_*` prefix to avoid collision with anything else):

- `neuronoc_lab_edge1_core1`
- `neuronoc_lab_edge2_core1`
- `neuronoc_lab_branch1_core1`

Containers:

- `neuronoc-lab-edge-1`
- `neuronoc-lab-edge-2`
- `neuronoc-lab-core-1`
- `neuronoc-lab-branch-1`

## Prerequisites

- Docker Desktop running.
- `frrouting/frr:v8.4.1` image cached locally (already present per Phase 8A audit; this lab does not pull a new FRR version).
- Recommended Docker Desktop memory ≥ 12 GB if the lab will run alongside `neuronoc-postgres`, Ollama, and dev servers.

Every FRR service in the compose file sets `pull_policy: never`. If the image is missing locally, `compose up` will fail fast with a clear error rather than silently pulling 200+ MB. Restore the image by `docker pull frrouting/frr:v8.4.1` (one-off, explicit) before re-running the lab.

## Bring it up

From the project root:

```bash
./infra/lab/scripts/lab.sh up
./infra/lab/scripts/lab.sh ps
```

Or with raw compose:

```bash
docker compose -f infra/lab/docker-compose.lab.yml up -d
docker compose -f infra/lab/docker-compose.lab.yml ps
```

Healthchecks verify BGP convergence:

- edge-1, edge-2, branch-1 each require ≥ 1 Established session.
- core-1 requires all 3 Established sessions before being marked healthy.
- `start_period` is 25 s; convergence usually completes well within that.

## Verify BGP

```bash
./infra/lab/scripts/lab.sh bgp core-1     # all three neighbours should show a numeric pfx count
./infra/lab/scripts/lab.sh bgp all        # all four routers in sequence
./infra/lab/scripts/lab.sh cli edge-1     # interactive vtysh; try 'show ip route bgp'
```

`show ip bgp summary` reports a numeric value in the `State/PfxRcd` column when the session is **Established** (and the word `Active` / `Idle` / `Connect` otherwise).

## Shut it down

```bash
./infra/lab/scripts/lab.sh down
```

This removes the lab containers and the three `neuronoc_lab_*` networks. No volumes are used by this stack, so nothing persists. `neuronoc-postgres` (Phase 1+) is untouched.

## Helper script reference

`infra/lab/scripts/lab.sh` is pure dev ergonomics — it just wraps `docker compose -f infra/lab/docker-compose.lab.yml`:

| Command | What it does |
|---|---|
| `lab.sh up` | `docker compose up -d` for the lab |
| `lab.sh down` | `docker compose down` for the lab (no volumes touched) |
| `lab.sh ps` | container status |
| `lab.sh logs [router]` | tail logs (one router or all) |
| `lab.sh cli <router>` | interactive `vtysh` |
| `lab.sh bgp <router>` | `show ip bgp summary` on one router or `all` |
| `lab.sh inject bgp-down <router>` | **Phase 21D**: shut canonical BGP neighbor on `<router>` |
| `lab.sh heal bgp-down <router>` | **Phase 21D**: un-shut the same neighbor |
| `lab.sh inject iface-down <router> <iface>` | **Phase 21D**: `ip link set <iface> down` inside the container |
| `lab.sh heal iface-down <router> <iface>` | **Phase 21D**: `ip link set <iface> up` |

## Fault injection (demo helper, Phase 21D)

The recruiter-demo path (see `backend/README.md` → "Demo path") needs a reliable way to break the lab into a state the Phase 21A `Collect lab snapshot` button can observe. `lab.sh inject` / `lab.sh heal` are that helper — they only target the existing `neuronoc-lab-*` containers via `docker exec`, never touch the host network stack, and are naturally idempotent (re-running an inject or a heal is a safe no-op).

### Fault matrix

| Fault | Mechanism | Heal | Effect on collector |
|---|---|---|---|
| `bgp-down <router>` | `vtysh -c "configure terminal" -c "router bgp <AS>" -c "neighbor <CANONICAL_PEER> shutdown"` inside the router | symmetric `no shutdown` | Phase 21A collector sees `state != Established` for one peer → emits `lab_bgp_peer_not_established` → Phase 21C R001 fires `bgp_neighbor_down_detected` |
| `iface-down <router> <iface>` | `ip link set <iface> down` inside the container (works because the FRR images carry `NET_ADMIN`) | `ip link set <iface> up` | Phase 21A collector reads `oper_status=down` on the interface → emits `lab_interface_status` with `payload.down=True` → Phase 21C R008 fires `link_down_detected`. Any BGP session traversing that link will also flap. |

### Canonical BGP peer per router

`inject bgp-down <router>` always targets the same peer per router so the demo is reproducible. Mapped to a peer that produces a partial-fault state (the other peers on the router stay up so the collector still scrapes the node successfully):

| Router | Canonical neighbor | The other end |
|---|---|---|
| `edge-1` | `172.30.1.2` | core-1 |
| `edge-2` | `172.30.2.2` | core-1 |
| `core-1` | `172.30.1.1` | edge-1 |
| `branch-1` | `172.30.3.2` | core-1 |

If you need a different peer, drop into `lab.sh cli <router>` and run vtysh manually — the helper is intentionally narrow.

### Manual validation recipe

`lab.sh` injects produce instant state changes (no convergence wait), but the lab's BGP hold timers need ~30 s to fully reconverge after a `heal`. Run from project root:

```bash
./infra/lab/scripts/lab.sh up
./infra/lab/scripts/lab.sh bgp all                # baseline: every session Established

./infra/lab/scripts/lab.sh inject bgp-down edge-1
./infra/lab/scripts/lab.sh bgp all                # edge-1 <-> core-1 now Idle/Active

# Optional: hit "Collect lab snapshot" in the operator console; you should
# see a lab_bgp_peer_not_established event + bgp_neighbor_down_detected
# finding + plan_type=bgp_neighbor_recovery.

./infra/lab/scripts/lab.sh heal bgp-down edge-1
sleep 35                                            # let BGP hold-timer reconverge
./infra/lab/scripts/lab.sh bgp all                # back to Established
```

The same pattern works for `iface-down` — pre-check available interfaces with `lab.sh cli <router>` then `show interface brief` if you're not sure which name docker assigned.

### Why `iface-errors` is deferred

A `tc qdisc add ... netem corrupt N%` approach would work in principle (the FRR images have `NET_ADMIN`), but in practice for this lab:

- BGP keepalives are tiny (~80 bytes) so even modest corrupt percentages tear the session down — which conflates with `bgp-down`, hiding the "interface errors" signal you'd want to demo.
- The `tc` toolchain isn't always present in the FRR base image; adding a host-side wrapper would couple the helper to package versions.
- The Phase 21C anomaly engine already exercises the `interface_error_spike_detected` path via the lab collector's `has_errors` payload flag, but generating that flag reliably from a synthetic fault requires sustained traffic plus stable error generation — fragile for a demo.

`iface-down` already produces BOTH a Phase 21C `link_down_detected` finding AND knocks down any BGP session on that link, so it's a richer single-command demo than `bgp-down` alone — that's the lever to reach for if you want a "mixed-fault" story in one click.

### Argument validation smoke

`infra/lab/scripts/test_lab.sh` runs in <1 s with no docker dependency. Asserts the helper rejects bad input loudly (unknown router, unknown fault type, missing args) before shelling out to a real container. Re-run after touching `lab.sh`:

```bash
./infra/lab/scripts/test_lab.sh
```

33 assertions; exit 0 on success. Half are argument-validation (unknown router / fault type / missing args); half are fake-docker capture assertions that put a stub `docker` script first on `PATH` and pin the exact vtysh / `ip link` command string lab.sh emits per fault, including a load-bearing assertion that `heal bgp-down` emits the FRR-correct `no neighbor X shutdown` (not the syntactically-invalid `neighbor X no shutdown`).

## RFC 8212 / `no bgp ebgp-requires-policy`

FRR 8.x defaults to RFC 8212 strict policy compliance: an eBGP session without explicit inbound *and* outbound policy silently discards every update. Production routers should always carry such policies. **For this lab** each router includes `no bgp ebgp-requires-policy` so prefixes flow without us needing to maintain prefix-lists / route-maps for what is a four-router demo. A real Phase 7 remediation plan against a production peer would still recommend the inverse (keep RFC 8212 on, add explicit policy).

## Healthcheck limitation

We grep the JSON output of `vtysh -c 'show ip bgp summary json'` for `"state":"Established"`. This works on FRR 8.x's standard output but assumes:

- `vtysh` is available inside the container (true for the official image).
- The JSON shape includes a `state` field per peer (true for v8.4).

If a future FRR version changes the JSON shape, healthchecks will fail closed (containers stay marked `unhealthy`) rather than passing incorrectly — that's intentional. Swap the grep for `jq` or a small Python check at that point.

## What this lab is NOT (yet)

- It is **not** wired into the NeuroNOC backend. The collector that scrapes `show bgp summary` and feeds it into `IncidentEvent` rows lands in Phase 8C.
- It does not run any remediation against itself. Phase 7 plans remain plan-only.
- It uses bridges only — no veth pairs, no L2 trunking, no EVPN. If we ever need those, the audit calls for switching to Lima + Containerlab (Phase 8A Option B).
