# Interface error / drop spike

## Symptoms
- Rising input CRC errors, runts, giants, or rx_dropped on a single interface.
- Output drops + queue depth growing under normal load.
- Optics RX power below vendor spec.

## Evidence to inspect
- `show interface <name>` counters at two timestamps (delta matters more than absolute).
- `show interface <name> transceiver` for optics light levels and temperature.
- Physical: patch panel, fiber connector, SFP seating.
- Has the interface been recently moved, re-cabled, or upgraded?

## Likely causes
- Dirty / damaged fiber connector or patch cable.
- Failing optic (TX or RX side; check both peers).
- Duplex mismatch (rare on modern links; still possible on legacy gear).
- Sustained congestion exceeding the link bandwidth.

## Safe next steps (no approval required)
- Snapshot counters, optic light levels, and recent topology changes.
- Compare against the peer-side interface counters; one-sided errors often point to the local optic / patch.

## Requires explicit approval
- Cleaning / reseating the optic (causes a brief outage).
- Swapping the SFP or patch cord.
- Shutting / no-shutting the interface.
- Migrating traffic off the link via routing policy.

## Do not
- Mark the interface "errdisable recovery" without understanding the underlying cause - that can mask a failing optic.
