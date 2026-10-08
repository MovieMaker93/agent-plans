# Template library

Each directory is a kind a new plan can start from. `research-report` is one reversible task and has no human gate. `announcement` adds a publish task that waits for a person. After the copy, set the goal, the deadline, and `schedule_origin`. The everyday loop is `docs/core.md`.

```bash
python -m planner instantiate announcement --into plans/<YYYY-MM-DD>-<slug> --plan-id <id>
python -m planner instantiate research-report --into plans/<YYYY-MM-DD>-<slug> --plan-id <id>
```

The copy sets `plan_id` and writes a fresh `log.md`. `template_kind` stays on the plan so `calibrate --apply` can attach history in `calibration.yaml` under `by_kind`.

These directories sit under `plans/_templates/`, one level deeper than `plans/`. `ready`, `portfolio`, and `schedule` without a path do not pick them up. `validate` with no path does, so a broken template fails `python -m planner check`.
