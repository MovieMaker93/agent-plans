# SPDX-License-Identifier: Apache-2.0
"""Schema and graph checks that run before a schedule is trusted.

The JSON Schema in schemas/ is the editor contract. This module is what
`planner validate` runs: unknown fields, roster fit, artifact provenance,
irreversible work, and cycles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal

from planner.attest import done_lock_errors
from planner.model import Plan, Roster, Task
from planner.schedule import ScheduleError, find_cycle
from planner.util import parse_datetime

TASK_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


@dataclass
class Findings:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _ancestors(task_id: str, preds: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(preds.get(task_id, []))
    while stack:
        node = stack.pop()
        if node in seen or node not in preds:
            continue
        seen.add(node)
        stack.extend(preds[node])
    return seen


def _by_id(plan: Plan) -> dict[str, Task]:
    return {task.id: task for task in plan.tasks}


def validate_plan(plan: Plan, roster: Roster) -> Findings:
    found = Findings()
    if roster.get(plan.owner) is None:
        found.errors.append(f"owner '{plan.owner}' is not in the roster")

    origin = _optional_datetime(plan.schedule_origin, "constraints.schedule_origin", found)
    deadline = None
    if plan.deadline:
        deadline = _optional_datetime(plan.deadline, "constraints.deadline", found)
    if origin is not None and deadline is not None and deadline <= origin:
        found.errors.append("constraints.deadline must be after schedule_origin")

    seen_ids: set[str] = set()
    for task in plan.tasks:
        if not TASK_ID.match(task.id):
            found.errors.append(
                f"{task.id or '(blank)'}: id must match {TASK_ID.pattern}"
            )
        if task.id in seen_ids:
            found.errors.append(f"duplicate task id '{task.id}'")
        seen_ids.add(task.id)

    produced: dict[str, str] = {}
    for task in plan.tasks:
        for output in task.outputs:
            previous = produced.get(output.artifact)
            if previous:
                found.errors.append(
                    f"{task.id}: output '{output.artifact}' is also produced by {previous}"
                )
            else:
                produced[output.artifact] = task.id

    provided = {item.artifact for item in plan.provided_inputs}
    for name in provided:
        if name in produced:
            found.errors.append(
                f"provided input '{name}' is also produced by {produced[name]}"
            )
    if len(plan.deliverables) != len(set(plan.deliverables)):
        found.errors.append("deliverables has duplicates")

    known = _by_id(plan)
    preds: dict[str, list[str]] = {task.id: [] for task in plan.tasks}
    graph_ok = True
    for task in plan.tasks:
        if not _check_task(task, known, roster, preds, found):
            graph_ok = False

    active = [task for task in plan.tasks if task.status != "cancelled"]
    active_ids = {task.id for task in active}
    if graph_ok and active:
        cycle_tasks = [
            replace(
                task,
                depends_on=[dep for dep in task.depends_on if dep.task in active_ids],
            )
            for task in active
        ]
        try:
            cycle = find_cycle(cycle_tasks)
        except ScheduleError as exc:
            found.errors.append(str(exc))
            cycle = None
        if cycle:
            found.errors.append("cycle: " + " -> ".join(cycle))

    for task in plan.tasks:
        if task.status == "cancelled":
            continue
        ancestors = _ancestors(task.id, preds)
        for item in task.inputs:
            _check_input(task, item, ancestors, produced, provided, known, found)

    for name in plan.deliverables:
        owner = produced.get(name)
        if owner is None or owner not in active_ids:
            found.errors.append(
                f"deliverable '{name}' is not produced by an active task"
            )

    consumed = {
        item.artifact
        for task in active
        for item in task.inputs
    }
    final = set(plan.deliverables)
    for task in active:
        for output in task.outputs:
            if output.artifact not in consumed and output.artifact not in final:
                found.errors.append(
                    f"{task.id}: output '{output.artifact}' is unused and is not a deliverable"
                )
    for name in sorted(provided):
        if name not in consumed:
            found.warnings.append(f"provided input '{name}' is not used by any task")

    for task in plan.tasks:
        if task.status == "done":
            found.errors.extend(done_lock_errors(plan, task))

    from planner.subplan import check_subplans

    check_subplans(plan, roster, found)

    effort = sum((task.estimate_hours.scheduling for task in active), Decimal(0))
    if effort > plan.budget.hours:
        found.warnings.append(
            "sum of scheduling hours (calibrated when set, else PERT expected) "
            "exceeds plan.budget.hours"
        )
    return found


def _optional_datetime(value: str, where: str, found: Findings):
    try:
        return parse_datetime(value, where)
    except ValueError as exc:
        found.errors.append(str(exc))
        return None


def _check_task(task: Task, known: dict[str, Task], roster: Roster, preds, found: Findings) -> bool:
    """Return False when the dependency edges should not be cycled-checked."""
    ok = True
    bot = roster.get(task.assignee)
    if bot is None:
        found.errors.append(
            f"{task.id}: assignee '{task.assignee}' is not in the roster"
        )
    else:
        missing = [cap for cap in task.required_capabilities if cap not in bot.capabilities]
        if missing:
            found.errors.append(
                f"{task.id}: assignee '{task.assignee}' lacks {', '.join(missing)} "
                f"(has {', '.join(bot.capabilities)})"
            )
    if not task.reversible and not task.needs_human:
        found.errors.append(
            f"{task.id}: reversible is false, so needs_human must be true"
        )
    if task.status == "done" and task.actuals.attempts < 1:
        found.warnings.append(f"{task.id}: status is done but actuals.attempts is 0")
    if task.status == "done" and task.actuals.hours is None:
        found.warnings.append(f"{task.id}: status is done but actuals.hours is null")
    for stamp, label in ((task.actuals.start, "start"), (task.actuals.end, "end")):
        if stamp:
            _optional_datetime(stamp, f"{task.id}.actuals.{label}", found)

    seen = set()
    for dep in task.depends_on:
        if dep.task == task.id:
            found.errors.append(f"{task.id}: depends on itself")
            ok = False
            continue
        if dep.task not in known:
            found.errors.append(f"{task.id}: depends on unknown task '{dep.task}'")
            ok = False
            continue
        if dep.task in seen:
            found.errors.append(f"{task.id}: depends on '{dep.task}' more than once")
            ok = False
            continue
        seen.add(dep.task)
        preds[task.id].append(dep.task)
        pred = known[dep.task]
        if pred.status == "cancelled" and task.status != "cancelled":
            found.errors.append(f"{task.id}: depends on cancelled task '{dep.task}'")
    return ok


def _check_input(task, item, ancestors, produced, provided, known, found: Findings):
    if item.from_task:
        if item.from_task not in known:
            found.errors.append(
                f"{task.id}: input '{item.artifact}' names unknown task '{item.from_task}'"
            )
            return
        if item.from_task not in ancestors:
            found.errors.append(
                f"{task.id}: input '{item.artifact}' must come from an upstream task "
                f"('{item.from_task}' is not a dependency)"
            )
        if produced.get(item.artifact) != item.from_task:
            found.errors.append(
                f"{task.id}: input '{item.artifact}' is not produced by {item.from_task}"
            )
        return
    if item.artifact not in provided:
        found.errors.append(
            f"{task.id}: input '{item.artifact}' is neither a provided input nor tied to from_task"
        )
