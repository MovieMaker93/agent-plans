# Agent operating rules

This repo is the plan store for the agent team. Plans live in git. Bots do not commit directly; a cloud coding agent makes the edit in one short run.

The everyday path is [docs/core.md](docs/core.md). Use it for a research note, a one-off, or a launch. The sections below are the rules for that path, then the commands you run only when the work needs them.

## Core contract

1. Shape the goal, the definition of done, the deadline, and the hour budget.
2. Plan with `instantiate research-report` or `instantiate announcement`, or copy `plans/_template/` for a custom graph. Edit `plan.yaml`. Leave `estimate_hours.calibrated` unset.
3. `validate`, `schedule`, and `gantt`.
4. `ready`, then `brief`. Hand the brief to the assignee.
5. The assignee writes the output, runs `record-artifact`, and stops at `in_review`.
6. Accuracy runs `attest-done`.
7. The Planner runs `complete` only when that attestation is a pass. A fail goes to `set-status` (`failed` or `in_review`).
8. `replan --now` when actuals slip. Skip it when the origin schedule is still right.

Omit `replan_policy` and the defaults apply (60 minutes, 0.5h hysteresis, 1h freeze window). Run `calibrate` only after a done task has actual hours. `portfolio schedule`, `subplan`, `simulate`, `serve`, and `digest --post` are for shared capacity, nested plans, what-ifs, and the meeting surface.

## Who writes what

- The Planner is the only writer of `plans/*/plan.yaml`.
- Other bots do not edit `plan.yaml`. They report to the Planner: task id, status, actuals, and where the artifacts are. The Planner batches those reports into the file.
- Accuracy is the only bot that may decide a task is `done`. Accuracy writes `plans/<slug>/attestations/<task_id>.yaml` with `attest-done`. The Planner records `done` with `complete`, and only when that file is a pass.
- A worker report must not set `done`. `set-status` refuses `done`. `validate` rejects a `done` task that has no matching pass attestation from `accuracy`.
- Git cannot prove which bot wrote the attestation. The check is enforcement inside this tool chain, not a cryptographic signature. Signatures are a later phase.
- `schedule.yaml`, `gantt.md`, `briefs/`, `portfolio.md`, `portfolio.yaml`, `portfolio-schedule.yaml`, `calibration.yaml`, and `dashboard/` are generated. Do not edit them by hand.
- Workers record outputs with `record-artifact` under `plans/<slug>/artifacts/<task_id>/`. The manifest's sha256 is what the attestation locks.

## Add a plan

Start here. Details and the traps are in [docs/core.md](docs/core.md).

```bash
python -m planner instantiate research-report --into plans/<YYYY-MM-DD>-<slug> --plan-id <id>
```

Use `announcement` when the work includes a publish step that a human must approve. Copy `plans/_template/` when you want the annotated field guide instead of a kind.

`AGENT_PLANS_DIR` overrides the directory scanned for live plans. Unset, that directory is `plans/`. A relative value is resolved from the repository root; an absolute value is used as given. Templates stay in `plans/_templates` in this checkout. A `subplan` value that starts with `plans/` is resolved inside the override when the variable is set. `check` validates the override, then clears the variable before the unit tests. `pytest` clears it when the test package is imported.

1. Edit `plan.yaml`. Set the goal, owner, definition of done, budget, deadline, `schedule_origin`, and tasks. The dates copied from a template are placeholders. Assignees are names in `roster.yaml`. The assignee must have every entry in `required_capabilities`. Leave `estimate_hours.calibrated` unset. Put raw optimistic, likely, and pessimistic hours only.
2. `depends_on` defaults to finish-to-start. A bare task id means type `FS` and lag 0.
3. `reversible: false` requires `needs_human: true`.
4. `instantiate` writes the first `log.md` entry. If you copied `_template/` by hand, append that entry yourself (reason, what changed, which in-progress tasks were frozen).
5. From the repo root, run:

```bash
python -m planner validate plans/<YYYY-MM-DD>-<slug>
python -m planner schedule plans/<YYYY-MM-DD>-<slug>
python -m planner gantt plans/<YYYY-MM-DD>-<slug>
```

6. Commit `plan.yaml`, `log.md`, `schedule.yaml`, and `gantt.md`.
7. If the repo already tracks portfolio or dashboard files, regenerate them so `check` stays green. Pin the clock the committed snapshot uses:

```bash
python -m planner portfolio
python -m planner portfolio schedule
python -m planner dashboard --at 2026-10-08T09:00:00-05:00
python -m planner digest --at 2026-10-08T09:00:00-05:00
```

`digest` without `--post` does not send Slack.

`replan_policy` is optional. If you omit it, the defaults are `min_interval_minutes: 60`, `hysteresis_hours: 0.5`, and `freeze_window_hours: 1`.

Calibration is a later pass. When a done task has actual hours, run `calibrate --apply`, then `estimate`, then `apply-calibration` on the next plan. `apply-calibration` writes `calibrated` beside the raw triple. It does not copy the factor back into likely, so a later apply does not compound. Commit `calibration.yaml` when that command changes it.

## Update status or actuals

1. Edit only `status` and `actuals` on the affected tasks, or use `set-status`. Bump `version`. Prefer the CLI so the log entry is appended for you.
2. Do not set `done` in the YAML by hand.
3. The worker records each output:

```bash
python -m planner record-artifact plans/<slug> <task_id> --file <path>
```

4. The worker stops at `in_review` (or the Planner sets that with `set-status`). Accuracy then runs:

```bash
python -m planner attest-done plans/<slug> <task_id> --verdict pass --at <ISO-8601>
python -m planner attest-done plans/<slug> <task_id> --verdict fail --notes "..." --at <ISO-8601>
```

5. Pass: the Planner runs `complete` with hours and attempts (at least 1). Fail: the Planner runs `set-status` to `failed` or `in_review`. That is the rework path. `complete` will refuse a fail.
6. Append a log entry if you did not use the CLI. The CLI appends one.
7. Run `validate`, `schedule`, and `gantt` again. `complete` and `set-status` regenerate the origin-based views. Then commit `plan.yaml`, `log.md`, `schedule.yaml`, `gantt.md`, plus any new manifest and attestation.

Status values: `planned`, `ready`, `in_progress`, `in_review`, `done`, `blocked`, `failed`, `cancelled`.

## Briefs

`ready` is still the queue. `brief` is the handoff:

```bash
python -m planner brief plans/<slug>            # every dispatchable task
python -m planner brief plans/<slug> <task_id>  # one task
```

Files land in `plans/<slug>/briefs/`. Each brief has the goal, the objective, input paths and sha256 values when a manifest exists, expected output paths under `artifacts/<task_id>/`, the acceptance criteria, the estimate, the budget, retries left, and a JSON report-back. The brief says not to expand scope and not to set `done`.

`dispatch-dry-run` prints the same queue with budget headroom, 80% warnings, and tasks a kill switch would stop. It does not send anything.

## Replan

1. Freeze tasks in `in_progress` or `in_review`. Do not reassign or rescope them unless status is `failed`.
2. Edit only the not-yet-started tasks when the change is a scope edit. Bump `version`.
3. To reschedule from the current time without a scope edit, run:

```bash
python -m planner replan plans/<slug> --now --reason "..." --at <ISO-8601>
```

That command checks the freeze, keeps each running task's start on `actuals.start`, reschedules not-yet-started tasks from `--at`, regenerates `schedule.yaml` and `gantt.md`, appends `log.md`, and bumps `version`.

4. A second replan inside `min_interval_minutes` (default 60) is refused. `--force` publishes anyway. `--human` bypasses the rate limit and still honors hysteresis. A plan with a `failed` task also bypasses the rate limit.
5. Hysteresis (default 0.5h): if the makespan moved by less than that and the critical path is unchanged, the command prints `suppressed by hysteresis` and does not write. `--force` publishes anyway.
6. Tasks whose previous start falls inside `freeze_window_hours` (default 1) are listed as assignee-frozen. This command does not change assignees.

A goal change (new deliverable, dropped deliverable, new deadline) is a human decision. Record it in `log.md` before editing the goal. `replan --now` does not change the goal.

A plain `schedule` after a replan replaces the from-now view with the origin-based view. Run `replan --now` again if you still want the anchored chart.

## Portfolio and calibration files

`python -m planner portfolio` rewrites `portfolio.md` and `portfolio.yaml`. Commit them with the plan change. `python -m planner check` fails if they are stale. The rollup includes a forecast (hours, and tokens or USD when the assignee has a cost model). A warning at 80% of budget is printed before the forecast crosses the cap. `forecast_hold` is that cross. It does not by itself mark a task killed.

`python -m planner portfolio schedule` rewrites `portfolio-schedule.yaml`. It levels every open live plan on one roster. Order is plan `priority` (higher first), then earlier deadline, then least CPM slack. Assignee capacity is shared across plans. Each plan keeps its own `max_parallel`. This file does not replace `plans/<slug>/schedule.yaml`.

`calibration.yaml` is the output of `calibrate --apply`. Do not edit it by hand. See the README for the shrinkage formula. `estimate` and `apply-calibration` look up a factor in this order: plan pair, plan task type, plan assignee, plan blanket factor, then the shared pair, task type, assignee, and overall, then `by_kind` for `template_kind`, else 1. `by_kind` is filled from done tasks whose plan sets `template_kind`.

## Sub-plans

A task may set `subplan: plans/<directory>` (a path from the repo root, with no `..`). The parent's scheduling duration is the child plan's makespan. `gantt`, `schedule`, and `portfolio` show the rolled status: all active tasks done, else failed if any failed, else in progress if any task is running, in review, or already done, else blocked, else planned. `validate` rejects a missing path, a cycle, and a parent set to `done` while the child has not rolled up to done. A status that merely disagrees is a warning. Do not expect these commands to rewrite the parent status. The Planner still edits `plan.yaml`.

## Cloud agents

A roster entry with `kind: cloud_agent` is a worker with capabilities, a concurrency `capacity`, and an optional `cost` (`usd_per_hour`, `tokens_per_hour`). `dispatch-dry-run` treats that name like any other assignee: same brief path, same artifact manifest, same attestation. `kind: human` is the approval roster entry. Omit `kind` and the entry is a bot.

## Templates, simulation, dashboard

`python -m planner instantiate <kind> --into plans/<slug> --plan-id <id>` copies `plans/_templates/<kind>/`. Kinds in the tree now: `announcement`, `research-report`. Keep `template_kind` so later calibration can attach to that kind.

`python -m planner simulate <plan>` prints p10, p50, and p90 finish times. It writes nothing. `--add-capacity name=N`, `--omit <task>`, `--omit-deliverable <name>`, and `--deadline <ISO-8601>` are what-if knobs. They print a delta against the baseline.

`python -m planner dashboard --at <ISO-8601>` writes `dashboard/index.md` and `dashboard/index.html`. `python -m planner digest --at <ISO-8601>` writes `dashboard/digest.md` and `dashboard/slack-payload.json`, and prints the same text: what changed, decisions needed, risks, and the next 24 hours. The committed snapshot uses `2026-10-08T09:00:00-05:00`. Regenerate with that timestamp before committing, or the tests fail.

## Tailscale dashboard

`python -m planner serve` is a read-only HTTP server. It serves the generated dashboard, the digest, the Slack payload, and the portfolio files. GET `/regenerate` rewrites those views from `plan.yaml`. It leaves plans alone. POST, PUT, and DELETE are refused.

Run it on localhost and let Tailscale publish that port to the tailnet:

```bash
python -m planner dashboard
python -m planner digest
python -m planner serve --host 127.0.0.1 --port 8787 --refresh 300
tailscale serve --bg 8787
```

`tailscale serve` gives the machine an HTTPS name on the tailnet. Peers open that name. A public funnel is not part of this setup. `--refresh 300` regenerates the views every 300 seconds and adds a reload tag to the HTML response. The file on disk stays the plain generated page.

To bind the process to the tailnet address instead of using `tailscale serve`:

```bash
python -m planner serve --host "$(tailscale ip -4)" --port 8787
```

Stop the proxy with `tailscale serve off`.

## Slack digests

`digest` is a dry run unless `--post` is set. `--post` reads `SLACK_DIGEST_WEBHOOK` (a Slack incoming webhook) from the environment and POSTs `dashboard/slack-payload.json`. The URL is never written into the repo. If `--post` is set and the variable is empty, the command exits 1 and sends nothing.

A Planner or Grok Bot routine can run this on a schedule from the repo root:

```bash
python -m planner digest --post
```

Example cron, weekdays at 09:00, with the webhook already in the environment of that job:

```bash
0 9 * * 1-5 cd /path/to/agent-plans && python -m planner digest --post
```

`python -m planner check` does not post and does not open a webhook. Tests pass a fake opener.

## Dispatch

`python -m planner ready` prints two lists. `dispatch` is safe to hand to `brief` and then to the assignee. `awaiting_human` is a gate: leave it until a person decides, then set `in_progress` (a worker will do it) or, after Accuracy accepts the approval record and `attest-done` passes, `complete`.

## Checks

`python -m planner check` validates every plan and runs the tests. Run it before you finish.

Scheduling is a heuristic. RCPSP is NP-hard. Do not rewrite the scheduler to "make it optimal" in a status-update run.
