# Contributing

Thanks for looking at agent-plans. Issues and changes are welcome.

## License

By submitting a change you agree it is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).

## Setup

Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m planner check
```

`python3 -m planner check` validates every plan under `plans/` (or `AGENT_PLANS_DIR` when it is set) and runs the tests. The unit-test half clears `AGENT_PLANS_DIR` so snapshot comparisons stay on this checkout. Run it before you send a change.

There is no separate formatter or linter configuration. Match the style of the file you edit.

## What to change

The scheduler is a heuristic. Resource-constrained scheduling is NP-hard. A change that only updates status or docs should not rewrite the scheduler to chase an optimum.

`schedule.yaml`, `gantt.md`, `briefs/`, `portfolio.md`, `portfolio.yaml`, `portfolio-schedule.yaml`, `calibration.yaml`, and `dashboard/` are generated. Change `plan.yaml` or the generator, then regenerate. Do not hand-edit those views.

`examples/` holds public sample plans. Each one must pass `validate` and `schedule`. `plans/` may also hold live project plans for the people who use this checkout. Do not delete or rewrite those as part of an unrelated change.

`AGENT_PLANS_DIR` selects the live plan directory. The default is `plans/` inside the repository. Keep that default working.

## Commit identity

The identity rule applies to maintainer commits on `main`. Those commits use the author and committer name `mr-r0b0t` and the address `noreply@example.com`. An outside contribution should use your own name and address. Maintainers rewrite contributed commits to that identity when they land on `main`.

`scripts/check_commit_hygiene.py` runs when you invoke it. It is not part of `python3 -m planner check`. The GitLab CI file runs it only on GitLab, in its own job, on the default branch and on tags. Before a maintainer pushes `main`:

```bash
python scripts/check_commit_hygiene.py --all && gitleaks git
```

## Secrets

Do not commit tokens, webhook URLs, private keys, or Tailscale hostnames. The Slack digest reads `SLACK_DIGEST_WEBHOOK` from the environment and only when `--post` is set. Tests use a placeholder URL.

## Done, in this tool

A task in a plan is `done` only when Accuracy has a passing attestation and the Planner runs `complete`. `set-status` refuses `done`. That rule is about plans the tool stores. It is not a rule about how to mark a code review.
