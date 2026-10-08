# Changelog

## 1.0.0 - 2026-10-08

Public release of the agent plan store.

- Validate a `plan.yaml` graph against the roster, artifact provenance, dependency cycles, and the Accuracy done-lock
- Schedule with critical-path slack and a resource-constrained pass that honors assignee capacity and `max_parallel`
- Render a Mermaid Gantt chart
- List a ready queue that keeps human gates out of dispatch
- Write task briefs, sha256 artifact manifests, and Accuracy attestations; `complete` is the only path to `done`
- Replan from a chosen clock, freezing in-progress and in-review work, with a rate limit and hysteresis
- Calibrate estimates from done-task actuals without rewriting the raw optimistic, likely, and pessimistic hours
- Roll up a portfolio and level open plans on one shared roster
- Roll a sub-plan's makespan and status into a parent task
- Simulate p10, p50, and p90 finish times, including what-if knobs that do not edit the plan
- Instantiate `research-report` and `announcement` templates
- Write a static dashboard and serve it read-only
- Write a meeting digest and post it to Slack only when `SLACK_DIGEST_WEBHOOK` is set and `--post` is passed
- Preview dispatch, including budget headroom, an 80 percent warning, and a kill switch, without sending work
- Point `AGENT_PLANS_DIR` at another directory when live plans should not live in this checkout. `check` still validates that directory; its unit tests clear the variable so they stay on this checkout
