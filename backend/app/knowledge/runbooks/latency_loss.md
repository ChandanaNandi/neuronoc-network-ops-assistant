# Latency spike / packet loss on a path

## Symptoms
- RTT for a known destination increases multiple times above its baseline.
- Sustained packet loss > 1% on a path that is normally clean.
- Traceroute shows a single hop where latency jumps.

## Evidence to inspect
- `ping` / `traceroute` from the affected source to the destination.
- Queue / drop counters on the suspected slow hop.
- Link utilization on the suspect hop's upstream / downstream interfaces.
- Any recently-scheduled jobs (backups, replication) that could saturate the path.

## Likely causes
- Sustained congestion on an intermediate link.
- Queue management changes (shaper / policer / WRED) misconfigured.
- Microbursts from a noisy neighbor flow.
- Asymmetric routing introducing a longer return path.

## Safe next steps (no approval required)
- Capture ping / traceroute baselines and current measurements.
- Pull queue / drop counters on the slow hop.
- Identify the top talkers on the slow hop via flow telemetry (NetFlow / sFlow if available).

## Requires explicit approval
- Adjusting QoS / shaper configuration on the slow hop.
- Diverting traffic to an alternate path.
- Rate-limiting a top-talker.

## Do not
- Reload the slow hop as a first step.
- Apply ACL drops "to see what happens" - that hides the real cause.
