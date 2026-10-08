# Decision log

Append-only. Add new entries at the bottom. Do not edit earlier entries.

## 2026-10-08 v1

- Reason: Initial plan for the product announcement worked example.
- Change: Added T1, T2, T3, T4, T5, M1, and T7. T6 is unused so these ids match the design doc.
- Critical path at this version: T1, T2, T4, T5, M1, T7 (5.5 expected hours). T3 has 0.5h of CPM slack.
- Frozen: none (no task is in progress).
- Note: M1 is the content approval. T7 stays needs_human because publishing cannot be undone. Move T7 to in_progress only after the human says publish.

## 2026-10-08 v2

- Reason: Record the default replan policy on the worked example.
- Change: Added replan_policy (60 minute rate limit, 0.5h hysteresis, 1h freeze window). No task was rescheduled or reassigned.
- Frozen: none (no task is in progress).
