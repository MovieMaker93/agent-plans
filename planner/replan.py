# SPDX-License-Identifier: Apache-2.0
"""Replan from now.

In-progress and in-review tasks keep the start recorded in actuals. Their
remaining work is the scheduling duration minus logged hours (or elapsed
time when hours are still null). Not-yet-started tasks are scheduled from
now. Done tasks release their successors and do not take future capacity.

Defaults, unless the plan sets replan_policy:

- min_interval_minutes: 60. A second replan inside that window is refused
  unless --force, --human, or some task is failed.
- hysteresis_hours: 0.5. The new schedule is not written when the makespan
  moves by less than this and the critical path is unchanged, unless --force.
- freeze_window_hours: 1. Not-yet-started tasks whose previous start falls
  inside this window are listed as assignee-frozen. This command does not
  change assignees.

RCPSP stays a heuristic. This pass is the same priority list, anchored at now.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from planner.gantt import render_gantt, render_schedule_yaml
from planner.model import Plan, ReplanPolicy, Roster, Task
from planner.planedit import append_log, log_entry, set_version
from planner.schedule import ScheduleResult, schedule_tasks
from planner.util import fmt_hours, hours_between, parse_datetime
from planner.yamlio import YamlError, load_yaml

FROZEN_STATUSES = {"in_progress", "in_review"}
_KIND = re.compile(r"^- Kind:\s*replan\s*$", re.M)
_AT = re.compile(r"^- At:\s*(\S+)\s*$", re.M)


def effective_policy(plan: Plan) -> ReplanPolicy:
    return plan.replan_policy or ReplanPolicy()


def clock_from(at: str | None) -> datetime:
    if at:
        return parse_datetime(at, "--at")
    return datetime.now().astimezone().replace(microsecond=0)


def now_offset(plan: Plan, at: datetime) -> Decimal:
    origin = parse_datetime(plan.schedule_origin, "constraints.schedule_origin")
    if (at.tzinfo is None) != (origin.tzinfo is None):
        raise ValueError(
            "--at and schedule_origin must both include a timezone offset, or both omit it"
        )
    hours = hours_between(origin, at)
    if hours < 0:
        return Decimal(0)
    return hours


def _stamp_offset(plan: Plan, stamp: str | None) -> Decimal | None:
    if not stamp:
        return None
    origin = parse_datetime(plan.schedule_origin, "constraints.schedule_origin")
    moment = parse_datetime(stamp, "actuals")
    if (moment.tzinfo is None) != (origin.tzinfo is None):
        raise ValueError(f"{stamp} must use the same timezone style as schedule_origin")
    return hours_between(origin, moment)


def build_anchor(
    plan: Plan,
    now_hours: Decimal,
    base_durations: dict[str, Decimal] | None = None,
):
    """Return duration overrides, fixed windows, and human notes.

    `base_durations` is the sub-plan rollup. Not-yet-started sub-plan tasks
    keep that duration. Frozen tasks subtract actuals from it.
    """
    base_durations = base_durations or {}
    overrides: dict[str, Decimal] = {}
    fixed: dict[str, tuple[Decimal, Decimal]] = {}
    notes: list[str] = []
    for task in plan.tasks:
        if task.status == "cancelled":
            continue
        duration = base_durations.get(task.id, task.estimate_hours.scheduling)
        if task.status == "done":
            start = _stamp_offset(plan, task.actuals.start)
            if start is None:
                start = Decimal(0)
            end = _stamp_offset(plan, task.actuals.end)
            if end is None:
                if task.actuals.hours is not None:
                    end = start + task.actuals.hours
                else:
                    end = now_hours
            if end < start:
                end = start
            if end > now_hours:
                end = now_hours
            overrides[task.id] = Decimal(0)
            fixed[task.id] = (start, end)
            continue
        if task.status not in FROZEN_STATUSES:
            if task.id in base_durations:
                overrides[task.id] = base_durations[task.id]
            continue
        start = _stamp_offset(plan, task.actuals.start)
        if start is None:
            start = now_hours
            notes.append(f"{task.id} has no actuals.start; anchored at now")
        if start > now_hours:
            fixed[task.id] = (start, start + duration)
            overrides[task.id] = duration
            continue
        if task.actuals.hours is not None:
            consumed = task.actuals.hours
        else:
            elapsed = now_hours - start
            if elapsed < 0:
                elapsed = Decimal(0)
            consumed = elapsed if elapsed < duration else duration
        remaining = duration - consumed
        if remaining < 0:
            remaining = Decimal(0)
        finish = now_hours + remaining
        if finish < start:
            finish = start
        overrides[task.id] = finish - start
        fixed[task.id] = (start, finish)
    return overrides, fixed, notes


def schedule_from_now(plan: Plan, roster: Roster, at: datetime) -> tuple[ScheduleResult, list[str]]:
    from planner.subplan import rollup_durations

    now = now_offset(plan, at)
    base, rolled = rollup_durations(plan, roster)
    overrides, fixed, notes = build_anchor(plan, now, base)
    capacity = {bot.name: bot.capacity for bot in roster.bots}
    result = schedule_tasks(
        plan.tasks,
        capacity,
        plan.max_parallel,
        duration_overrides=overrides or None,
        not_before=now,
        fixed=fixed or None,
    )
    result.rolled_status = rolled
    return result, notes


def last_replan_at(log_text: str) -> datetime | None:
    found: datetime | None = None
    sections = re.split(r"(?=^## )", log_text, flags=re.M)
    for section in sections:
        if not _KIND.search(section):
            continue
        match = _AT.search(section)
        if not match:
            continue
        found = parse_datetime(match.group(1).strip().strip('"'), "log At")
    return found


def _previous(schedule_path: Path) -> tuple[Decimal, list[str]] | None:
    if not schedule_path.is_file():
        return None
    try:
        data = load_yaml(schedule_path)
    except YamlError:
        return None
    if not isinstance(data, dict):
        return None
    project = data.get("project") or {}
    if "makespan_hours" not in project:
        return None
    makespan = Decimal(str(project["makespan_hours"]))
    path = [str(item) for item in (project.get("critical_path") or [])]
    return makespan, path


def assignee_frozen(plan: Plan, now_hours: Decimal, schedule_path: Path) -> list[str]:
    policy = effective_policy(plan)
    if not schedule_path.is_file():
        return []
    try:
        data = load_yaml(schedule_path)
    except YamlError:
        return []
    if not isinstance(data, dict):
        return []
    starts: dict[str, Decimal] = {}
    for item in data.get("tasks") or []:
        if isinstance(item, dict) and "id" in item and "start" in item:
            starts[str(item["id"])] = Decimal(str(item["start"]))
    window_end = now_hours + policy.freeze_window_hours
    found = []
    for task in plan.tasks:
        if task.status not in {"planned", "ready"}:
            continue
        start = starts.get(task.id)
        if start is None:
            continue
        if start < window_end:
            found.append(task.id)
    return found


def _frozen_ids(plan: Plan) -> list[str]:
    return [task.id for task in plan.tasks if task.status in FROZEN_STATUSES]


def _identity(task: Task):
    return (
        task.assignee,
        task.objective,
        task.title,
        task.estimate_hours.optimistic,
        task.estimate_hours.likely,
        task.estimate_hours.pessimistic,
        task.status,
        tuple((dep.task, dep.type, dep.lag_hours) for dep in task.depends_on),
    )


@dataclass
class ReplanOutcome:
    code: int
    message: str


def replan_plan(
    plan: Plan,
    roster: Roster,
    *,
    reason: str,
    at: datetime,
    force: bool = False,
    human: bool = False,
) -> ReplanOutcome:
    if plan.directory is None or plan.path is None:
        return ReplanOutcome(1, "plan has no directory")
    if not reason.strip():
        return ReplanOutcome(1, "replan needs --reason")
    directory = plan.directory
    policy = effective_policy(plan)
    log_path = directory / "log.md"
    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    failed = any(task.status == "failed" for task in plan.tasks)
    bypass = None
    if force:
        bypass = "force"
    elif human:
        bypass = "human"
    elif failed:
        bypass = "failed-task"
    if bypass is None:
        previous_at = last_replan_at(log_text)
        if previous_at is not None:
            if (at.tzinfo is None) != (previous_at.tzinfo is None):
                return ReplanOutcome(1, "log replan timestamp timezone does not match --at")
            elapsed = hours_between(previous_at, at)
            limit = Decimal(policy.min_interval_minutes) / Decimal(60)
            if elapsed < limit:
                return ReplanOutcome(
                    1,
                    "replan refused: last replan at "
                    f"{previous_at.isoformat()} is within {policy.min_interval_minutes} minutes. "
                    "Pass --force or --human to override.",
                )
    try:
        result, notes = schedule_from_now(plan, roster, at)
    except Exception as exc:
        return ReplanOutcome(1, f"replan failed: {exc}")
    schedule_path = directory / "schedule.yaml"
    previous = _previous(schedule_path)
    old_makespan = previous[0] if previous else None
    old_path = previous[1] if previous else None
    delta = None if old_makespan is None else abs(result.makespan - old_makespan)
    path_same = old_path is not None and old_path == result.critical_path
    if (
        not force
        and delta is not None
        and path_same
        and delta < policy.hysteresis_hours
    ):
        return ReplanOutcome(
            0,
            "replan suppressed by hysteresis: makespan moved "
            f"{fmt_hours(delta)}h (threshold {fmt_hours(policy.hysteresis_hours)}h) "
            "and the critical path is unchanged. Pass --force to publish.",
        )
    frozen = _frozen_ids(plan)
    before = {task.id: _identity(task) for task in plan.tasks if task.id in frozen}
    window = assignee_frozen(plan, result.now_hours or Decimal(0), schedule_path)
    plan.version += 1
    text = plan.path.read_text(encoding="utf-8")
    try:
        updated = set_version(text, plan.version)
    except ValueError as exc:
        return ReplanOutcome(1, str(exc))
    plan.path.write_text(updated, encoding="utf-8")
    (directory / "schedule.yaml").write_text(render_schedule_yaml(plan, result), encoding="utf-8")
    (directory / "gantt.md").write_text(render_gantt(plan, result), encoding="utf-8")
    # Reload and refuse to leave a replan that rescope'd frozen work.
    from planner.model import load_plan

    try:
        again = load_plan(plan.path)
    except Exception as exc:
        return ReplanOutcome(1, f"replan wrote a plan that does not load: {exc}")
    for task in again.tasks:
        if task.id not in before:
            continue
        if _identity(task) != before[task.id]:
            return ReplanOutcome(
                1,
                f"replan changed frozen task {task.id}. The file was written; review it.",
            )
    if again.version != plan.version:
        return ReplanOutcome(1, "replan did not bump version")
    old_text = "none" if old_makespan is None else f"{fmt_hours(old_makespan)}h"
    path_text = " -> ".join(result.critical_path) or "(none)"
    change = (
        f"Rescheduled not-yet-started tasks from {at.isoformat()}. "
        f"Makespan {old_text} -> {fmt_hours(result.makespan)}h. "
        f"Critical path: {path_text}. "
        "Assignees were not changed. In-progress and in-review tasks kept their start."
    )
    if notes:
        change += " " + " ".join(notes) + "."
    stamp = at.isoformat()
    append_log(
        log_path,
        log_entry(
            version=plan.version,
            at=stamp,
            kind="replan",
            reason=reason.strip(),
            change=change,
            frozen=frozen,
            assignee_frozen=window,
            bypass=bypass,
        ),
    )
    frozen_text = ", ".join(frozen) if frozen else "none"
    return ReplanOutcome(
        0,
        "replan wrote schedule.yaml and gantt.md at version "
        f"{plan.version}. Frozen: {frozen_text}. "
        f"Makespan {old_text} -> {fmt_hours(result.makespan)}h.",
    )

