# Missing prefix in routing table

## Symptoms
- A prefix that should be reachable returns "% Network not in table" or its equivalent.
- Downstream services that depend on the prefix start failing with timeouts.
- The expected advertising router still has the prefix in its local RIB.

## Evidence to inspect
- `show ip route <prefix>` on the receiver.
- `show bgp neighbors <peer> advertised-routes` on the sender.
- Outbound prefix-list / route-map on the sender for the affected peer.
- Inbound prefix-list / route-map on the receiver.
- Recent policy changes on either side.

## Likely causes
- A missing entry in an outbound prefix-list (sender drops the announcement).
- An inbound filter on the receiver rejecting the prefix.
- The advertiser lost the prefix in its own table (upstream withdrawal cascading).
- AS-path / community based policy unintentionally matching the prefix.

## Safe next steps (no approval required)
- Walk the announcement path: local RIB on advertiser, advertised-routes to peer, received-routes on receiver, RIB on receiver.
- Compare the active policy against the last known-good revision in version control.

## Requires explicit approval
- Adding an explicit `permit` entry to a prefix-list or route-map.
- Bouncing the BGP session to force re-advertisement.
- Modifying redistribution policy.

## Do not
- Apply a `permit any` to fix it - that opens unrelated prefixes to the same path.
