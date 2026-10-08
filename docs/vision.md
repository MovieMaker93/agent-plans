# Product vision notes

This file records what the plan store implements and what is still a follow-on. The announcement plan under `plans/2026-10-08-product-announcement/` stays the unstarted design-doc baseline (critical path 5.5h). Multi-plan fixtures live in the tests.

The everyday path is [core.md](core.md): shape, plan, brief, work, attest, complete, and replan when needed.

## Core

- YAML plans, a roster, and `python -m planner check` (validate plus unit tests). Pure Python. `check` does not make network calls.
- `instantiate` for `research-report` (one reversible task) and `announcement` (a publish gate). `plans/_template/` is the annotated field guide for a custom graph.
- PERT estimates (optimistic, likely, pessimistic). The scheduler uses raw expected hours until `calibrated` is set.
- Origin `schedule` and `gantt`. `replan --now` when actuals slip, with freeze, rate limit, and hysteresis. The scheduler is a priority-list heuristic. RCPSP is NP-hard. It is not an exact optimum.
- `ready` and `brief`. Artifact manifests (sha256). An Accuracy done-lock. Workers cannot set `done`. The Planner is the writer of `plan.yaml` and runs `complete`.

## Available when the work needs them

- Calibration (`calibrate --apply`, `estimate`, `apply-calibration`) after a done task has actual hours. Raw optimistic, likely, and pessimistic hours stay. A plan may set `calibration_override` or `template_kind`.
- `portfolio`, including a forecast that warns at 80% of budget and flags a hold when the forecast is already over.
- `portfolio schedule` levels open plans on one roster (priority, then deadline, then least slack). Each plan's own `schedule.yaml` stays the single-plan view.
- Sub-plans: `subplan: plans/<directory>` rolls duration and status up to the parent task.
- Cloud agents as roster entries (`kind: cloud_agent`) with capacity and an optional cost model. Omit `kind` and the entry is a bot. Dry-run dispatch uses the same brief, artifact, and attest paths.
- `simulate`: seeded Monte Carlo percentiles and report-only what-ifs. It writes nothing.
- A static dashboard and a digest. `serve` exposes them on localhost; Tailscale Serve publishes that port on the tailnet.
- Slack digests. `digest` writes `dashboard/slack-payload.json`. `digest --post` sends it when `SLACK_DIGEST_WEBHOOK` is set. `check` does not post.

## Follow-ons

These are named so a later change does not have to rediscover them. They are not implemented.

- Live webhook dispatcher. `dispatch-dry-run` is the preview. It does not send work.
- Attestation signatures. `signature` is stored as null and is not checked. Git cannot prove which bot wrote the file.
- An `events/` inbox that workers append to, which the Planner batches into `plan.yaml`.
