#!/usr/bin/env bash
# NeuroNOC Phase 8B - FRR mini-lab helper.
#
# Wraps the lab's docker-compose file so you don't have to retype -f every time.
# Pure developer-ergonomics; no destructive commands beyond `down`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$LAB_DIR/docker-compose.lab.yml"

ROUTERS=(edge-1 edge-2 core-1 branch-1)

usage() {
    cat <<'EOF'
Usage:
  lab.sh up              # bring the lab up in the background
  lab.sh down            # tear the lab down (containers + networks, NO volumes)
  lab.sh ps              # show lab container status
  lab.sh logs [router]   # tail logs for one router or all
  lab.sh cli <router>    # interactive vtysh on a router
  lab.sh bgp <router>    # 'show ip bgp summary' on a router (or 'all')

Routers: edge-1, edge-2, core-1, branch-1
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

_container() {
    echo "neuronoc-lab-$1"
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
