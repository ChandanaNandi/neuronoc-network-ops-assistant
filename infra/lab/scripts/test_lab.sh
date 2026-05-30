#!/usr/bin/env bash
# Phase 21D - argument-validation smoke for lab.sh inject/heal.
#
# Runs in <1s with no docker required. Asserts the helper rejects bad input
# loudly and at the right exit code BEFORE shelling out to a real container.
# Actual fault mechanics (which require the lab to be up and BGP to converge,
# ~25-30s) are validated manually per the recipe in infra/lab/README.md.
#
# This script is intentionally not wired into CI yet - it only exists as a
# zero-dependency smoke that a developer can re-run after touching lab.sh
# to make sure no obvious arg-parsing regression slipped in.

set -uo pipefail  # NOT -e - we expect commands to fail; we check exit codes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB="$SCRIPT_DIR/lab.sh"

if [[ ! -x "$LAB" ]]; then
    echo "lab.sh not executable at $LAB" >&2
    exit 1
fi

pass=0
fail=0
failures=()

_record_fail() {
    fail=$((fail + 1))
    failures+=("$1")
    echo "  FAIL: $1" >&2
}

# expect_exit <expected_code> <description> -- <argv...>
expect_exit() {
    local expected="$1" desc="$2"
    shift 2
    [[ "${1:-}" == "--" ]] && shift
    local output rc
    # Capture combined stdout+stderr; ignore it for the assertion but available
    # if the test fails so the developer can see what happened.
    output="$("$LAB" "$@" 2>&1)" && rc=$? || rc=$?
    if [[ "$rc" -eq "$expected" ]]; then
        pass=$((pass + 1))
        echo "  ok ($expected): $desc"
    else
        _record_fail "$desc (expected exit $expected, got $rc)"
        printf '    output: %s\n' "$output" | head -5 >&2
    fi
}

# expect_stderr_contains <pattern> <description> -- <argv...>
expect_stderr_contains() {
    local pattern="$1" desc="$2"
    shift 2
    [[ "${1:-}" == "--" ]] && shift
    local stderr
    stderr="$("$LAB" "$@" 2>&1 >/dev/null)" || true
    if [[ "$stderr" == *"$pattern"* ]]; then
        pass=$((pass + 1))
        echo "  ok (stderr~='$pattern'): $desc"
    else
        _record_fail "$desc (stderr did not contain '$pattern')"
        printf '    stderr: %s\n' "$stderr" | head -3 >&2
    fi
}

echo "== inject: missing fault type =="
expect_exit 2 "inject with no args"                          -- inject
expect_stderr_contains "fault type" "inject with no args mentions fault type" -- inject

echo "== heal: missing fault type =="
expect_exit 2 "heal with no args"                            -- heal
expect_stderr_contains "fault type" "heal with no args mentions fault type"   -- heal

echo "== unknown fault type =="
expect_exit 2 "inject unknown-fault"                         -- inject quantum-tunnel edge-1
expect_stderr_contains "Unknown fault type" "rejects unknown fault" -- inject quantum-tunnel edge-1
expect_exit 2 "heal unknown-fault"                           -- heal quantum-tunnel edge-1

echo "== bgp-down: missing router =="
expect_exit 2 "inject bgp-down without router"               -- inject bgp-down
expect_exit 2 "heal bgp-down without router"                 -- heal bgp-down

echo "== bgp-down: unknown router =="
expect_exit 1 "inject bgp-down rogue-router"                 -- inject bgp-down rogue-router
expect_stderr_contains "Unknown router" "rejects unknown router" -- inject bgp-down rogue-router

echo "== iface-down: missing args =="
expect_exit 2 "inject iface-down without router"             -- inject iface-down
expect_exit 2 "inject iface-down without iface"              -- inject iface-down edge-1
expect_exit 2 "heal iface-down without iface"                -- heal iface-down edge-1
expect_stderr_contains "router" "iface-down arg-error mentions router or iface" -- inject iface-down

echo "== iface-down: unknown router =="
expect_exit 1 "inject iface-down rogue eth0"                 -- inject iface-down rogue eth0

echo "== unknown top-level command =="
expect_exit 2 "unknown top-level command"                    -- not-a-real-command

echo "== help still works after Phase 21D additions =="
expect_exit 0 "no args prints usage"                         --
expect_exit 0 "help command"                                 -- help
# Usage text MUST advertise the new subcommands.
expect_stderr_contains "inject bgp-down" "help mentions inject bgp-down" -- xxx-bad-cmd
expect_stderr_contains "heal" "help mentions heal" -- xxx-bad-cmd
expect_stderr_contains "iface-down" "help mentions iface-down" -- xxx-bad-cmd

# ---------- Fake-docker capture assertions ----------
#
# Argument-validation alone would have missed the original FRR-syntax bug
# (`neighbor X no shutdown` vs the correct `no neighbor X shutdown`).
# Below, we put a fake `docker` script first on PATH so lab.sh's
# `docker exec ...` invocations are captured (one arg per line) into a
# temp file. We then assert the exact vtysh/ip-link command lab.sh emits
# matches the FRR-correct form for each fault.
#
# The fake exits 0 always so lab.sh proceeds without thinking the lab is
# broken. This is still completely offline - no real docker daemon used.

_make_fake_docker_dir() {
    local dir
    dir="$(mktemp -d)"
    cat >"$dir/docker" <<'FAKE_EOF'
#!/usr/bin/env bash
# Capture every arg on its own line so the test can grep -Fx for exact
# whole-line matches. The captured-file path is passed via env var so
# multiple tests can share one fake binary against different log paths.
printf '%s\n' "$@" >> "${LAB_FAKE_DOCKER_LOG:-/dev/null}"
exit 0
FAKE_EOF
    chmod +x "$dir/docker"
    echo "$dir"
}

# expect_capture_contains <log-path> <exact-line> <description>
expect_capture_contains() {
    local log="$1" line="$2" desc="$3"
    if grep -Fxq -- "$line" "$log"; then
        pass=$((pass + 1))
        echo "  ok (captured ~'$line'): $desc"
    else
        _record_fail "$desc — fake-docker log did NOT contain exact line: '$line'"
        echo "    full capture:" >&2
        sed 's/^/      /' "$log" >&2
    fi
}

# expect_capture_missing <log-path> <exact-line> <description>
expect_capture_missing() {
    local log="$1" line="$2" desc="$3"
    if grep -Fxq -- "$line" "$log"; then
        _record_fail "$desc — fake-docker log unexpectedly contained: '$line'"
        echo "    full capture:" >&2
        sed 's/^/      /' "$log" >&2
    else
        pass=$((pass + 1))
        echo "  ok (NOT captured '$line'): $desc"
    fi
}

echo
echo "== fake-docker: bgp-down emits FRR-correct shutdown / no-shutdown =="
fake_dir="$(_make_fake_docker_dir)"

inject_log="$fake_dir/inject.log"
LAB_FAKE_DOCKER_LOG="$inject_log" PATH="$fake_dir:$PATH" "$LAB" \
    inject bgp-down edge-1 >/dev/null 2>&1 || true
expect_capture_contains "$inject_log" \
    "neighbor 172.30.1.2 shutdown" \
    "inject bgp-down edge-1 emits 'neighbor 172.30.1.2 shutdown'"
expect_capture_missing "$inject_log" \
    "no neighbor 172.30.1.2 shutdown" \
    "inject does NOT accidentally emit the heal form"
expect_capture_contains "$inject_log" \
    "router bgp 65011" \
    "inject bgp-down edge-1 uses edge-1's local AS 65011"

heal_log="$fake_dir/heal.log"
LAB_FAKE_DOCKER_LOG="$heal_log" PATH="$fake_dir:$PATH" "$LAB" \
    heal bgp-down edge-1 >/dev/null 2>&1 || true
# The FRR-correct heal syntax is `no neighbor X shutdown`, NOT
# `neighbor X no shutdown`. This is the assertion that catches the bug.
expect_capture_contains "$heal_log" \
    "no neighbor 172.30.1.2 shutdown" \
    "heal bgp-down edge-1 emits the FRR-correct 'no neighbor X shutdown'"
expect_capture_missing "$heal_log" \
    "neighbor 172.30.1.2 no shutdown" \
    "heal does NOT emit the INVALID 'neighbor X no shutdown' form"

# Spot-check core-1's mapping uses its own AS, not edge-1's.
core_log="$fake_dir/core.log"
LAB_FAKE_DOCKER_LOG="$core_log" PATH="$fake_dir:$PATH" "$LAB" \
    inject bgp-down core-1 >/dev/null 2>&1 || true
expect_capture_contains "$core_log" \
    "neighbor 172.30.1.1 shutdown" \
    "inject bgp-down core-1 uses core-1's canonical peer 172.30.1.1"
expect_capture_contains "$core_log" \
    "router bgp 65000" \
    "inject bgp-down core-1 uses core-1's local AS 65000"

echo
echo "== fake-docker: iface-down emits 'ip link set <iface> down/up' =="
iface_log="$fake_dir/iface.log"
LAB_FAKE_DOCKER_LOG="$iface_log" PATH="$fake_dir:$PATH" "$LAB" \
    inject iface-down edge-1 eth0 >/dev/null 2>&1 || true
expect_capture_contains "$iface_log" "set" \
    "inject iface-down emits an 'ip link set' invocation"
expect_capture_contains "$iface_log" "down" \
    "inject iface-down emits the 'down' verb"

iface_heal_log="$fake_dir/iface-heal.log"
LAB_FAKE_DOCKER_LOG="$iface_heal_log" PATH="$fake_dir:$PATH" "$LAB" \
    heal iface-down edge-1 eth0 >/dev/null 2>&1 || true
expect_capture_contains "$iface_heal_log" "up" \
    "heal iface-down emits the 'up' verb"
expect_capture_missing "$iface_heal_log" "down" \
    "heal iface-down does NOT emit the 'down' verb"

# Cleanup the fake-docker tmpdir on exit (also covers early-exit paths).
trap 'rm -rf "$fake_dir"' EXIT

echo
echo "==========================="
echo "  $pass passed, $fail failed"
echo "==========================="
if (( fail > 0 )); then
    for f in "${failures[@]}"; do
        echo "  - $f" >&2
    done
    exit 1
fi
