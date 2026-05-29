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
