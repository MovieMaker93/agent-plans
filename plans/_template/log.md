# Decision log

Append-only. Add new entries at the bottom. Do not edit earlier entries.

## 2026-10-08 v1

- Reason: Annotated template, not a live project.
- Change: Added T1, T2, and M1 so agents can copy a valid plan.
- Frozen: none.

## 2026-10-08 v2

- Reason: Show the default replan policy on the template.
- Change: Added replan_policy with the control-loop defaults (60 minute rate limit, 0.5h hysteresis, 1h freeze window).
- Frozen: none.
