"""Deterministic simulator scenarios.

Every Incident created by the simulator has its `summary` field prefixed with
`SIMULATOR_MARKER` so it can be unambiguously identified and removed by
`reset_simulator_data` without touching real (operator-created) incidents.
"""

from __future__ import annotations

from typing import Any

SIMULATOR_MARKER = "[simulator]"
SIMULATOR_ORIGIN = "simulator"


def _src(device: str) -> str:
    """Source tag used on simulator events/evidence."""
    return f"simulator:{device}"


def _payload(**kwargs: Any) -> dict[str, Any]:
    """Tag every simulator JSONB payload with `_origin` for future filtering."""
    base: dict[str, Any] = {"_origin": SIMULATOR_ORIGIN}
    base.update(kwargs)
    return base


# Devices the simulator owns. Hostnames are the idempotency key.
DEVICE_SPECS: list[dict[str, str]] = [
    {
        "hostname": "edge-1",
        "role": "edge",
        "management_ip": "10.0.0.11",
        "vendor": "frr",
        "platform": "frrouting",
    },
    {
        "hostname": "edge-2",
        "role": "edge",
        "management_ip": "10.0.0.12",
        "vendor": "frr",
        "platform": "frrouting",
    },
    {
        "hostname": "core-1",
        "role": "core",
        "management_ip": "10.0.0.21",
        "vendor": "frr",
        "platform": "frrouting",
    },
    {
        "hostname": "branch-1",
        "role": "branch",
        "management_ip": "10.0.0.31",
        "vendor": "frr",
        "platform": "frrouting",
    },
]


# Each scenario produces exactly one Incident plus N events, N evidence, 1 recommendation.
# Timestamps in payloads are deterministic strings; DB-side created_at varies per run.
SCENARIOS: dict[str, dict[str, Any]] = {
    "bgp_neighbor_down": {
        "incident": {
            "title": "BGP neighbor 10.0.0.21 down from edge-1",
            "severity": "critical",
            "incident_type": "bgp_neighbor_down",
            "summary": (
                f"{SIMULATOR_MARKER} BGP session edge-1 -> core-1 (10.0.0.21, AS 65010) "
                "transitioned Established -> Idle; 142 prefixes withdrawn; downstream "
                "reachability lost to 198.51.100.0/24."
            ),
        },
        "events": [
            {
                "event_type": "bgp_state_change",
                "source": _src("edge-1"),
                "message": "BGP neighbor 10.0.0.21 (AS 65010): Established -> Idle",
                "payload": _payload(
                    device="edge-1",
                    neighbor="10.0.0.21",
                    neighbor_as=65010,
                    before="Established",
                    after="Idle",
                    observed_at="2026-05-28T19:30:00Z",
                ),
            },
            {
                "event_type": "route_withdrawal",
                "source": _src("edge-1"),
                "message": "BGP neighbor 10.0.0.21 withdrew 142 prefixes",
                "payload": _payload(
                    device="edge-1",
                    neighbor="10.0.0.21",
                    metric_name="withdrawn_prefixes",
                    metric_value=142,
                    unit="count",
                    observed_at="2026-05-28T19:30:05Z",
                ),
            },
            {
                "event_type": "reachability_loss",
                "source": _src("core-1"),
                "message": (
                    "Loss of reachability to 198.51.100.0/24 (previously reachable via edge-1)"
                ),
                "payload": _payload(
                    device="core-1",
                    prefix="198.51.100.0/24",
                    before="reachable",
                    after="unreachable",
                    observed_at="2026-05-28T19:30:10Z",
                ),
            },
        ],
        "evidence": [
            {
                "evidence_type": "command_output",
                "source": _src("edge-1"),
                "content": (
                    "edge-1# show bgp summary\n"
                    "BGP router identifier 10.0.0.11, local AS number 65000\n"
                    "Neighbor     V    AS   MsgRcvd  MsgSent  Up/Down  State/PfxRcd\n"
                    "10.0.0.21    4 65010        0        0 00:00:12   Idle"
                ),
                "payload": _payload(
                    device="edge-1",
                    command="show bgp summary",
                ),
            },
            {
                "evidence_type": "interface_counters",
                "source": _src("edge-1"),
                "content": (
                    "edge-1# show interface eth0\n"
                    "eth0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>\n"
                    "  rx_packets 1842137  tx_packets 1923004\n"
                    "  input_errors 0  output_errors 0  link up"
                ),
                "payload": _payload(
                    device="edge-1",
                    interface="eth0",
                    link="up",
                    input_errors=0,
                    output_errors=0,
                ),
            },
            {
                "evidence_type": "route_table_excerpt",
                "source": _src("core-1"),
                "content": (
                    "core-1# show ip route 198.51.100.0/24\n"
                    "% Network not in table"
                ),
                "payload": _payload(
                    device="core-1",
                    prefix="198.51.100.0/24",
                    result="not_in_table",
                ),
            },
        ],
        "recommendation": {
            "recommendation_type": "ansible_playbook",
            "title": "Validate link state and BGP neighbor config on edge-1 ↔ core-1",
            "details": (
                f"{SIMULATOR_MARKER} 1) Verify L1 on edge-1<->core-1 (carrier, CRC). "
                "2) Diff BGP neighbor config (remote-as, password, timers, update-source) "
                "on both ends. 3) Only after L1 and config are confirmed clean, perform "
                "'clear ip bgp 10.0.0.21 soft in' inside a change window. Capture before/after "
                "'show bgp summary' as evidence."
            ),
            "risk": "medium",
            "requires_approval": True,
        },
    },
    "interface_errors_spike": {
        "incident": {
            "title": "Input error rate climbing on edge-2 eth1",
            "severity": "high",
            "incident_type": "interface_errors_spike",
            "summary": (
                f"{SIMULATOR_MARKER} edge-2 eth1 input CRC errors rose from "
                "0/min to 412/min over 5 minutes with corresponding packet drops."
            ),
        },
        "events": [
            {
                "event_type": "metric_threshold_breach",
                "source": _src("edge-2"),
                "message": "edge-2 eth1: input_errors rate 412/min (threshold 50/min)",
                "payload": _payload(
                    device="edge-2",
                    interface="eth1",
                    metric_name="input_errors_per_min",
                    metric_value=412,
                    unit="count_per_min",
                    before=0,
                    after=412,
                    observed_at="2026-05-28T19:34:00Z",
                ),
            },
            {
                "event_type": "packet_drop",
                "source": _src("edge-2"),
                "message": "edge-2 eth1: rx_dropped rising (87/s)",
                "payload": _payload(
                    device="edge-2",
                    interface="eth1",
                    metric_name="rx_dropped_per_sec",
                    metric_value=87,
                    unit="count_per_sec",
                    observed_at="2026-05-28T19:34:05Z",
                ),
            },
        ],
        "evidence": [
            {
                "evidence_type": "interface_counters",
                "source": _src("edge-2"),
                "content": (
                    "edge-2# show interface eth1\n"
                    "eth1: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>\n"
                    "  rx_packets 9821330  tx_packets 9620441\n"
                    "  input_errors 2058  output_errors 0  rx_dropped 411  link up"
                ),
                "payload": _payload(
                    device="edge-2",
                    interface="eth1",
                    link="up",
                    input_errors=2058,
                    rx_dropped=411,
                ),
            },
            {
                "evidence_type": "optics_diagnostics",
                "source": _src("edge-2"),
                "content": (
                    "edge-2# show interface eth1 transceiver\n"
                    "  TX power: -2.1 dBm  RX power: -14.8 dBm (low; spec -12.0 dBm)\n"
                    "  temperature: 51 C"
                ),
                "payload": _payload(
                    device="edge-2",
                    interface="eth1",
                    rx_power_dbm=-14.8,
                    rx_power_spec_dbm=-12.0,
                    rx_power_below_spec=True,
                ),
            },
        ],
        "recommendation": {
            "recommendation_type": "manual_inspection",
            "title": "Inspect edge-2 eth1 optics and patch; shift traffic if errors persist",
            "details": (
                f"{SIMULATOR_MARKER} 1) Inspect/clean optics on edge-2 eth1; RX power is "
                "below spec (-14.8 vs -12.0 dBm). 2) Check the fiber/patch panel run. "
                "3) If error rate stays > 50/min after a clean reseat, drain traffic from "
                "eth1 via routing policy and replace the SFP."
            ),
            "risk": "low",
            "requires_approval": True,
        },
    },
    "latency_spike": {
        "incident": {
            "title": "Latency spike branch-1 → core-1 (12ms → 180ms, 4% loss)",
            "severity": "medium",
            "incident_type": "latency_spike",
            "summary": (
                f"{SIMULATOR_MARKER} RTT on branch-1 -> core-1 jumped from a 12 ms baseline "
                "to 180 ms with 4% packet loss; consistent with congestion or downstream queue drops."
            ),
        },
        "events": [
            {
                "event_type": "latency_breach",
                "source": _src("branch-1"),
                "message": "RTT branch-1 -> core-1 (10.0.0.21): 180 ms (baseline 12 ms)",
                "payload": _payload(
                    device="branch-1",
                    target="10.0.0.21",
                    metric_name="rtt_ms",
                    metric_value=180,
                    unit="ms",
                    before=12,
                    after=180,
                    observed_at="2026-05-28T19:36:00Z",
                ),
            },
            {
                "event_type": "packet_loss",
                "source": _src("branch-1"),
                "message": "Loss to core-1 (10.0.0.21): 4.0%",
                "payload": _payload(
                    device="branch-1",
                    target="10.0.0.21",
                    metric_name="loss_pct",
                    metric_value=4.0,
                    unit="percent",
                    observed_at="2026-05-28T19:36:05Z",
                ),
            },
        ],
        "evidence": [
            {
                "evidence_type": "command_output",
                "source": _src("branch-1"),
                "content": (
                    "branch-1# ping -c 50 10.0.0.21\n"
                    "50 packets transmitted, 48 received, 4% packet loss\n"
                    "rtt min/avg/max/mdev = 142.110/180.341/241.117/22.110 ms"
                ),
                "payload": _payload(
                    device="branch-1",
                    command="ping -c 50 10.0.0.21",
                    rtt_avg_ms=180.34,
                    loss_pct=4.0,
                ),
            },
            {
                "evidence_type": "command_output",
                "source": _src("branch-1"),
                "content": (
                    "branch-1# traceroute 10.0.0.21\n"
                    " 1  10.0.0.30  0.6 ms\n"
                    " 2  10.0.0.41  178.2 ms\n"
                    " 3  10.0.0.21  179.1 ms"
                ),
                "payload": _payload(
                    device="branch-1",
                    command="traceroute 10.0.0.21",
                    slow_hop="10.0.0.41",
                    slow_hop_rtt_ms=178.2,
                ),
            },
        ],
        "recommendation": {
            "recommendation_type": "manual_inspection",
            "title": "Investigate 10.0.0.41 hop for congestion or queue drops",
            "details": (
                f"{SIMULATOR_MARKER} Traceroute points to a single slow hop at 10.0.0.41. "
                "1) Pull queue/drop counters on that node's upstream interface. "
                "2) Confirm whether a scheduled job or backup is saturating the link. "
                "3) If congestion is structural, propose CoS / shaper changes (separate ticket)."
            ),
            "risk": "low",
            "requires_approval": True,
        },
    },
    "route_missing": {
        "incident": {
            "title": "Prefix 10.42.0.0/16 missing from core-1",
            "severity": "high",
            "incident_type": "route_missing",
            "summary": (
                f"{SIMULATOR_MARKER} 10.42.0.0/16 (app subnet) is no longer present in "
                "core-1's routing table; edge-1 was the expected advertiser. Likely a "
                "stale outbound policy filter on edge-1 is dropping the announcement."
            ),
        },
        "events": [
            {
                "event_type": "route_missing",
                "source": _src("core-1"),
                "message": "Prefix 10.42.0.0/16 absent from core-1 route table",
                "payload": _payload(
                    device="core-1",
                    prefix="10.42.0.0/16",
                    before="present",
                    after="absent",
                    expected_via="edge-1",
                    observed_at="2026-05-28T19:38:00Z",
                ),
            },
            {
                "event_type": "policy_filter_detected",
                "source": _src("edge-1"),
                "message": (
                    "Outbound prefix-list APP-OUT on edge-1 -> core-1 missing entry "
                    "for 10.42.0.0/16"
                ),
                "payload": _payload(
                    device="edge-1",
                    neighbor="10.0.0.21",
                    policy="APP-OUT",
                    missing_prefix="10.42.0.0/16",
                    observed_at="2026-05-28T19:38:10Z",
                ),
            },
        ],
        "evidence": [
            {
                "evidence_type": "route_table_excerpt",
                "source": _src("core-1"),
                "content": (
                    "core-1# show ip route 10.42.0.0/16\n"
                    "% Network not in table"
                ),
                "payload": _payload(
                    device="core-1",
                    prefix="10.42.0.0/16",
                    result="not_in_table",
                ),
            },
            {
                "evidence_type": "policy_excerpt",
                "source": _src("edge-1"),
                "content": (
                    "edge-1# show ip prefix-list APP-OUT\n"
                    "ip prefix-list APP-OUT seq 5 permit 10.40.0.0/16\n"
                    "ip prefix-list APP-OUT seq 10 permit 10.41.0.0/16\n"
                    "ip prefix-list APP-OUT seq 99 deny 0.0.0.0/0 le 32"
                ),
                "payload": _payload(
                    device="edge-1",
                    policy="APP-OUT",
                    permitted=["10.40.0.0/16", "10.41.0.0/16"],
                    catchall_deny=True,
                ),
            },
        ],
        "recommendation": {
            "recommendation_type": "ansible_playbook",
            "title": "Validate APP-OUT prefix-list on edge-1 and add 10.42.0.0/16 if intended",
            "details": (
                f"{SIMULATOR_MARKER} 1) Confirm with the application team that 10.42.0.0/16 "
                "should be advertised. 2) If yes, add 'ip prefix-list APP-OUT seq 15 permit "
                "10.42.0.0/16' on edge-1 and verify the announcement reaches core-1 with "
                "'show bgp neighbors 10.0.0.21 advertised-routes'. 3) Roll back if the "
                "before/after diff shows any unintended additions."
            ),
            "risk": "medium",
            "requires_approval": True,
        },
    },
    "acl_blocking_traffic": {
        "incident": {
            "title": "ACL denying branch-1 → app subnet after policy change",
            "severity": "medium",
            "incident_type": "acl_blocking_traffic",
            "summary": (
                f"{SIMULATOR_MARKER} After today's 19:40Z policy push, traffic from "
                "branch-1 (10.0.0.31) to the app subnet (10.42.10.0/24) is denied at "
                "core-1; reachability checks fail at the firewall hop."
            ),
        },
        "events": [
            {
                "event_type": "policy_change",
                "source": _src("core-1"),
                "message": (
                    "ACL CORE-INGRESS revised at 19:40Z; rule 'permit 10.0.0.0/24 -> "
                    "10.42.10.0/24' removed"
                ),
                "payload": _payload(
                    device="core-1",
                    policy="CORE-INGRESS",
                    change="rule_removed",
                    before="permit 10.0.0.0/24 10.42.10.0/24",
                    after="absent",
                    observed_at="2026-05-28T19:40:00Z",
                ),
            },
            {
                "event_type": "traffic_denied",
                "source": _src("core-1"),
                "message": (
                    "ACL CORE-INGRESS deny hit: src=10.0.0.31 dst=10.42.10.42 count=137"
                ),
                "payload": _payload(
                    device="core-1",
                    policy="CORE-INGRESS",
                    src="10.0.0.31",
                    dst="10.42.10.42",
                    metric_name="acl_deny_hits",
                    metric_value=137,
                    unit="count",
                    observed_at="2026-05-28T19:40:30Z",
                ),
            },
        ],
        "evidence": [
            {
                "evidence_type": "policy_excerpt",
                "source": _src("core-1"),
                "content": (
                    "core-1# show access-list CORE-INGRESS\n"
                    "access-list CORE-INGRESS\n"
                    "  10 permit ip 10.0.0.0/24 10.40.0.0/16\n"
                    "  20 permit ip 10.0.0.0/24 10.41.0.0/16\n"
                    "  99 deny ip any any log"
                ),
                "payload": _payload(
                    device="core-1",
                    policy="CORE-INGRESS",
                    rules=[
                        {"seq": 10, "action": "permit", "dst": "10.40.0.0/16"},
                        {"seq": 20, "action": "permit", "dst": "10.41.0.0/16"},
                        {"seq": 99, "action": "deny", "dst": "any"},
                    ],
                ),
            },
            {
                "evidence_type": "reachability_check",
                "source": _src("branch-1"),
                "content": (
                    "branch-1# curl -sS --max-time 3 http://10.42.10.42/health\n"
                    "curl: (28) Connection timed out after 3000 ms"
                ),
                "payload": _payload(
                    device="branch-1",
                    target="10.42.10.42",
                    result="timeout",
                    observed_at="2026-05-28T19:41:00Z",
                ),
            },
        ],
        "recommendation": {
            "recommendation_type": "ansible_playbook",
            "title": "Review CORE-INGRESS policy diff; reinstate explicit permit if intended",
            "details": (
                f"{SIMULATOR_MARKER} 1) Pull the change-record for the 19:40Z ACL push and "
                "diff against the prior version. 2) If 10.0.0.0/24 -> 10.42.10.0/24 should "
                "still be permitted, add 'access-list CORE-INGRESS 15 permit ip 10.0.0.0/24 "
                "10.42.10.0/24' on core-1, ahead of the deny. 3) Re-run the reachability "
                "check from branch-1; verify acl_deny_hits stops climbing."
            ),
            "risk": "medium",
            "requires_approval": True,
        },
    },
}


ALL_SCENARIO_NAMES: list[str] = list(SCENARIOS.keys())
