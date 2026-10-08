# Plan template

This directory is the annotated field guide for a custom graph. For a one-task research note or a launch with a publish gate, use `instantiate` instead. The everyday loop is `docs/core.md`.

Copy this directory to `plans/YYYY-MM-DD-<slug>/`, then edit `plan.yaml`.

The comments in `plan.yaml` are the field guide. `schemas/plan.schema.json` is the editor contract. After edits, from the repo root:

```bash
python -m planner estimate plans/YYYY-MM-DD-<slug>
python -m planner apply-calibration plans/YYYY-MM-DD-<slug>
python -m planner validate plans/YYYY-MM-DD-<slug>
python -m planner schedule plans/YYYY-MM-DD-<slug>
python -m planner gantt plans/YYYY-MM-DD-<slug>
```

Run `python -m planner calibrate --apply` first when any live plan has done tasks with actual hours. `apply-calibration` writes `estimate_hours.calibrated` and leaves optimistic, likely, and pessimistic alone.

Append a dated entry to `log.md` for the new version. Commit `plan.yaml`, `log.md`, `schedule.yaml`, and `gantt.md`.

When a task is ready, `python -m planner brief plans/YYYY-MM-DD-<slug>` writes `briefs/`. Workers put outputs in `artifacts/<task_id>/` and run `record-artifact`. Accuracy writes `attestations/<task_id>.yaml`. The Planner sets `done` with `complete`.

`python -m planner replan plans/YYYY-MM-DD-<slug> --now --reason "..."` reschedules from the current time. In-progress and in-review tasks stay frozen. Defaults: 60 minutes between replans, 0.5h hysteresis, 1h assignee freeze window. `replan_policy` in `plan.yaml` overrides them.

Directories whose names start with `_` are patterns. `ready`, `status`, `calibrate`, and `portfolio` skip them.

For a head start, `python -m planner instantiate announcement` or `instantiate research-report` copies `plans/_templates/<kind>/` instead of this annotated sample. Optional plan fields: `priority` (higher wins in `portfolio schedule`), `template_kind`, `calibration_override`, and on a task `subplan: plans/<directory>`.
