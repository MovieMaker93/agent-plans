# SPDX-License-Identifier: Apache-2.0
"""Ready queue, status, and estimate calibration.

These commands recompute the schedule from plan.yaml. schedule.yaml is a
generated view, not a second source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from planner.model import Plan, Roster
from planner.schedule import ScheduleResult, schedule_tasks
from planner.util import add_hours, fmt_hours, fmt_percent, fmt_ratio, parse_datetime

STARTED = {"in_progress", "in_review", "done"}


def schedule_plan(plan: Plan, roster: Roster) -> ScheduleResult:
    from planner.subplan import rollup_durations

    overrides, rolled = rollup_durations(plan, roster)
    capacity = {bot.name: bot.capacity for bot in roster.bots}
    result = schedule_tasks(
        plan.tasks,
        capacity,
        plan.max_parallel,
        duration_overrides=overrides or None,
    )
    result.rolled_status = rolled
    return result


def deadline_warning(plan: Plan, result: ScheduleResult) -> str | None:
    if not plan.deadline:
        return None
    origin = parse_datetime(plan.schedule_origin, "constraints.schedule_origin")
    deadline = parse_datetime(plan.deadline, "constraints.deadline")
    finish = add_hours(origin, result.makespan)
    if finish > deadline:
        return (
            f"{plan.plan_id}: resource-constrained finish {finish.isoformat()} "
            f"is after deadline {plan.deadline}"
        )
    return None


def _known(plan: Plan) -> dict:
    return {task.id: task for task in plan.tasks}


def deps_satisfied(task, known: dict) -> bool:
    for dep in task.depends_on:
        pred = known.get(dep.task)
        if pred is None or pred.status == "cancelled":
            return False
        if dep.type in {"FS", "FF"}:
            if pred.status != "done":
                return False
        elif pred.status not in STARTED:
            return False
    return True


def is_dispatchable(task, known: dict) -> bool:
    return (
        task.status in {"planned", "ready"}
        and not task.needs_human
        and deps_satisfied(task, known)
    )


def is_awaiting_human(task, known: dict) -> bool:
    return (
        task.needs_human
        and task.status in {"planned", "ready", "blocked"}
        and deps_satisfied(task, known)
    )


def _sort_key(plan_result: ScheduleResult):
    def key(task):
        timing = plan_result.by_id.get(task.id)
        start = timing.start if timing else Decimal(0)
        return (start, task.id)

    return key


def _task_line(task) -> str:
    hours = fmt_hours(task.estimate_hours.expected)
    lags = [dep.lag_hours for dep in task.depends_on if dep.lag_hours != 0]
    lag = ""
    if lags:
        lag = " | lag " + ", ".join(fmt_hours(item) + "h" for item in lags)
    return f"  {task.id} | {task.assignee} | {hours}h | {task.title}{lag}"


def render_ready(plan: Plan, result: ScheduleResult) -> str:
    known = _known(plan)
    key = _sort_key(result)
    dispatch = sorted(
        (task for task in plan.tasks if is_dispatchable(task, known)),
        key=key,
    )
    waiting = sorted(
        (task for task in plan.tasks if is_awaiting_human(task, known)),
        key=key,
    )
    lines = [f"plan: {plan.plan_id}", "dispatch:"]
    lines.extend(_task_line(task) for task in dispatch)
    if not dispatch:
        lines.append("  (none)")
    lines.append("awaiting_human:")
    lines.extend(_task_line(task) for task in waiting)
    if not waiting:
        lines.append("  (none)")
    return "\n".join(lines)


def _variance_line(plan: Plan) -> str:
    actual = Decimal(0)
    expected = Decimal(0)
    count = 0
    for task in plan.tasks:
        if task.actuals.hours is None:
            continue
        duration = task.estimate_hours.expected
        if duration == 0:
            continue
        actual += task.actuals.hours
        expected += duration
        count += 1
    if count == 0:
        return "schedule_variance: n/a"
    ratio = actual / expected
    return (
        "schedule_variance: "
        f"{fmt_hours(actual)}h actual / {fmt_hours(expected)}h expected "
        f"(n={count}, pooled ratio {fmt_ratio(ratio)})"
    )


def _id_list(tasks) -> list[str]:
    if not tasks:
        return ["  (none)"]
    return [f"  {task.id} | {task.title}" for task in tasks]


def render_status(plan: Plan, result: ScheduleResult) -> str:
    known = _known(plan)
    active = [task for task in plan.tasks if task.status != "cancelled"]
    done = [task for task in active if task.status == "done"]
    folder = plan.directory.name if plan.directory else plan.plan_id
    failed = [task for task in plan.tasks if task.status == "failed"]
    blocked = [task for task in plan.tasks if task.status == "blocked"]
    waiting = [
        task for task in plan.tasks if is_awaiting_human(task, known)
    ]
    lines = [
        f"plan: {plan.plan_id}",
        f"version: {plan.version}",
        f"directory: {folder}",
        f"progress: {len(done)}/{len(active)} done ({fmt_percent(len(done), len(active))}%)",
        f"critical_path: {' -> '.join(result.critical_path) or '(none)'}",
        f"critical_tasks: {', '.join(result.critical_tasks) or '(none)'}",
        f"cpm_duration_hours: {fmt_hours(result.cpm_duration)}",
        f"cpm_std_dev_hours: {fmt_hours(result.cpm_std_dev)}",
        f"makespan_hours: {fmt_hours(result.makespan)}",
        f"effort_hours: {fmt_hours(result.effort)}",
        _variance_line(plan),
        "failed:",
        *_id_list(failed),
        "blocked:",
        *_id_list(blocked),
        "awaiting_human:",
        *_id_list(waiting),
    ]
    return "\n".join(lines)


@dataclass
class Sample:
    task_type: str
    assignee: str
    expected: Decimal
    actual: Decimal
    optimistic: Decimal
    pessimistic: Decimal


def collect_samples(plans: list[Plan]) -> tuple[list[Sample], int]:
    samples: list[Sample] = []
    skipped = 0
    for plan in plans:
        for task in plan.tasks:
            if task.actuals.hours is None:
                continue
            expected = task.estimate_hours.expected
            if expected == 0:
                skipped += 1
                continue
            samples.append(
                Sample(
                    task_type=task.task_type,
                    assignee=task.assignee,
                    expected=expected,
                    actual=task.actuals.hours,
                    optimistic=task.estimate_hours.optimistic,
                    pessimistic=task.estimate_hours.pessimistic,
                )
            )
    return samples, skipped


def _group_line(name: str, samples: list[Sample]) -> str:
    count = len(samples)
    actual = sum((sample.actual for sample in samples), Decimal(0))
    expected = sum((sample.expected for sample in samples), Decimal(0))
    pooled = actual / expected
    mean = sum(
        (sample.actual / sample.expected for sample in samples), Decimal(0)
    ) / Decimal(count)
    inside = sum(
        1
        for sample in samples
        if sample.optimistic <= sample.actual <= sample.pessimistic
    )
    return (
        f"  {name}  n={count}  pooled_ratio={fmt_ratio(pooled)}  "
        f"mean_ratio={fmt_ratio(mean)}  inside_range={inside}/{count}"
    )


def render_calibration(plans: list[Plan]) -> str:
    samples, skipped = collect_samples(plans)
    lines = [
        "calibration",
        f"plans: {len(plans)}",
        f"samples: {len(samples)}",
        f"skipped_zero_estimate: {skipped}",
    ]
    if not samples:
        lines.extend(["by_task_type:", "  (none)", "by_assignee:", "  (none)"])
        return "\n".join(lines)

    lines.append("overall:")
    lines.append(_group_line("all", samples))

    def grouped(attr: str) -> list[str]:
        buckets: dict[str, list[Sample]] = {}
        for sample in samples:
            buckets.setdefault(getattr(sample, attr), []).append(sample)
        return [_group_line(name, buckets[name]) for name in sorted(buckets)]

    lines.append("by_task_type:")
    lines.extend(grouped("task_type"))
    lines.append("by_assignee:")
    lines.extend(grouped("assignee"))
    lines.append("write_back: python -m planner calibrate --apply")
    lines.append("preview: python -m planner estimate <plan>")
    lines.append("store: python -m planner apply-calibration <plan>")
    return "\n".join(lines)
