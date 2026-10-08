# SPDX-License-Identifier: Apache-2.0
"""Parent-task rollup from a child plan.

A task may set `subplan: plans/<directory>`. The parent's scheduling
duration becomes the child's makespan. The parent's displayed status
follows the child. plan.yaml is not rewritten.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from planner.model import LoadError, Plan, Roster
from planner.schedule import ScheduleError, schedule_tasks
from planner.util import repo_of, resolve_plan_reference
from planner.yamlio import YamlError


def subplan_path(plan: Plan, ref: str) -> Path | None:
    """Resolve `plans/<id>` from the repo that holds this plan."""
    root = repo_of(plan.directory)
    if root is None:
        return None
    path = resolve_plan_reference(root, ref)
    if path is None:
        return None
    if path.is_file() and path.name == "plan.yaml":
        return path
    return path / "plan.yaml"


def _identity(plan: Plan) -> str:
    if plan.path is not None:
        return str(plan.path.resolve())
    return plan.plan_id


def _load(path: Path) -> Plan:
    from planner.model import load_plan

    return load_plan(path)


def rolled_status(plan: Plan, roster: Roster, stack: tuple[str, ...] = ()) -> str:
    """Status a parent should display. Does not write the plan."""
    identity = _identity(plan)
    if identity in stack:
        raise ScheduleError("subplan cycle: " + " -> ".join(stack + (identity,)))
    active = [task for task in plan.tasks if task.status != "cancelled"]
    if not active:
        return "cancelled"
    statuses: list[str] = []
    child_stack = stack + (identity,)
    for task in active:
        if task.subplan:
            path = subplan_path(plan, task.subplan)
            if path is None or not path.is_file():
                statuses.append(task.status)
                continue
            try:
                child = _load(path)
            except (LoadError, YamlError):
                statuses.append(task.status)
                continue
            statuses.append(rolled_status(child, roster, child_stack))
        else:
            statuses.append(task.status)
    unique = set(statuses)
    if unique <= {"done"}:
        return "done"
    if "failed" in unique:
        return "failed"
    if unique & {"in_progress", "in_review"} or "done" in unique:
        return "in_progress"
    if "blocked" in unique:
        return "blocked"
    return "planned"


def rollup_durations(
    plan: Plan,
    roster: Roster,
    stack: tuple[str, ...] = (),
) -> tuple[dict[str, Decimal], dict[str, str]]:
    """Return (duration overrides, rolled status) for tasks that set subplan.

    An empty override map means this plan has no sub-plans. Callers should
    pass None into the scheduler in that case so an ordinary plan is unchanged.
    """
    identity = _identity(plan)
    if identity in stack:
        raise ScheduleError("subplan cycle: " + " -> ".join(stack + (identity,)))
    overrides: dict[str, Decimal] = {}
    rolled: dict[str, str] = {}
    child_stack = stack + (identity,)
    capacity = {bot.name: bot.capacity for bot in roster.bots}
    for task in plan.tasks:
        if not task.subplan or task.status == "cancelled":
            continue
        path = subplan_path(plan, task.subplan)
        if path is None or not path.is_file():
            raise ScheduleError(f"{task.id}: subplan '{task.subplan}' was not found")
        try:
            child = _load(path)
        except (LoadError, YamlError) as exc:
            raise ScheduleError(f"{task.id}: cannot load subplan {task.subplan}: {exc}") from exc
        child_overrides, _child_rolled = rollup_durations(child, roster, child_stack)
        result = schedule_tasks(
            child.tasks,
            capacity,
            child.max_parallel,
            duration_overrides=child_overrides or None,
        )
        overrides[task.id] = result.makespan
        rolled[task.id] = rolled_status(child, roster, child_stack)
    return overrides, rolled


def check_subplans(plan: Plan, roster: Roster, found, stack: tuple[str, ...] = ()) -> None:
    """Append errors for a missing path, a cycle, or a done parent over open work."""
    identity = _identity(plan)
    if identity in stack:
        found.errors.append("subplan cycle: " + " -> ".join((*stack, identity)))
        return
    child_stack = stack + (identity,)
    for task in plan.tasks:
        if not task.subplan:
            continue
        path = subplan_path(plan, task.subplan)
        if path is None or not path.is_file():
            found.errors.append(f"{task.id}: subplan '{task.subplan}' was not found")
            continue
        try:
            child = _load(path)
        except LoadError as exc:
            found.errors.append(f"{task.id}: cannot load subplan {task.subplan}: {exc}")
            continue
        except YamlError as exc:
            found.errors.append(f"{task.id}: cannot load subplan {task.subplan}: {exc}")
            continue
        if _identity(child) in child_stack:
            found.errors.append(
                "subplan cycle: " + " -> ".join((*child_stack, _identity(child)))
            )
            continue
        try:
            status = rolled_status(child, roster, child_stack)
        except ScheduleError as exc:
            found.errors.append(str(exc))
            continue
        if task.status == "done" and status != "done":
            found.errors.append(
                f"{task.id}: status is done but subplan {task.subplan} rolls up to {status}"
            )
        elif task.status != status:
            found.warnings.append(
                f"{task.id}: status is {task.status} but subplan {task.subplan} rolls up to {status}"
            )
        check_subplans(child, roster, found, child_stack)
