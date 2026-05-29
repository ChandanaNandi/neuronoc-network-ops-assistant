# ACL / policy denying previously-permitted traffic

## Symptoms
- A flow that worked yesterday now times out at a specific hop.
- ACL deny counters climbing on the suspected hop for the affected source/destination pair.
- The change is correlated in time with a recent policy push.

## Evidence to inspect
- `show access-list <name>` on the denying device.
- `show access-list <name> | include matches|hits` for deny counters.
- The change-record / commit log for the ACL or firewall policy.
- A reachability test (ping / curl) from the affected source to the destination.

## Likely causes
- A new policy push removed or reordered a `permit` rule.
- A `permit` rule was intended for a different prefix (typo).
- An implicit catch-all `deny` is now reached because an earlier permit narrowed.
- Object-group / address-set membership changed under a referenced rule.

## Safe next steps (no approval required)
- Diff the current ACL against the previous revision.
- Capture deny-counter hits for the affected flow to confirm the deny location.
- Verify the change-record approved the rule removal / re-order.

## Requires explicit approval
- Inserting an explicit `permit` rule ahead of the deny (most common fix).
- Reverting the policy to the previous revision.
- Disabling the ACL temporarily ("permit any") - rarely safe; never without approval.

## Do not
- Add a catch-all `permit ip any any` to "fix" the symptom.
- Edit ACLs in place on production gear without a rollback plan.
