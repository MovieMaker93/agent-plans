# Gantt

Generated from `plan.yaml` by `python -m planner gantt`. Do not edit by hand.

- Plan: content-launch-2026-10 v1
- Origin: 2026-10-08T09:00:00-05:00
- CPM duration: 6h (std dev 0h along the critical path)
- Resource-constrained makespan: 6h
- Critical path: T1, T2, T3, T4, M1
- `crit` means unconstrained CPM slack is zero. Bar times are the resource-constrained schedule.

| ID | Start | Finish | Slack | Assignee | Critical |
| --- | --- | --- | --- | --- | --- |
| T1 | 0 | 2 | 0 | research | yes |
| T2 | 2 | 4 | 0 | social | yes |
| T3 | 4 | 5 | 0 | creative-director | yes |
| T4 | 5 | 6 | 0 | accuracy | yes |
| M1 | 6 | 6 | 0 | human | yes |

```mermaid
gantt
    title Multi-agent content launch v1
    dateFormat HH:mm
    axisFormat %H:%M
    section Research
    T1 Research the announcement - research :crit, t1, 09:00, 2h
    section Writing
    T2 Write the announcement - social :crit, t2, 11:00, 2h
    section Design
    T3 Design the image - creative-director :crit, t3, 13:00, 1h
    section Review
    T4 Check the package - accuracy :crit, t4, 14:00, 1h
    section Gate
    M1 Approve publication - human :milestone, crit, m1, 15:00, 0m
```
