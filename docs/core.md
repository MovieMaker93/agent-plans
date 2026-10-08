# Core contract

This is the path every task uses. One research note and a multi-step launch share it. The other commands stay in the repo for the cases that need them. They are not steps you run on every plan.

## Loop

1. **Shape.** Write the goal, the definition of done, the deadline, and the hour budget.
2. **Plan.** Start a directory and edit `plan.yaml`.
3. **Schedule.** `validate`, then `schedule`, then `gantt`.
4. **Brief.** `ready`, then `brief`. Hand that file to the assignee.
5. **Work.** The assignee writes the output and runs `record-artifact`. Status stops at `in_review`.
6. **Attest.** Accuracy runs `attest-done`.
7. **Complete.** The Planner runs `complete` when the verdict is a pass from `accuracy`.
8. **Replan when needed.** `replan --now` after actuals slip. Skip it when the origin schedule is still right.

The Planner is the only writer of `plan.yaml`. A worker report does not set `done`. `set-status` refuses `done`.

## Start a plan

One reversible task (research, a writeup, a one-off):

```bash
python -m planner instantiate research-report \
  --into plans/<YYYY-MM-DD>-<slug> --plan-id <id>
```

A launch that publishes something (the publish task is a human gate):

```bash
python -m planner instantiate announcement \
  --into plans/<YYYY-MM-DD>-<slug> --plan-id <id>
```

A custom graph: copy `plans/_template/` and use the comments in that `plan.yaml` as the field guide.

Live plans are read from `plans/` unless `AGENT_PLANS_DIR` is set to another directory. Templates stay in this checkout under `plans/_templates`. The field guide is [user-guide.md](user-guide.md). Worked plans are in [scenarios.md](scenarios.md).

Then edit the goal, the deadline, and `schedule_origin`. The copied dates are placeholders. Assignees are names in `roster.yaml`. Each assignee must have every `required_capabilities` entry. Estimates are optimistic, likely, and pessimistic hours. Leave `estimate_hours.calibrated` unset. A bare task id in `depends_on` means finish-to-start with lag 0. `reversible: false` requires `needs_human: true`.

```bash
python -m planner validate plans/<slug>
python -m planner schedule plans/<slug>
python -m planner gantt plans/<slug>
```

Commit `plan.yaml`, `log.md`, `schedule.yaml`, and `gantt.md`.

`replan_policy` may be omitted. The defaults are 60 minutes between replans, 0.5h hysteresis, and a 1h freeze window.

## Work and done

```bash
python -m planner ready
python -m planner brief plans/<slug>
python -m planner record-artifact plans/<slug> <task_id> --file <path>
python -m planner attest-done plans/<slug> <task_id> --verdict pass --at <ISO-8601>
python -m planner complete plans/<slug> <task_id> --hours <n> --attempts 1 --at <ISO-8601>
```

`ready` prints `dispatch` (safe to brief) and `awaiting_human` (leave it until a person decides). The queue is computed from dependencies and gates. You do not have to set `status: ready` by hand.

A fail attestation is rework. The Planner runs `set-status` to `failed` or `in_review`. `complete` refuses a fail.

```bash
python -m planner replan plans/<slug> --now --reason "..." --at <ISO-8601>
```

In-progress and in-review tasks keep their start. The command appends `log.md` and bumps `version`.

## When a live plan changes the generated views

`python -m planner check` compares the committed portfolio, dashboard, and digest to a fresh render. After you add or edit a live plan, regenerate them before you finish:

```bash
python -m planner portfolio
python -m planner portfolio schedule
python -m planner dashboard --at 2026-10-08T09:00:00-05:00
python -m planner digest --at 2026-10-08T09:00:00-05:00
```

That `digest` is a dry run. It writes `dashboard/slack-payload.json` and does not post.

## Use these only when the situation needs them

| Situation | Command |
| --- | --- |
| A done task has actual hours, and the next estimate should learn | `calibrate --apply`, then `estimate`, then `apply-calibration` |
| Several open plans share one roster | `portfolio` and `portfolio schedule` |
| A task is itself another plan | `subplan: plans/<directory>` on that task |
| You want a finish-time range or a what-if | `simulate` (prints only) |
| Someone wants the meeting page on the tailnet | `serve`, then `tailscale serve` |
| Someone wants the digest in Slack | `digest --post` with `SLACK_DIGEST_WEBHOOK` set |

`dispatch-dry-run` previews the queue, the budget, and the forecast. It does not send work.

## Traps

- `schedule` with no `--from-now` rewrites `schedule.yaml` from `schedule_origin`. After `replan --now`, run `replan --now` again if you still want the anchored chart.
- Each plan's `schedule.yaml` ignores other plans. When two open plans share an assignee, `portfolio schedule` is the view that keeps that person inside capacity.
- `instantiate` copies `schedule_origin` and `deadline` from the template. Set both to this piece of work before you schedule.
- `research-report` is one task and has no human gate. Add `needs_human: true` on a later task when that step cannot be undone.
- `digest` and `dashboard` without `--at` use the current clock. The committed snapshot in this repo uses `2026-10-08T09:00:00-05:00`.
- A forecast warning is advice. `forecast_hold` means the projection is already over the hour budget, or over a non-zero token or USD budget. `halt_new_dispatch` watches hours already spent. Neither one is a dispatcher.
