# SPDX-License-Identifier: Apache-2.0
"""Level every open plan against one roster.

Ordering when two tasks could start is plan priority (higher first), then
earlier deadline, then least CPM slack, then the same tie breaks the
single-plan scheduler uses. Assignee capacity is shared. Each plan still
honors its own max_parallel. This does not rewrite per-plan schedule.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from planner.model import Plan, Roster, Task
from planner.reports import schedule_plan
from planner.schedule import ScheduleError, ScheduleResult
from planner.util import hours_between, parse_datetime, plan_directory_label
from planner.yamlio import dump_yaml

ORDERING = "priority, then deadline, then least slack"
FAR = Decimal("1000000000000")


@dataclass
class _Item:
    plan_id: str
    task: Task
    duration: Decimal
    offset: Decimal
    priority: int
    deadline_hours: Decimal
    slack: Decimal
    downstream: int
    preds: list[tuple[str, str, Decimal]]
    max_parallel: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.plan_id, self.task.id)


def is_open(plan: Plan) -> bool:
    return any(task.status not in {"done", "cancelled"} for task in plan.tasks)


def _downstream(plan: Plan) -> dict[str, int]:
    succs: dict[str, list[str]] = {task.id: [] for task in plan.tasks}
    for task in plan.tasks:
        for dep in task.depends_on:
            if dep.task in succs:
                succs[dep.task].append(task.id)
    memo: dict[str, int] = {}

    def count(node: str) -> int:
        if node in memo:
            return memo[node]
        seen: set[str] = set()
        stack = list(succs[node])
        while stack:
            nxt = stack.pop()
            if nxt in seen or nxt not in succs:
                continue
            seen.add(nxt)
            stack.extend(succs[nxt])
        memo[node] = len(seen)
        return memo[node]

    return {task.id: count(task.id) for task in plan.tasks}


def _overlaps(start: Decimal, finish: Decimal, other_start: Decimal, other_finish: Decimal) -> bool:
    return start < other_finish and other_start < finish


def _precedence(item: _Item, scheduled: dict[tuple[str, str], tuple[Decimal, Decimal]]) -> Decimal | None:
    est = item.offset
    for plan_id, task_id, lag in item.preds:
        key = (plan_id, task_id)
        if key not in scheduled:
            return None
        _start, finish = scheduled[key]
        release = finish + lag
        if release > est:
            est = release
    return est


def _fits(
    item: _Item,
    start: Decimal,
    scheduled: dict[tuple[str, str], tuple[Decimal, Decimal]],
    by_key: dict[tuple[str, str], _Item],
    capacity: dict[str, int],
) -> bool:
    if item.duration == 0:
        return True
    finish = start + item.duration
    agent_load = 0
    plan_load = 0
    for key, (other_start, other_finish) in scheduled.items():
        other = by_key[key]
        if other.duration == 0:
            continue
        if not _overlaps(start, finish, other_start, other_finish):
            continue
        if other.task.assignee == item.task.assignee:
            agent_load += 1
        if other.plan_id == item.plan_id:
            plan_load += 1
    cap = capacity.get(item.task.assignee, 0)
    return agent_load < cap and plan_load < item.max_parallel


def _priority(item: _Item):
    return (
        -item.priority,
        item.deadline_hours,
        item.slack,
        -item.duration,
        -item.downstream,
        item.plan_id,
        item.task.id,
    )


def _items(plans: list[Plan], roster: Roster, origin: datetime) -> tuple[list[_Item], dict[str, ScheduleResult]]:
    capacity = {bot.name: bot.capacity for bot in roster.bots}
    results: dict[str, ScheduleResult] = {}
    items: list[_Item] = []
    for plan in plans:
        result = schedule_plan(plan, roster)
        results[plan.plan_id] = result
        down = _downstream(plan)
        plan_origin = parse_datetime(plan.schedule_origin, "constraints.schedule_origin")
        offset = hours_between(origin, plan_origin)
        if offset < 0:
            offset = Decimal(0)
        if plan.deadline:
            deadline_hours = hours_between(
                origin, parse_datetime(plan.deadline, "constraints.deadline")
            )
        else:
            deadline_hours = FAR
        active = {task.id for task in plan.tasks if task.status != "cancelled"}
        for task in plan.tasks:
            if task.status == "cancelled":
                continue
            if task.assignee not in capacity:
                raise ScheduleError(f"no capacity for assignee {task.assignee}")
            timing = result.by_id.get(task.id)
            duration = timing.expected if timing is not None else task.estimate_hours.scheduling
            slack = timing.slack if timing is not None else Decimal(0)
            preds = []
            seen_preds: set[str] = set()
            for dep in task.depends_on:
                if dep.task not in active or dep.task in seen_preds:
                    continue
                seen_preds.add(dep.task)
                # Finish-to-start keeps its lag. Other link types wait until
                # the predecessor finishes so a successor cannot start early.
                lag = dep.lag_hours if dep.type == "FS" else Decimal(0)
                preds.append((plan.plan_id, dep.task, lag))
            items.append(
                _Item(
                    plan_id=plan.plan_id,
                    task=task,
                    duration=duration,
                    offset=offset,
                    priority=plan.priority,
                    deadline_hours=deadline_hours,
                    slack=slack,
                    downstream=down.get(task.id, 0),
                    preds=preds,
                    max_parallel=plan.max_parallel,
                )
            )
    return items, results


def level_plans(plans: list[Plan], roster: Roster) -> dict:
    """Schedule open plans on one roster. Closed plans are ignored by the caller."""
    if not plans:
        return {
            "schema_version": 1,
            "kind": "portfolio-schedule",
            "ordering": ORDERING,
            "origin": None,
            "makespan_hours": Decimal(0),
            "overallocated": [],
            "plans": [],
            "tasks": [],
        }
    origins = [
        parse_datetime(plan.schedule_origin, "constraints.schedule_origin") for plan in plans
    ]
    origin = min(origins)
    items, _results = _items(plans, roster, origin)
    by_key = {item.key: item for item in items}
    scheduled: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    remaining = set(by_key)
    t = Decimal(0)
    steps = 0
    limit = max(len(items), 1) * max(len(items), 1) * 4 + 8
    while remaining:
        steps += 1
        if steps > limit:
            stuck = ", ".join(sorted(f"{plan}:{task}" for plan, task in remaining))
            raise ScheduleError(f"no feasible start for {stuck}")
        eligible = []
        for key in remaining:
            est = _precedence(by_key[key], scheduled)
            if est is None:
                continue
            if est <= t:
                eligible.append(by_key[key])
        eligible.sort(key=_priority)
        progressed = False
        for item in eligible:
            if _fits(item, t, scheduled, by_key, {bot.name: bot.capacity for bot in roster.bots}):
                scheduled[item.key] = (t, t + item.duration)
                remaining.remove(item.key)
                progressed = True
        if progressed:
            continue
        candidates = [finish for _start, finish in scheduled.values() if finish > t]
        for key in remaining:
            est = _precedence(by_key[key], scheduled)
            if est is not None and est > t:
                candidates.append(est)
        if not candidates:
            stuck = ", ".join(sorted(f"{plan}:{task}" for plan, task in remaining))
            raise ScheduleError(f"no feasible start for {stuck}")
        nxt = min(candidates)
        if nxt <= t:
            stuck = ", ".join(sorted(f"{plan}:{task}" for plan, task in remaining))
            raise ScheduleError(f"no feasible start for {stuck}")
        t = nxt
    return _document(plans, items, scheduled, origin)


def _document(plans, items: list[_Item], scheduled, origin: datetime) -> dict:
    rows = []
    for item in items:
        start, finish = scheduled[item.key]
        rows.append(
            {
                "plan_id": item.plan_id,
                "task_id": item.task.id,
                "assignee": item.task.assignee,
                "start": start,
                "finish": finish,
                "slack": item.slack,
                "duration_hours": item.duration,
                "priority": item.priority,
            }
        )
    rows.sort(key=lambda row: (row["start"], row["plan_id"], row["task_id"]))
    overallocated = _overallocated(rows)
    makespan = max((row["finish"] for row in rows), default=Decimal(0))
    plan_rows = []
    offsets = {item.plan_id: item.offset for item in items}
    priorities = {item.plan_id: item.priority for item in items}
    for plan in plans:
        plan_rows.append(
            {
                "plan_id": plan.plan_id,
                "directory": plan_directory_label(plan.directory, plan.plan_id),
                "priority": priorities.get(plan.plan_id, plan.priority),
                "offset_hours": offsets.get(plan.plan_id, Decimal(0)),
                "deadline": plan.deadline,
            }
        )
    return {
        "schema_version": 1,
        "kind": "portfolio-schedule",
        "ordering": ORDERING,
        "origin": origin.isoformat(),
        "makespan_hours": makespan,
        "overallocated": overallocated,
        "plans": plan_rows,
        "tasks": rows,
    }


def _overallocated(rows: list[dict]) -> list[str]:
    """Assignees with two positive-duration tasks in the same half-open window."""
    found: set[str] = set()
    for index, row in enumerate(rows):
        if row["duration_hours"] == 0:
            continue
        for other in rows[index + 1 :]:
            if other["assignee"] != row["assignee"] or other["duration_hours"] == 0:
                continue
            if _overlaps(row["start"], row["finish"], other["start"], other["finish"]):
                found.add(row["assignee"])
    return sorted(found)


def render_portfolio_schedule(document: dict) -> str:
    header = (
        "# Generated by `python -m planner portfolio schedule`. Do not edit by hand.\n"
        "# Open plans share one roster. Per-plan schedule.yaml is a separate view.\n"
        f"# Ordering: {ORDERING}.\n"
    )
    return header + dump_yaml(document)

