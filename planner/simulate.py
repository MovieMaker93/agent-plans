# SPDX-License-Identifier: Apache-2.0
"""Monte Carlo finish dates from calibrated PERT ranges.

Report only. Nothing is written. Each trial draws a triangular sample on
the optimistic / likely / pessimistic range, scaled so the mean matches
estimate_hours.calibrated when that field is set. Sub-plan tasks keep the
rolled makespan as a point duration.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_CEILING

from planner.model import Plan, Roster, Task
from planner.schedule import ScheduleError, schedule_tasks
from planner.subplan import rollup_durations
from planner.util import add_hours, fmt_hours, hours_between, parse_datetime

STEPS = 1_000_000


@dataclass
class SimSummary:
    makespans: list[Decimal]

    def percentile(self, p: Decimal) -> Decimal:
        ordered = sorted(self.makespans)
        if not ordered:
            return Decimal(0)
        rank = (p * Decimal(len(ordered))).to_integral_value(rounding=ROUND_CEILING)
        if rank < 1:
            rank = Decimal(1)
        if rank > len(ordered):
            rank = Decimal(len(ordered))
        return ordered[int(rank) - 1]


def _scale(task: Task) -> tuple[Decimal, Decimal, Decimal]:
    estimate = task.estimate_hours
    raw = estimate.expected
    if estimate.calibrated is None or raw == 0:
        return estimate.optimistic, estimate.likely, estimate.pessimistic
    scale = estimate.calibrated / raw
    return estimate.optimistic * scale, estimate.likely * scale, estimate.pessimistic * scale


def _triangular(rng: random.Random, optimistic: Decimal, likely: Decimal, pessimistic: Decimal) -> Decimal:
    if pessimistic <= optimistic:
        return optimistic
    unit = Decimal(rng.randrange(STEPS)) / Decimal(STEPS)
    span = pessimistic - optimistic
    cutoff = (likely - optimistic) / span
    if unit <= cutoff:
        inside = unit * span * (likely - optimistic)
        return optimistic + (inside.sqrt() if inside > 0 else Decimal(0))
    inside = (Decimal(1) - unit) * span * (pessimistic - likely)
    return pessimistic - (inside.sqrt() if inside > 0 else Decimal(0))


def _prepare(plan: Plan, omit: set[str]) -> list[Task]:
    tasks = []
    for task in plan.tasks:
        if task.id in omit or task.status == "cancelled":
            continue
        deps = [dep for dep in task.depends_on if dep.task not in omit]
        tasks.append(replace(task, depends_on=deps))
    return tasks


def _capacity(roster: Roster, additions: dict[str, int]) -> dict[str, int]:
    capacity = {bot.name: bot.capacity for bot in roster.bots}
    for name, extra in additions.items():
        if name not in capacity:
            raise ScheduleError(f"unknown assignee for --add-capacity: {name}")
        capacity[name] = capacity[name] + extra
        if capacity[name] < 1:
            raise ScheduleError(f"{name} capacity must stay >= 1")
    return capacity


def parse_capacity_adds(values: list[str]) -> dict[str, int]:
    added: dict[str, int] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"--add-capacity must look like name=N, not {item}")
        name, raw = item.split("=", 1)
        name = name.strip()
        raw = raw.strip()
        if raw.startswith("+"):
            raw = raw[1:]
        if not name or not raw.isdigit():
            raise ValueError(f"--add-capacity must look like name=N, not {item}")
        added[name] = added.get(name, 0) + int(raw)
    return added


def omit_ids(plan: Plan, tasks: list[str], deliverables: list[str]) -> set[str]:
    known = {task.id for task in plan.tasks}
    omit = set(tasks)
    unknown = sorted(task_id for task_id in omit if task_id not in known)
    if unknown:
        raise ValueError("unknown task " + ", ".join(unknown))
    produced = {}
    for task in plan.tasks:
        for output in task.outputs:
            produced.setdefault(output.artifact, task.id)
    missing = [name for name in deliverables if name not in produced]
    if missing:
        raise ValueError("unknown deliverable " + ", ".join(missing))
    for name in deliverables:
        omit.add(produced[name])
    return omit


def run_trials(
    plan: Plan,
    roster: Roster,
    *,
    seed: int,
    runs: int,
    additions: dict[str, int] | None = None,
    omit: set[str] | None = None,
) -> SimSummary:
    if runs < 1:
        raise ValueError("--runs must be >= 1")
    selected = _prepare(plan, omit or set())
    if not selected:
        return SimSummary([Decimal(0) for _ in range(runs)])
    capacity = _capacity(roster, additions or {})
    point, _rolled = rollup_durations(plan, roster)
    ranges = {task.id: _scale(task) for task in selected}
    rng = random.Random(seed)
    makespans = []
    for _ in range(runs):
        overrides = {}
        for task in selected:
            if task.id in point:
                overrides[task.id] = point[task.id]
                continue
            optimistic, likely, pessimistic = ranges[task.id]
            overrides[task.id] = _triangular(rng, optimistic, likely, pessimistic)
        result = schedule_tasks(
            selected,
            capacity,
            plan.max_parallel,
            duration_overrides=overrides,
        )
        makespans.append(result.makespan)
    return SimSummary(makespans)


def _finish(plan: Plan, hours: Decimal) -> str:
    origin = parse_datetime(plan.schedule_origin, "constraints.schedule_origin")
    return add_hours(origin, hours).isoformat()


def _slack(plan: Plan, hours: Decimal, deadline: str | None) -> str:
    if not deadline:
        return "n/a"
    origin = parse_datetime(plan.schedule_origin, "constraints.schedule_origin")
    finish = add_hours(origin, hours)
    moment = parse_datetime(deadline, "--deadline")
    return fmt_hours(hours_between(finish, moment)) + "h"


def render_simulation(
    plan: Plan,
    roster: Roster,
    *,
    seed: int = 1,
    runs: int = 200,
    additions: dict[str, int] | None = None,
    omit: set[str] | None = None,
    deadline: str | None = None,
) -> str:
    additions = additions or {}
    omit = omit or set()
    baseline = run_trials(plan, roster, seed=seed, runs=runs)
    p10 = baseline.percentile(Decimal("0.10"))
    p50 = baseline.percentile(Decimal("0.50"))
    p90 = baseline.percentile(Decimal("0.90"))
    lines = [
        f"plan: {plan.plan_id}",
        f"seed: {seed}",
        f"runs: {runs}",
        "method: triangular PERT, scaled so the mean matches calibrated hours when set",
        "writes: nothing",
        "baseline:",
        f"  p10: {_finish(plan, p10)} ({fmt_hours(p10)}h)",
        f"  p50: {_finish(plan, p50)} ({fmt_hours(p50)}h)",
        f"  p90: {_finish(plan, p90)} ({fmt_hours(p90)}h)",
        f"  deadline_slack_at_p50: {_slack(plan, p50, plan.deadline)}",
    ]
    changed = bool(additions or omit or deadline)
    if not changed:
        lines.append("what_if: (none)")
        return "\n".join(lines)
    scenario = run_trials(
        plan,
        roster,
        seed=seed,
        runs=runs,
        additions=additions,
        omit=omit,
    )
    s10 = scenario.percentile(Decimal("0.10"))
    s50 = scenario.percentile(Decimal("0.50"))
    s90 = scenario.percentile(Decimal("0.90"))
    compare_deadline = deadline or plan.deadline
    lines.append("what_if:")
    if additions:
        rendered = ", ".join(f"{name}+{amount}" for name, amount in sorted(additions.items()))
        lines.append(f"  add_capacity: {rendered}")
    else:
        lines.append("  add_capacity: (none)")
    lines.append("  omit: " + (", ".join(sorted(omit)) if omit else "(none)"))
    if deadline:
        lines.append(f"  deadline: {deadline}")
    else:
        lines.append("  deadline: (unchanged)")
    lines.extend(
        [
            f"  p10: {_finish(plan, s10)} ({fmt_hours(s10)}h)",
            f"  p50: {_finish(plan, s50)} ({fmt_hours(s50)}h)",
            f"  p90: {_finish(plan, s90)} ({fmt_hours(s90)}h)",
            f"  delta_p10_hours: {fmt_hours(s10 - p10)}",
            f"  delta_p50_hours: {fmt_hours(s50 - p50)}",
            f"  delta_p90_hours: {fmt_hours(s90 - p90)}",
            f"  deadline_slack_at_p50: {_slack(plan, s50, compare_deadline)}",
        ]
    )
    return "\n".join(lines)
