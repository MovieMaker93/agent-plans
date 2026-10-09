# Agent plan store

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Agent-plans is a planning framework for a team of AI agents. Each project is a `plan.yaml` task graph. A command-line planner validates that graph, schedules it against a roster, renders a Gantt chart, and keeps a ready queue. A task becomes `done` only after Accuracy, the verifier, writes a passing attestation and the Planner records it. Human gates stay out of the dispatch queue until a person decides.

## Core features

- A YAML plan per project, with a JSON Schema and a validator for roster fit, artifact provenance, dependency cycles, and the Accuracy done-lock
- Critical-path slack and a resource-constrained schedule that honors assignee capacity and each plan's `max_parallel`
- Mermaid Gantt charts from the resource-constrained times
- A ready queue that separates dispatchable tasks from human gates
- Task briefs, sha256 artifact manifests, and an Accuracy attestation that is the only path to `done`
- Replanning from a chosen clock, freezing in-progress and in-review work, with a rate limit and hysteresis
- Calibration from done-task actuals, plus a preview that does not rewrite the raw optimistic, likely, and pessimistic hours
- A portfolio rollup and a shared-roster schedule across open plans
- Sub-plan rollup of makespan and status
- Monte Carlo finish percentiles and what-if knobs that do not edit the plan
- `research-report` and `announcement` templates
- A static dashboard and a read-only local server
- A meeting digest that writes a Slack payload and posts only when `SLACK_DIGEST_WEBHOOK` is set and `--post` is passed
- A dispatch dry run that reports budget headroom, an 80 percent warning, and tasks a kill switch would stop, and sends nothing

## Quickstart

Python 3.11 or newer. PyYAML is the only Python package dependency. The commit-hygiene tests need the `git` executable, and they skip when `git` is missing.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m planner validate examples/01-solo-feature
python3 -m planner schedule examples/01-solo-feature
python3 -m planner gantt examples/01-solo-feature
python3 -m planner ready examples/01-solo-feature
```

`validate` prints `ok` when the plan matches the roster and the graph rules. `schedule` writes `schedule.yaml` (critical path `T1 -> T2 -> M1`, makespan 3h). `gantt` writes `gantt.md`, including a Mermaid chart. `ready` prints the dispatch queue:

```
plan: solo-feature-2026-10
dispatch:
  T1 | cloud-worker | 2h | Implement the feature
awaiting_human:
  (none)
```

The merge gate is not waiting yet, because its dependency is still open. More plans, and the commands that move a task through review, are in [docs/scenarios.md](docs/scenarios.md).

To start a plan of your own:

```bash
python3 -m planner instantiate research-report --into plans/<YYYY-MM-DD>-<slug> --plan-id <id>
```

Then edit the goal, the deadline, and `schedule_origin`, and run `validate`, `schedule`, and `gantt` on that directory.

## Documentation

- [User guide](docs/user-guide.md): concepts, the `plan.yaml` schema, every subcommand, and the Planner, Orchestrator, and human-reviewer workflows
- [Scenarios](docs/scenarios.md): six runnable plans under `examples/`
- [Core contract](docs/core.md): the everyday loop for this repository
- [Vision](docs/vision.md): what is implemented and the follow-ons not built yet.

Live plans are read from `plans/` unless `AGENT_PLANS_DIR` points somewhere else. Templates stay in `plans/_templates`.

## Tests

```bash
python3 -m planner check
```

That validates every plan under `plans/` (or under `AGENT_PLANS_DIR` when it is set), including the template library, and runs the unit tests. The unit-test half clears `AGENT_PLANS_DIR` so snapshot comparisons stay on this checkout. It does not check this repository's commit history.

`scripts/check_commit_hygiene.py` checks commits reachable from HEAD, or every ref with `--all` (`refs/stash` is skipped). Only the name rule is limited to `main`. Before pushing that history:

```bash
python scripts/check_commit_hygiene.py --all && gitleaks git
```

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Copyright 2026 mr-r0b0t
