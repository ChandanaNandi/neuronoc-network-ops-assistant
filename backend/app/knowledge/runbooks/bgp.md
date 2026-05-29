# BGP neighbor / session flap

## Symptoms
- BGP neighbor state oscillating Established <-> Idle (or Active).
- Sudden bulk route withdrawal (prefix count drops on the local router).
- Downstream loss of reachability to prefixes the neighbor was originating.

## Evidence to inspect
- `show bgp summary` on both peers (state, up/down time, prefixes received).
- `show interface <link>` for L1/L2 errors on the underlying link.
- Syslog for BGP NOTIFY messages around the state transition.
- Recent config changes on either peer (timers, password, prefix-list, route-map).

## Likely causes
- Underlying link / optic failure (CRC errors, light below spec).
- BGP password / AS / IP mismatch after a maintenance change.
- Hold timer expiry due to control-plane CPU spike or RP failover.
- Outbound prefix-list / route-map drop that strips advertised prefixes (looks like withdrawal).

## Safe next steps (no approval required)
- Capture `show bgp neighbor <ip>` and surrounding syslog from both peers.
- Compare current BGP and interface counters against a known-good baseline.
- Trace the underlying physical path for L1 errors.

## Requires explicit approval
- `clear ip bgp <neighbor> [soft]` - resets the session; never run without an approved change window.
- Editing prefix-list / route-map on a production peer.
- Bouncing the underlying interface.

## Do not
- Reload the router as a first step.
- Modify timers globally.
