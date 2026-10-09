# User guide

This guide covers the concepts, the `plan.yaml` schema, every `python3 -m planner` subcommand, and three workflows: a Planner bot, an Orchestrator, and a human reviewer. The schema section is checked against `schemas/plan.schema.json` and `schemas/roster.schema.json`. The command section is checked against `python3 -m planner <command> --help`.

Python 3.11 or newer. From the repository root, after `pip install -r requirements.txt`:

```bash
python3 -m planner validate examples/01-solo-feature
python3 -m planner schedule examples/01-solo-feature
python3 -m planner gantt examples/01-solo-feature
python3 -m planner ready examples/01-solo-feature
```

The everyday loop for this checkout is also written in [core.md](core.md). Worked plans are in [scenarios.md](scenarios.md).

Global options, from `python3 -m planner --help`:

- `--version` prints the package version.
- `--root PATH` sets the repository root. The default is the directory that contains `roster.yaml` and `plans/`.

## Concepts

### Plan

A plan is one directory with a `plan.yaml`. It has a `plan_id`, a `version` that you bump on every edit, a `goal`, an `owner`, a `definition_of_done`, a `budget`, a `human_approval_policy`, `constraints`, `deliverables`, and `tasks`. Optional fields are `title`, `priority`, `template_kind`, `replan_policy`, `calibration_override`, and `provided_inputs`.

`constraints.schedule_origin` is the clock. Schedule times are hours after that instant. `constraints.deadline` is optional. `constraints.max_parallel` is how many tasks in this plan may run at once.

`priority` is an integer. Higher values are scheduled first when `portfolio schedule` shares one roster. The default is 0.

`template_kind` names the `plans/_templates/<kind>` directory the plan was copied from. Calibration can attach a factor to that name.

Generated files in the directory (`schedule.yaml`, `gantt.md`, `briefs/`) are views. Edit `plan.yaml`, then regenerate them.

### Task

A task has an `id`, a `title`, a `task_type`, an `objective`, `inputs`, `outputs`, `depends_on`, `estimate_hours`, `required_capabilities`, an `assignee`, `acceptance_criteria`, a `risk`, `reversible`, `needs_human`, a `budget`, `max_retries`, a `status`, and `actuals`. Optional fields are `section` and `subplan`.

`estimate_hours` is a PERT triple: `optimistic`, `likely`, and `pessimistic`. Expected hours are `(optimistic + 4 * likely + pessimistic) / 6`. The spread is `(pessimistic - optimistic) / 6`. `calibrated`, when set, is the duration the scheduler uses. It does not replace the three raw numbers.

A milestone is a task whose optimistic, likely, and pessimistic hours are all 0, with no `calibrated` override. A milestone takes no capacity.

`section` is the Gantt row group. It defaults to the assignee.

`task_type` is a stable name used by calibration, such as `research` or `accuracy_review`.

`risk` is `low`, `medium`, or `high`.

`actuals` records `start`, `end`, `hours`, and `attempts`. Use null for a time or an hour figure that is not known yet.

Every output is either a deliverable or an input of a downstream task. An input is a `provided_inputs` artifact or it names `from_task`, and that task must be an upstream dependency and the producer of the artifact.

### Owner and roster

`owner` and every `assignee` are names in `roster.yaml`. The assignee must have every entry in `required_capabilities`.

A roster entry has `name`, `description`, `capabilities`, and `capacity`. `capacity` is how many of that assignee's tasks may run at once. `kind` is `bot` (the default), `cloud_agent`, or `human`. Optional `cost` has `usd_per_hour` and `tokens_per_hour`, which the forecast uses. Omit `kind` and the entry is a bot.

`kind: cloud_agent` is a worker with the same brief, artifact manifest, and attestation path as a bot. `kind: human` is the approval entry. Assign human gates there.

`default_max_parallel` on the roster is a fleet note. Each plan still sets `constraints.max_parallel`.

### Dependencies

`depends_on` defaults to finish-to-start. A bare task id means type `FS` and lag 0. A mapping has `task`, optional `type` (`FS`, `SS`, `FF`, `SF`), and optional `lag_hours`.

- `FS`: the successor starts after the predecessor finishes, plus lag.
- `SS`: the successor starts after the predecessor starts, plus lag.
- `FF`: the successor finishes after the predecessor finishes, plus lag.
- `SF`: the successor finishes after the predecessor starts, plus lag.

The validator rejects a self-dependency, an unknown id, a duplicate edge, a cycle, and an active task that depends on a cancelled task.

### Gates

Two different gates show up in the tool.

A human gate is a task with `needs_human: true`. Irreversible work must set that flag: `reversible: false` is an error unless `needs_human` is true. While the gate is open, work it does not block continues. The policy text on the plan says what a person must approve. The safe default while a gate is open is hold.

The Accuracy done-gate is not a task flag. It is the rule that `status: done` is valid only with a passing attestation. See below.

### Statuses

`planned`, `ready`, `in_progress`, `in_review`, `done`, `blocked`, `failed`, `cancelled`.

`ready` is computed. You do not have to set `status: ready` for a task to appear in the dispatch queue. `cancelled` tasks stay in the file and drop out of the active graph.

`set-status` can set any status except `done`. The `--help` choices list `done` because the parser shares the status enum; the command refuses it.

### Critical path and slack

`schedule` stores two results in `schedule.yaml`.

Critical path method (CPM) assumes unlimited assignees. It records earliest start, earliest finish, latest start, latest finish, and slack. Slack is latest start minus earliest start. Zero slack is critical. `critical_path` is one chain. `critical_tasks` lists every task with zero slack, which can be wider than that chain when several independent tasks are the same length.

The resource-constrained `start` and `finish` come from a parallel priority list:

1. Priority is least CPM slack, then longest scheduling duration, then most downstream tasks, then task id.
2. At the current time, start every eligible task that fits the assignee's `capacity` and the plan's `max_parallel`.
3. Advance time to the next finish, or to the next moment a predecessor releases a task.

Intervals are half-open, so the next task can start when the previous one finishes. This list is a heuristic. It is not an exact optimum.

`gantt` draws bars at the resource-constrained times. In the Mermaid chart, `crit` means CPM slack is zero.

### Ready queue

`ready` prints two lists.

`dispatch` is tasks whose dependencies are satisfied, that are not started (`planned` or `ready`), and that do not need a human. `awaiting_human` is the same dependency test for tasks with `needs_human: true`. A human gate whose predecessor is still open is in neither list.

The queue does not apply assignee capacity. Capacity shows up in `schedule` and in `portfolio schedule`. A task can be dispatchable and still have to wait for a free slot.

### Accuracy done-gate

`validate` accepts `status: done` only when all of these hold:

- `attestations/<task_id>.yaml` exists and names this `plan_id` and `task_id`.
- `verdict` is `pass`, and every acceptance criterion is `met: true`, in the same words as the task.
- `reviewer` is `accuracy`.
- `artifacts/<task_id>/manifest.yaml` exists, and each output sha256 matches the attestation.

A fail attestation does not unlock `done`. The Planner sets `failed` or `in_review`. `complete` is the only command that writes `done`, and it refuses a fail. Git does not prove which bot wrote the attestation. The check is this tool, not a signature. The `signature` field is stored and is not checked.

### Human gates

`needs_human: true` puts a task in `awaiting_human` once its dependencies are done. Leave it there until a person decides. After the decision, a worker sets the task `in_progress` (the work of recording the decision) or, when the decision itself is the task output and Accuracy has accepted it, the Planner runs `complete`.

Publishing, deleting, sending external messages, changing the goal or the deadline, and spending beyond the budget are the usual reasons for a human gate. The plan's `human_approval_policy` is the prose version of that rule.

### Calibration

Skip this until a done task has actual hours. A new plan schedules on raw PERT expected.

`calibrate` prints a diagnostic: actual hours divided by PERT expected, for every task that has actual hours, including work that is not done. It writes nothing unless you pass `--apply`.

`calibrate --apply` rebuilds `calibration.yaml` from done tasks only. For each `(assignee, task_type)` pair, and for the task-type, assignee, and overall fallbacks:

```
raw    = sum(actual hours) / sum(likely hours)
weight = n / (n + prior_strength)     # prior_strength is 3
factor = clamp(1 + weight * (raw - 1), 0.5, 2.0)
```

`estimate PLAN` prints raw PERT expected, the factor, the sample count, the basis, and the preview. It writes nothing.

`apply-calibration PLAN` writes `estimate_hours.calibrated` beside the raw triple and bumps `version` when any calibrated value changes. It always multiplies the raw expected by the current factor, so a later run does not compound. It does not copy the factor back into `likely`.

Lookup order:

1. The plan's `calibration_override` pair (`by_pair`: `assignee`, `task_type`, `factor`), then `by_task_type`, then `by_assignee`, then its blanket `factor`.
2. The shared file: pair, then task type, then assignee, then overall.
3. `by_kind` for the plan's `template_kind`.
4. Else 1.

`schedule` reads `calibrated` when it is set. It does not multiply by the factor itself.

### Capacity

Assignee `capacity` in the roster and `constraints.max_parallel` on the plan are the two caps inside one plan. `portfolio schedule` adds a third view: every open live plan shares one roster, so an assignee at capacity 1 cannot be in two plans at once. Each plan keeps its own `max_parallel`. Order is plan `priority` (higher first), then earlier deadline, then least CPM slack. That file does not replace each plan's `schedule.yaml`.

A zero token or USD budget is not treated as a cap. The hour budget is. `dispatch-dry-run` warns at 80 percent of a task's hour budget and lists a task under the kill switch at 100 percent of that task budget, or when `attempts` has already reached `max_retries` after the task has started. At 100 percent of the plan hour budget already spent, `halt_new_dispatch` is yes. The forecast projects hours still to go. At 80 percent of the plan hour budget it says warn. Over the budget it says breach and `forecast_hold: yes`. A non-zero token or USD budget that the forecast exceeds also sets `forecast_hold: yes`. Forecast hold does not by itself stop the queue.

### Sub-plans

A task may set `subplan` to a relative path with no `..`, such as `plans/<directory>` or `examples/06-program/child`. The parent's scheduling duration for that task becomes the child plan's makespan. `gantt`, `schedule`, and `portfolio` show the child's rolled status: all active tasks done, else failed if any failed, else in progress if any task is running, in review, or already done, else blocked, else planned.

`validate` errors on a missing path, a cycle, or a parent set to `done` while the child has not rolled up to done. A status that merely disagrees is a warning. These commands do not rewrite the parent `plan.yaml`.

### Dashboard and serve

`dashboard` writes `dashboard/index.md` and `dashboard/index.html` from live plans: portfolio, Gantt snapshot, ready queue, gates, and burn versus forecast. The HTML page has no remote assets. Pass `--at` when you need a stable snapshot. The committed snapshot in this repository uses `2026-10-08T09:00:00-05:00`.

`serve` is a read-only HTTP server for those files, the digest, and the portfolio files. `GET /regenerate` rewrites the views from `plan.yaml`. POST, PUT, and DELETE are refused. `--refresh N` regenerates every N seconds and adds a reload tag to the HTML response. The file on disk stays the plain page.

```bash
python3 -m planner serve --host 127.0.0.1 --port 8787 --refresh 300
```

Bind to localhost and put a private network proxy in front of it if people on that network should open the page. This repository does not record a Tailscale hostname.

### Slack digest

`digest` prints a meeting-prep summary and writes `dashboard/digest.md` and `dashboard/slack-payload.json`: what changed, decisions needed, risks, and the next 24 hours. It does not post unless you pass `--post`. `--post` reads `SLACK_DIGEST_WEBHOOK` from the environment and sends the payload. If `--post` is set and the variable is empty, the command exits 1 and sends nothing. The URL is never written into the repository. `check` does not post.

```bash
python3 -m planner digest --at 2026-10-08T09:00:00-05:00
```

## Where plans live

`validate`, `schedule`, `gantt`, `ready`, `status`, and `portfolio` scan `plans/` at the repository root. Set `AGENT_PLANS_DIR` to move that scan. A relative value is resolved from the repository root. An absolute value is used as given. Unset, the scan is `<root>/plans`, which is the current behavior.

`check` validates that same directory, then runs the unit tests with `AGENT_PLANS_DIR` unset. The tests compare the snapshots committed in this checkout. Unset the variable yourself only if you run a test file without importing the `tests` package. `pytest` clears it at import.

Templates stay in `<root>/plans/_templates`. `instantiate` copies from there even when the variable is set. Directories whose names start with `_` are patterns. `ready`, `status`, `calibrate`, `portfolio`, and `dispatch-dry-run` skip them. `validate` with no path still checks `plans/_template` and `plans/_templates/<kind>/`.

When the variable is set, a `subplan` value that starts with `plans/` is resolved inside the override, so a later move of live plans can keep those references. Any other relative reference stays under the repository root.

## plan.yaml schema

`schemas/plan.schema.json` is the editor contract (`$schema` draft 2020-12). `python3 -m planner check` (validate plus the unit tests) is the `planner-check` job in `.gitlab-ci.yml`. That job does not check commit history. The `commit-hygiene` job runs `scripts/check_commit_hygiene.py` only on GitLab, on the default branch and on tags, with a full clone. The guard itself runs when invoked. Before pushing maintainer history, run `python scripts/check_commit_hygiene.py --all && gitleaks git`. `validate` covers the schema plus rules a schema cannot express: unknown assignees, capability mismatch, missing inputs, cycles, and a `done` task with no passing attestation. `additionalProperties` is false on the plan, on a task, and on the nested objects below. `schema_version` is `1`.

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | Const 1 |
| `plan_id` | yes | Stable id |
| `version` | yes | Integer >= 1. Bump on every edit |
| `title` | no | Short name. The Gantt uses it |
| `goal` | yes | What the plan is for |
| `owner` | yes | Roster name. Usually `orchestrator` |
| `definition_of_done` | yes | When the plan is finished |
| `budget` | yes | Plan cap. See budget |
| `human_approval_policy` | yes | What a person must approve |
| `constraints` | yes | Deadline, parallelism, origin |
| `replan_policy` | no | Rate limit, hysteresis, freeze window |
| `priority` | no | Higher schedules first across plans. Default 0 |
| `template_kind` | no | Template name for calibration |
| `calibration_override` | no | Factors checked before `calibration.yaml` |
| `provided_inputs` | no | Artifacts that exist before any task |
| `deliverables` | yes | Final artifact names. At least one, unique |
| `tasks` | yes | At least one task |

Budget (`budget` on the plan and on each task):

| Field | Required | Meaning |
| --- | --- | --- |
| `tokens` | yes | Integer >= 0. Zero is not a cap |
| `hours` | yes | Number >= 0 |
| `usd` | yes | Number >= 0. Zero is not a cap |

Constraints:

| Field | Required | Meaning |
| --- | --- | --- |
| `deadline` | no | ISO-8601 datetime. Must be after the origin when set |
| `max_parallel` | yes | Integer >= 1 |
| `schedule_origin` | yes | ISO-8601 datetime. Hours are counted from here |

Replan policy. Defaults if omitted: `min_interval_minutes` 60, `hysteresis_hours` 0.5, `freeze_window_hours` 1.

| Field | Required | Meaning |
| --- | --- | --- |
| `min_interval_minutes` | no | Refuse another replan inside this window |
| `hysteresis_hours` | no | Do not publish a smaller move when the critical path is unchanged |
| `freeze_window_hours` | no | Not-yet-started tasks whose previous start falls in this window are listed as assignee-frozen |

Provided input:

| Field | Required | Meaning |
| --- | --- | --- |
| `artifact` | yes | Name |
| `description` | no | What the file is |

Estimate (`estimate_hours`):

| Field | Required | Meaning |
| --- | --- | --- |
| `optimistic` | yes | Hours >= 0 |
| `likely` | yes | Hours >= 0 |
| `pessimistic` | yes | Hours >= 0 |
| `calibrated` | no | Scheduling duration written by `apply-calibration` |

Actuals:

| Field | Required | Meaning |
| --- | --- | --- |
| `start` | yes | ISO-8601 or null |
| `end` | yes | ISO-8601 or null |
| `hours` | yes | Number >= 0, or null |
| `attempts` | yes | Integer >= 0 |

Task input:

| Field | Required | Meaning |
| --- | --- | --- |
| `artifact` | yes | Name |
| `from_task` | no | Producer task id. Omit only for a provided input |

Task output:

| Field | Required | Meaning |
| --- | --- | --- |
| `artifact` | yes | Name |
| `type` | yes | Kind, such as `markdown` or `image` |

Dependency: a string task id, or an object.

| Field | Required | Meaning |
| --- | --- | --- |
| `task` | yes | Predecessor id |
| `type` | no | `FS`, `SS`, `FF`, or `SF`. Default `FS` |
| `lag_hours` | no | Number. Default 0 |

Task:

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Starts with a letter, then letters, digits, `_`, or `-` |
| `title` | yes | Short name |
| `section` | no | Gantt group. Defaults to the assignee |
| `task_type` | yes | Calibration key |
| `objective` | yes | What the assignee does |
| `inputs` | yes | List, possibly empty |
| `outputs` | yes | At least one |
| `depends_on` | yes | List, possibly empty |
| `estimate_hours` | yes | PERT triple |
| `required_capabilities` | yes | At least one roster capability |
| `assignee` | yes | Roster name |
| `acceptance_criteria` | yes | At least one string. Accuracy copies these words |
| `risk` | yes | `low`, `medium`, or `high` |
| `reversible` | yes | Boolean. False requires `needs_human` |
| `needs_human` | yes | Boolean |
| `budget` | yes | Task cap |
| `max_retries` | yes | Integer >= 0 |
| `status` | yes | See statuses |
| `actuals` | yes | Start, end, hours, attempts |
| `subplan` | no | Relative path to a child plan |

Calibration override. Checked before `calibration.yaml`. Order: `by_pair`, `by_task_type`, `by_assignee`, blanket `factor`.

| Field | Required | Meaning |
| --- | --- | --- |
| `factor` | no | Blanket factor, exclusive minimum 0 |
| `by_pair` | no | List of `{assignee, task_type, factor}` |
| `by_task_type` | no | List of `{task_type, factor}` |
| `by_assignee` | no | List of `{assignee, factor}` |

## Roster schema

`schemas/roster.schema.json`. `schema_version` is 1.

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | Const 1 |
| `default_max_parallel` | yes | Integer >= 1. Fleet note |
| `bots` | yes | At least one entry |

Each entry:

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Assignee and owner id |
| `description` | yes | What this entry is for |
| `capabilities` | yes | Unique list of strings |
| `capacity` | yes | Integer >= 1. Concurrent tasks |
| `kind` | no | `bot`, `cloud_agent`, or `human` |
| `cost` | no | `usd_per_hour` and `tokens_per_hour`, both >= 0 |

## CLI reference

Run `python3 -m planner <command> --help` for the same text. Paths below are plan directories or a `plan.yaml` file.

### validate

Check schema, roster fit, provenance, irreversible work, cycles, and an unattested `done`.

```bash
python3 -m planner validate examples/01-solo-feature
```

Positional `plans`. With no path, every one-level plan under the plans directory, including `_template`, plus `plans/_templates/`. Exit 1 on any error. Warnings do not fail the command.

### schedule

Write `schedule.yaml` with CPM fields and resource-constrained `start` and `finish`.

```bash
python3 -m planner schedule examples/01-solo-feature
```

- `--from-now` anchors in-progress and in-review work and schedules the rest from `--at`.
- `--at` is the ISO-8601 timestamp used with `--from-now`. Default: current time.

A later plain `schedule` replaces a from-now view with the origin-based view.

### gantt

Write `gantt.md` with a table and a Mermaid chart.

```bash
python3 -m planner gantt examples/01-solo-feature
```

`--from-now` and `--at` match `schedule`.

### ready

List the dispatch queue and human gates that are waiting.

```bash
python3 -m planner ready examples/01-solo-feature
```

Positional `plans`. With no path, live plans only (names starting with `_` are skipped).

### status

Summarize progress, critical path, variance, and blocks.

```bash
python3 -m planner status examples/05-slipping-plan
```

Positional `plans`. Live plans only when the path is omitted. `schedule_variance` is actual hours over PERT expected for tasks that have `actuals.hours`.

### dispatch-dry-run

Show what a dispatcher would send, without sending it. Includes ready briefs, held gates, plan burn, 80 percent warnings, the kill switch, and the forecast. A `cloud_agent` assignee is listed like a bot, with its kind.

```bash
python3 -m planner dispatch-dry-run examples/01-solo-feature
```

Positional `plans`.

### calibrate

Estimate-versus-actual ratios by task type and assignee.

```bash
python3 -m planner calibrate
```

- `--apply` writes `calibration.yaml` from done-task actuals. Omit it for a dry diagnostic.

The scan is live plans in the plans directory, not `examples/`.

### estimate

Show raw PERT, the calibration factor, and preview hours. Writes nothing.

```bash
python3 -m planner estimate examples/05-slipping-plan
```

Positional `plan`.

### apply-calibration

Write `estimate_hours.calibrated`. Do not change optimistic, likely, or pessimistic.

```bash
python3 -m planner apply-calibration <plan> --at <ISO-8601>
```

- `--at` is the ISO-8601 timestamp for the log entry. Default: current time.

Requires `calibration.yaml`. The committed slipping example sets `calibration_override.factor` to 1.5, so this command writes preview hours and bumps `version`. Run it on a copy if you want to keep the published example.

### portfolio

```bash
python3 -m planner portfolio
python3 -m planner portfolio schedule
```

Positional `{summary,schedule}`. The default is `summary`, which writes `portfolio.md` and `portfolio.yaml`. `schedule` writes `portfolio-schedule.yaml` and levels open plans on one roster.

### check

Validate every plan, then run the unit tests with `AGENT_PLANS_DIR` unset. No extra flags. The validate half still honors `AGENT_PLANS_DIR`. This command does not check commit history. Before pushing maintainer history, run `python scripts/check_commit_hygiene.py --all && gitleaks git`.

```bash
python3 -m planner check
```

### simulate

Monte Carlo finish percentiles. Prints a report and writes nothing.

```bash
python3 -m planner simulate examples/01-solo-feature --runs 50 --seed 1
```

- `--seed` default 1.
- `--runs` default 200.
- `--add-capacity name=N` adds N to that assignee's concurrency for this run.
- `--omit TASK` drops a task id. Repeat the flag to drop more than one.
- `--omit-deliverable NAME` drops the task that produces this deliverable.
- `--deadline ISO-8601` compares p50 slack against this deadline. It does not edit the plan.

The report prints p10, p50, and p90. Samples are triangular on optimistic, likely, and pessimistic. When `calibrated` is set, the three numbers are scaled so the sample mean matches it. Sub-plan tasks keep the rolled makespan as a point.

### instantiate

Copy `plans/_templates/<kind>/plan.yaml` into a new directory and write `log.md`.

```bash
python3 -m planner instantiate research-report --into <directory> --plan-id <id> --title <title>
```

- `kind` is required. Current kinds: `research-report`, `announcement`.
- `--into` is required.
- `--plan-id` is required.
- `--title` replaces the template title. Optional.

The copied dates are placeholders. Set the goal, the deadline, and `schedule_origin` before you schedule.

### dashboard

Write `dashboard/index.md` and `dashboard/index.html` from live plans.

```bash
python3 -m planner dashboard --at <ISO-8601>
```

- `--at` pins the clock. Default: current time.

### digest

Print a meeting-prep summary and write a Slack payload. Posts only with `--post`.

```bash
python3 -m planner digest --at <ISO-8601>
```

- `--at` pins the clock. Default: current time.
- `--post` POSTs the payload to `SLACK_DIGEST_WEBHOOK`. Dry run when omitted.

### serve

Serve the generated dashboard. This process stays in the foreground.

```bash
python3 -m planner serve --host 127.0.0.1 --port 8787 --refresh 300 --at <ISO-8601>
```

- `--host` default `127.0.0.1`.
- `--port` default 8787.
- `--refresh` regenerates views every N seconds and asks the browser to reload. Default 0, which does not refresh.
- `--at` pins regenerate to this ISO-8601 timestamp. Default: current time.

### brief

Write a self-contained markdown brief for a task.

```bash
python3 -m planner brief examples/01-solo-feature
python3 -m planner brief examples/01-solo-feature T1
```

Positional `plan`, and optional `task`. With no task id, every dispatchable task is written to `briefs/<task_id>.md`. The brief has the goal, the objective, input paths and sha256 values when a manifest exists, expected outputs, acceptance criteria, the estimate, the budget, retries left, and a JSON report-back. It says not to expand scope and not to set `done`.

### record-artifact

Hash an output into `artifacts/<task>/manifest.yaml`.

```bash
python3 -m planner record-artifact <plan> <task> --file <path>
```

- `--file` is required.
- `--name` is the output artifact name. Default: the file name.
- `--producer` is a roster name. Default: the task assignee.
- `--attempt` is an integer. Optional.

### attest-done

Write `attestations/<task>.yaml`. Does not edit `plan.yaml`. `attest` is an alias with the same flags.

```bash
python3 -m planner attest-done <plan> <task> --verdict pass --at <ISO-8601>
python3 -m planner attest <plan> <task> --verdict fail --notes "what failed"
```

- `--verdict` is required: `pass` or `fail`.
- `--notes` defaults to empty.
- `--at` is the ISO-8601 timestamp. Default: current time.

The reviewer recorded by this command is `accuracy`.

### complete

Set `done` when a pass attestation from `accuracy` exists.

```bash
python3 -m planner complete <plan> <task> --hours <n> --attempts <n> --at <ISO-8601>
```

- `--hours` is required.
- `--attempts` is required, and at least 1.
- `--start` and `--end` are optional ISO-8601 timestamps.
- `--at` is used when start or end is omitted.

A fail attestation, a missing manifest, or a hash mismatch is refused.

### set-status

Set a status other than `done`, and optionally record actuals.

```bash
python3 -m planner set-status <plan> <task> in_progress --hours 0.5 --attempts 1 --start <ISO-8601>
```

Positional `status` choices: `planned`, `ready`, `in_progress`, `in_review`, `done`, `blocked`, `failed`, `cancelled`. `done` is refused.

- `--hours`, `--attempts`, `--start`, `--end`, and `--at` are optional.

Use `failed` or `in_review` after a fail attestation.

### replan

Freeze in-progress work, reschedule from now, regenerate views, append `log.md`, and bump `version`.

```bash
python3 -m planner replan examples/05-slipping-plan --now --reason "implementation ran long" --at 2026-10-08T15:00:00-05:00
```

- `--now` is required. It means schedule from `--at`.
- `--reason` is required.
- `--at` is the ISO-8601 timestamp for now. Default: current time.
- `--force` bypasses the rate limit and hysteresis.
- `--human` means a person asked. It bypasses the rate limit and still honors hysteresis.

A second replan inside `min_interval_minutes` is refused. A plan with a `failed` task also bypasses the rate limit. If the makespan moved by less than `hysteresis_hours` and the critical path is unchanged, the command prints `suppressed by hysteresis` and does not write. In-progress and in-review tasks keep their start on `actuals.start`. This command does not change assignees or the goal.

The committed slipping example is the plan before that replan. Running the command updates the copy you have. On that file it prints:

```
replan wrote schedule.yaml and gantt.md at version 2. Frozen: T1. Makespan 3h -> 7h.
```

## Workflows

### Planner

The Planner is the only writer of `plan.yaml`. Other bots report a task id, a status, actual hours, and where the artifacts are. The Planner batches those reports into the file.

1. Shape nothing yourself if an Orchestrator already wrote the goal. Otherwise set the goal, the definition of done, the deadline, and the hour budget.
2. Start from a template or from `examples/`, then edit assignees, estimates, and dependencies. Leave `estimate_hours.calibrated` unset.
3. Validate, schedule, and render the Gantt.

```bash
python3 -m planner validate examples/01-solo-feature
python3 -m planner schedule examples/01-solo-feature
python3 -m planner gantt examples/01-solo-feature
```

4. When a worker reports a status other than `done`, record it with `set-status`. Do not type `done` into the file.
5. After Accuracy's attestation is a pass and the manifest hashes match, run `complete` with hours and attempts (at least 1).
6. When actuals slip, run `replan`. Skip it when the origin schedule is still right.
7. After a done task has actual hours, run `calibrate --apply`, then `estimate`, then `apply-calibration` on the next plan. The raw triple stays.

`version` bumps on `set-status`, `complete`, and `replan`. `apply-calibration` bumps `version` when any calibrated value changes. Append a log entry. Those commands append one for you.

### Orchestrator

The Orchestrator shapes the ask and hands work out. The Orchestrator does not write `plan.yaml` and does not set `done`.

1. Write the goal, the constraints, the deadline, and the budget, and give them to the Planner.
2. Read the queue. Human gates are not yours to skip.

```bash
python3 -m planner ready examples/02-content-launch
python3 -m planner brief examples/02-content-launch
```

3. Hand `briefs/<task>.md` to the assignee named in the brief. The brief says not to expand scope.
4. When the assignee finishes, they run `record-artifact` and stop at `in_review`. You report that status to the Planner. You do not mark the task `done`.
5. `dispatch-dry-run` is the preview of budget headroom and the kill switch. It does not send the brief.

```bash
python3 -m planner dispatch-dry-run examples/02-content-launch
```

### Human reviewer

A human gate waits in `awaiting_human` after its dependencies are satisfied. Until then it is absent from both lists. Confirm that with `ready` and `status`.

The reviewer's job is the decision named in `human_approval_policy` and in the task objective: approve or hold, publish or hold, merge or hold. Record it in the task's output artifact, including the person and the time. Then either:

- a worker sets the task `in_progress` while that record is written, and stops at `in_review`, or
- Accuracy reviews the record with `attest-done`, and the Planner runs `complete` only on a pass.

Do not edit `plan.yaml` to force `done`. Irreversible tasks already have `needs_human: true`. Holding is the safe default. Work that the gate does not block continues.

A slipping schedule is still the Planner's `replan`, not a silent edit of the goal. A goal change, a dropped deliverable, or a new deadline is a human decision. Record it in `log.md` before the Planner edits the goal. `replan --now` does not make that edit. `--human` on `replan` only bypasses the rate limit.
