#!/usr/bin/env bash
# NeuroNOC Phase 8B - FRR mini-lab helper.
#
# Wraps the lab's docker-compose file so you don't have to retype -f every time.
# Phase 21D adds `inject` + `heal` subcommands for the demo helper: reliable,
# idempotent BGP-down and interface-down faults. Lab-only by construction -
# every command targets the existing `neuronoc-lab-*` containers via docker
# exec; no host packet tricks, no privileged kernel knobs.
#
# Pure developer-ergonomics; no destructive commands beyond `down`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$LAB_DIR/docker-compose.lab.yml"

ROUTERS=(edge-1 edge-2 core-1 branch-1)

# Phase 21D: canonical BGP peer per router for the `bgp-down` fault. Hardcoded
# against the Phase 8B topology (see infra/lab/README.md). The peer choice
# matters because we want the demo to produce visible state change WITHOUT
# tearing down the whole router (so the Phase 21A collector still scrapes
# every node successfully and shows a partial-fault story).
#
# Implemented as a case-lookup function rather than associative arrays so
# the script runs on stock macOS bash 3.2 (no `declare -A`).
_canonical_bgp_peer() {
    case "$1" in
        edge-1)   echo "172.30.1.2" ;;
        edge-2)   echo "172.30.2.2" ;;
        core-1)   echo "172.30.1.1" ;;
        branch-1) echo "172.30.3.2" ;;
        *)        return 1 ;;
    esac
}

_local_as() {
    case "$1" in
        edge-1)   echo "65011" ;;
        edge-2)   echo "65012" ;;
        core-1)   echo "65000" ;;
        branch-1) echo "65031" ;;
        *)        return 1 ;;
    esac
}

# Phase 21D injectable fault types (top-level subcommands `inject` / `heal`).
FAULT_TYPES=(bgp-down iface-down)

usage() {
    cat <<'EOF'
Usage:
  lab.sh up                          # bring the lab up in the background
  lab.sh down                        # tear the lab down (containers + networks, NO volumes)
  lab.sh ps                          # show lab container status
  lab.sh logs [router]               # tail logs for one router or all
  lab.sh cli <router>                # interactive vtysh on a router
  lab.sh bgp <router|all>            # 'show ip bgp summary' on a router or all

Phase 21D demo helper (lab-only, idempotent):
  lab.sh inject bgp-down <router>            # shut canonical BGP neighbor
  lab.sh heal   bgp-down <router>            # un-shut it
  lab.sh inject iface-down <router> <iface>  # ip link set <iface> down
  lab.sh heal   iface-down <router> <iface>  # ip link set <iface> up

Routers: edge-1, edge-2, core-1, branch-1
Faults:  bgp-down, iface-down
        (iface-errors deferred - see infra/lab/README.md for rationale)

Post-fault verification: `lab.sh bgp all` shows per-router BGP state.
BGP convergence after `heal` takes ~30s (hold timer).
EOF
}

_valid_router() {
    local r="$1"
    for valid in "${ROUTERS[@]}"; do
        [[ "$r" == "$valid" ]] && return 0
    done
    echo "Unknown router: $r" >&2
    echo "Valid routers: ${ROUTERS[*]}" >&2
    return 1
}

_valid_fault() {
    local f="$1"
    for valid in "${FAULT_TYPES[@]}"; do
        [[ "$f" == "$valid" ]] && return 0
    done
    echo "Unknown fault type: $f" >&2
    echo "Valid fault types: ${FAULT_TYPES[*]}" >&2
    return 2
}

_container() {
    echo "neuronoc-lab-$1"
}

# Phase 21D: shared body for inject/heal of bgp-down. `mode` is either
# "shutdown" (inject) or "no-shutdown" (heal). FRR's `no` operator wraps
# the entire command (the correct heal syntax is `no neighbor X shutdown`,
# NOT `neighbor X no shutdown`), so we build the literal vtysh command
# per mode rather than templating the `no` keyword. FRR accepts both as
# no-ops when already in the requested state, so this is naturally
# idempotent.
_bgp_neighbor_mode() {
    local router="$1" mode="$2"
    _valid_router "$router"
    local peer local_as
    peer="$(_canonical_bgp_peer "$router")" || {
        echo "No canonical BGP peer mapped for router $router" >&2
        return 1
    }
    local_as="$(_local_as "$router")" || {
        echo "No local AS mapped for router $router" >&2
        return 1
    }
    local vtysh_cmd
    case "$mode" in
        shutdown)    vtysh_cmd="neighbor $peer shutdown" ;;
        no-shutdown) vtysh_cmd="no neighbor $peer shutdown" ;;
        *)
            echo "internal error: unknown bgp mode '$mode'" >&2
            return 1
            ;;
    esac
    echo "[lab] $router AS$local_as: $vtysh_cmd"
    docker exec "$(_container "$router")" vtysh \
        -c "configure terminal" \
        -c "router bgp $local_as" \
        -c "$vtysh_cmd" \
        -c "end" >/dev/null
}

_iface_mode() {
    local router="$1" iface="$2" updown="$3"
    _valid_router "$router"
    if [[ -z "$iface" ]]; then
        echo "iface-down requires an interface name (try eth0/eth1)" >&2
        return 2
    fi
    # Pre-check so a typo'd interface name fails fast with a readable error
    # instead of leaving the lab half-touched.
    if ! docker exec "$(_container "$router")" ip link show "$iface" \
            >/dev/null 2>&1; then
        echo "Interface $iface not found on $router" >&2
        echo "Available interfaces:" >&2
        docker exec "$(_container "$router")" ip -o link show \
            | awk -F': ' '{print "  " $2}' >&2 || true
        return 1
    fi
    echo "[lab] $router: ip link set $iface $updown"
    docker exec "$(_container "$router")" ip link set "$iface" "$updown"
}

_inject() {
    local fault="${1:-}"
    shift || true
    if [[ -z "$fault" ]]; then
        echo "inject requires a fault type" >&2
        echo "Valid fault types: ${FAULT_TYPES[*]}" >&2
        return 2
    fi
    _valid_fault "$fault"
    case "$fault" in
        bgp-down)
            [[ $# -ge 1 ]] || { echo "inject bgp-down requires a router" >&2; return 2; }
            _bgp_neighbor_mode "$1" "shutdown"
            ;;
        iface-down)
            [[ $# -ge 2 ]] || { echo "inject iface-down requires <router> <iface>" >&2; return 2; }
            _iface_mode "$1" "$2" "down"
            ;;
    esac
}

_heal() {
    local fault="${1:-}"
    shift || true
    if [[ -z "$fault" ]]; then
        echo "heal requires a fault type" >&2
        echo "Valid fault types: ${FAULT_TYPES[*]}" >&2
        return 2
    fi
    _valid_fault "$fault"
    case "$fault" in
        bgp-down)
            [[ $# -ge 1 ]] || { echo "heal bgp-down requires a router" >&2; return 2; }
            _bgp_neighbor_mode "$1" "no-shutdown"
            ;;
        iface-down)
            [[ $# -ge 2 ]] || { echo "heal iface-down requires <router> <iface>" >&2; return 2; }
            _iface_mode "$1" "$2" "up"
            ;;
    esac
}

cmd="${1:-}"
shift || true

case "$cmd" in
    up)
        docker compose -f "$COMPOSE_FILE" up -d "$@"
        ;;
    down)
        docker compose -f "$COMPOSE_FILE" down "$@"
        ;;
    ps)
        docker compose -f "$COMPOSE_FILE" ps "$@"
        ;;
    logs)
        if [ $# -gt 0 ]; then
            _valid_router "$1"
            docker compose -f "$COMPOSE_FILE" logs --tail=200 -f "$1"
        else
            docker compose -f "$COMPOSE_FILE" logs --tail=100 -f
        fi
        ;;
    cli)
        [ $# -eq 1 ] || { usage >&2; exit 2; }
        _valid_router "$1"
        docker exec -it "$(_container "$1")" vtysh
        ;;
    bgp)
        [ $# -eq 1 ] || { usage >&2; exit 2; }
        if [ "$1" = "all" ]; then
            for r in "${ROUTERS[@]}"; do
                echo "===== $r ====="
                docker exec "$(_container "$r")" vtysh -c "show ip bgp summary" || true
                echo
            done
        else
            _valid_router "$1"
            docker exec "$(_container "$1")" vtysh -c "show ip bgp summary"
        fi
        ;;
    inject)
        _inject "$@"
        ;;
    heal)
        _heal "$@"
        ;;
    ""|-h|--help|help)
        usage
        ;;
    *)
        echo "Unknown command: $cmd" >&2
        echo >&2
        usage >&2
        exit 2
        ;;
esac
