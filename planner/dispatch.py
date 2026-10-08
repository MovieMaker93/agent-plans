# SPDX-License-Identifier: Apache-2.0
"""Dry run of what a Dispatcher would send. Nothing is sent.

This is the queue plus budget headroom. The live webhook dispatcher is a
later phase. Gates (`needs_human`) stay held. A task at 100% of its hour
budget, or out of retries, is on the kill switch. At 80% it is a warning
and still dispatchable. At 100% of the plan hour budget, new dispatch halts.
"""

from __future__ import annotations

from decimal import Decimal

from planner.model import TERMINAL, Plan, Task
from planner.reports import ScheduleResult, is_awaiting_human, is_dispatchable
from planner.util import fmt_hours, fmt_ratio


def _retries_left(task: Task) -> int:
    left = task.max_retries - task.actuals.attempts
    return left if left > 0 else 0


def _flags(task: Task) -> tuple[list[str], bool]:
    """Kill reasons, and whether the task is in the 80% warning band."""
    reasons: list[str] = []
    warn = False
    if task.status in {"done", "cancelled"}:
        return reasons, warn
    hours = task.actuals.hours
    if task.budget.hours > 0 and hours is not None:
        if hours >= task.budget.hours:
            reasons.append("budget")
        elif hours >= task.budget.hours * Decimal("0.8"):
            warn = True
    if (
        task.actuals.attempts > 0
        and task.actuals.attempts >= task.max_retries
        and task.status not in TERMINAL
    ):
        reasons.append("retries")
    return reasons, warn


def _headroom(task: Task) -> str:
    if task.budget.hours == 0:
        return "n/a"
    used = task.actuals.hours if task.actuals.hours is not None else Decimal(0)
    left = task.budget.hours - used
    if left < 0:
        left = Decimal(0)
    return fmt_hours(left) + "h"


def render_dispatch(plan: Plan, result: ScheduleResult, roster=None) -> str:
    known = {task.id: task for task in plan.tasks}
    actual = sum(
        (task.actuals.hours for task in plan.tasks if task.actuals.hours is not None),
        Decimal(0),
    )
    halt = plan.budget.hours > 0 and actual >= plan.budget.hours
    burn = "n/a"
    if plan.budget.hours > 0:
        burn = fmt_ratio(actual / plan.budget.hours)
    dispatch = []
    warnings = []
    killed = []
    for task in plan.tasks:
        reasons, warn = _flags(task)
        if reasons and task.status not in {"done", "cancelled"}:
            killed.append((task, reasons))
        if warn and task.status not in {"done", "cancelled"}:
            warnings.append(task)
        if is_dispatchable(task, known) and not reasons:
            dispatch.append(task)
    waiting = [task for task in plan.tasks if is_awaiting_human(task, known)]

    def sort_key(task: Task):
        timing = result.by_id.get(task.id)
        start = timing.start if timing else Decimal(0)
        return (start, task.id)

    dispatch.sort(key=sort_key)
    waiting.sort(key=sort_key)
    warnings.sort(key=lambda task: task.id)
    killed.sort(key=lambda item: item[0].id)

    lines = [
        f"plan: {plan.plan_id}",
        f"plan_budget_hours: {fmt_hours(plan.budget.hours)}",
        f"plan_actual_hours: {fmt_hours(actual)}",
        f"plan_burn: {burn}",
        f"halt_new_dispatch: {'yes' if halt else 'no'}",
        "dispatch:",
    ]
    if halt:
        lines.append("  (halted: plan hour budget is spent)")
    elif not dispatch:
        lines.append("  (none)")
    else:
        for task in dispatch:
            kind = ""
            if roster is not None:
                bot = roster.get(task.assignee)
                if bot is not None and bot.kind == "cloud_agent":
                    kind = " | kind cloud_agent"
            lines.append(
                f"  {task.id} | {task.assignee} | briefs/{task.id}.md | "
                f"headroom {_headroom(task)} | retries_left {_retries_left(task)} | budget ok{kind}"
            )
    lines.append("awaiting_human:")
    if waiting:
        for task in waiting:
            lines.append(f"  {task.id} | {task.assignee} | {task.title}")
    else:
        lines.append("  (none)")
    lines.append("warnings:")
    if warnings:
        for task in warnings:
            lines.append(f"  {task.id} | budget >= 80% | headroom {_headroom(task)}")
    else:
        lines.append("  (none)")
    lines.append("kill_switch:")
    if killed:
        for task, reasons in killed:
            lines.append(f"  {task.id} | {', '.join(reasons)} | retries_left {_retries_left(task)}")
    else:
        lines.append("  (none)")
    from planner.forecast import format_forecast, project

    forecast = project(plan, result, roster)
    lines.append("forecast:")
    lines.extend(format_forecast(forecast))
    lines.append(f"forecast_hold: {'yes' if forecast.hold else 'no'}")
    return "\n".join(lines)
