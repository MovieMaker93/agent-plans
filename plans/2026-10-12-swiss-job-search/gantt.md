# Gantt

Generated from `plan.yaml` by `python -m planner gantt`. Do not edit by hand.

- Plan: swiss-job-search-2026-10 v1
- Origin: 2026-10-12T09:00:00+02:00
- CPM duration: 34h (std dev 2.8087h along the critical path)
- Resource-constrained makespan: 34h
- Critical path: T1, T2, T3, T4, M1, T5
- `crit` means unconstrained CPM slack is zero. Bar times are the resource-constrained schedule.

| ID | Start | Finish | Slack | Assignee | Critical |
| --- | --- | --- | --- | --- | --- |
| T1 | 0 | 7 | 0 | job-hunter | yes |
| T2 | 7 | 16 | 0 | searcher | yes |
| T3 | 16 | 28 | 0 | tailor | yes |
| T4 | 28 | 32 | 0 | accuracy | yes |
| M1 | 32 | 32 | 0 | human | yes |
| T5 | 32 | 34 | 0 | human | yes |

```mermaid
gantt
    title Swiss DevSecOps job search v1
    dateFormat YYYY-MM-DD HH:mm
    axisFormat %m-%d %H:%M
    section Search
    T1 Find matching Swiss roles - job-hunter :crit, t1, 2026-10-12 09:00, 7h
    section Research
    T2 Research each company - searcher :crit, t2, 2026-10-12 16:00, 9h
    section Applications
    T3 Tailor CV and cover letter - tailor :crit, t3, 2026-10-13 01:00, 12h
    section Review
    T4 Verify CV matches the posting - accuracy :crit, t4, 2026-10-13 13:00, 4h
    section Gate
    M1 Approve applications - human :milestone, crit, m1, 2026-10-13 17:00, 0m
    T5 Submit approved applications - human :crit, t5, 2026-10-13 17:00, 2h
```
