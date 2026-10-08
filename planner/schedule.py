# SPDX-License-Identifier: Apache-2.0
"""Critical path method and a resource-constrained priority list.

CPM slack assumes unlimited agents. Start and finish times then come from
a parallel priority-list heuristic:

1. Priority is least CPM slack, then longest expected duration, then most
   downstream tasks, then task id.
2. At the current time, start every task whose precedence is satisfied and
   whose assignee still has capacity, without passing max_parallel.
3. Advance time to the next finish or the next precedence release.

Intervals are half-open, so a successor can start when a predecessor
finishes. Zero-duration tasks (milestones) do not consume capacity.

This is a standard RCPSP heuristic. It is near-optimal, not optimal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from planner.model import DEP_TYPES, Dependency, Task

class ScheduleError(Exception):
    pass


@dataclass
class TaskTiming:
    id: str
    assignee: str
    expected: Decimal
    std_dev: Decimal
    earliest_start: Decimal
    earliest_finish: Decimal
    latest_start: Decimal
    latest_finish: Decimal
    slack: Decimal
    critical: bool
    start: Decimal
    finish: Decimal


@dataclass
class ScheduleResult:
    by_id: dict[str, TaskTiming]
    topo: list[str]
    critical_path: list[str]
    critical_tasks: list[str]
    cpm_duration: Decimal
    cpm_std_dev: Decimal
    makespan: Decimal
    effort: Decimal
    mode: str = "origin"
    now_hours: Decimal | None = None
    rolled_status: dict[str, str] = field(default_factory=dict)


def _active(tasks: list[Task]) -> list[Task]:
    return [task for task in tasks if task.status != "cancelled"]


def _links(tasks: list[Task]):
    index = {task.id: task for task in tasks}
    succs: dict[str, list[tuple[str, Dependency]]] = {task.id: [] for task in tasks}
    preds: dict[str, list[str]] = {task.id: [] for task in tasks}
    for task in tasks:
        seen = set()
        for dep in task.depends_on:
            if dep.task not in index:
                raise ScheduleError(
                    f"{task.id} depends on unknown task {dep.task}"
                )
            if dep.type not in DEP_TYPES:
                raise ScheduleError(
                    f"{task.id} dependency on {dep.task} has unknown type {dep.type}"
                )
            if dep.task in seen:
                raise ScheduleError(
                    f"{task.id} depends on {dep.task} more than once"
                )
            seen.add(dep.task)
            succs[dep.task].append((task.id, dep))
            preds[task.id].append(dep.task)
    return index, succs, preds


def find_cycle(tasks: list[Task]) -> list[str] | None:
    """Return one cycle as [a, b, a], or None if the graph is a DAG."""
    _, succs, _ = _links(tasks)
    white, gray, black = 0, 1, 2
    color = {task.id: white for task in tasks}
    parent: dict[str, str] = {}

    def walk(node: str) -> list[str] | None:
        color[node] = gray
        for succ, _dep in succs[node]:
            if color[succ] == gray:
                cycle = [node]
                while cycle[-1] != succ:
                    cycle.append(parent[cycle[-1]])
                cycle.reverse()
                cycle.append(succ)
                return cycle
            if color[succ] == white:
                parent[succ] = node
                found = walk(succ)
                if found:
                    return found
        color[node] = black
        return None

    for node in sorted(color):
        if color[node] == white:
            found = walk(node)
            if found:
                return found
    return None


def topo_sort(tasks: list[Task]) -> list[str]:
    import heapq

    _, succs, preds = _links(tasks)
    indegree = {task.id: len(preds[task.id]) for task in tasks}
    ready = [task_id for task_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        node = heapq.heappop(ready)
        order.append(node)
        for succ, _dep in succs[node]:
            indegree[succ] -= 1
            if indegree[succ] == 0:
                heapq.heappush(ready, succ)
    if len(order) != len(tasks):
        cycle = find_cycle(tasks)
        rendered = " -> ".join(cycle or [])
        raise ScheduleError(f"cycle: {rendered}")
    return order


def _duration(task: Task, overrides: dict[str, Decimal] | None = None) -> Decimal:
    """Scheduling duration. An override wins, then calibrated, then raw PERT."""
    if overrides is not None and task.id in overrides:
        return overrides[task.id]
    return task.estimate_hours.scheduling


def _candidate_start(pred: str, dep: Dependency, es: dict, ef_pred: Decimal, dur_succ: Decimal):
    lag = dep.lag_hours
    if dep.type == "FS":
        return ef_pred + lag
    if dep.type == "SS":
        return es[pred] + lag
    if dep.type == "FF":
        return ef_pred + lag - dur_succ
    return es[pred] + lag - dur_succ


def _cpm(
    tasks: list[Task],
    topo: list[str],
    index,
    succs,
    overrides: dict[str, Decimal] | None = None,
    not_before: Decimal | None = None,
    fixed: dict[str, tuple[Decimal, Decimal]] | None = None,
):
    es = {task.id: Decimal(0) for task in tasks}
    for node in topo:
        # Frozen tasks keep the start recorded on the anchor. Other tasks
        # cannot start before `not_before` when a from-now schedule is on.
        if fixed and node in fixed:
            es[node] = fixed[node][0]
        elif not_before is not None and es[node] < not_before:
            es[node] = not_before
        finish = es[node] + _duration(index[node], overrides)
        for succ, dep in succs[node]:
            cand = _candidate_start(node, dep, es, finish, _duration(index[succ], overrides))
            if cand > es[succ]:
                es[succ] = cand
    ef = {node: es[node] + _duration(index[node], overrides) for node in es}
    project_end = max(ef.values()) if ef else Decimal(0)
    lf = {node: project_end for node in es}
    for node in reversed(topo):
        dur_node = _duration(index[node], overrides)
        for succ, dep in succs[node]:
            lf_succ = lf[succ]
            ls_succ = lf_succ - _duration(index[succ], overrides)
            lag = dep.lag_hours
            if dep.type == "FS":
                bound = ls_succ - lag
            elif dep.type == "SS":
                bound = ls_succ - lag + dur_node
            elif dep.type == "FF":
                bound = lf_succ - lag
            else:
                bound = lf_succ - lag + dur_node
            if bound < lf[node]:
                lf[node] = bound
    ls = {node: lf[node] - _duration(index[node], overrides) for node in lf}
    slack = {node: ls[node] - es[node] for node in es}
    return es, ef, ls, lf, slack, project_end


def _downstream(succs) -> dict[str, int]:
    memo: dict[str, int] = {}

    def count(node: str) -> int:
        if node in memo:
            return memo[node]
        seen = set()
        stack = [succ for succ, _dep in succs[node]]
        while stack:
            nxt = stack.pop()
            if nxt in seen:
                continue
            seen.add(nxt)
            stack.extend(succ for succ, _dep in succs[nxt])
        memo[node] = len(seen)
        return memo[node]

    return {node: count(node) for node in succs}


def _precedence_est(
    task: Task,
    scheduled: dict[str, tuple[Decimal, Decimal]],
    index,
    overrides: dict[str, Decimal] | None = None,
):
    est = Decimal(0)
    for dep in task.depends_on:
        if dep.task not in scheduled:
            return None
        start, finish = scheduled[dep.task]
        if dep.type == "FS":
            cand = finish + dep.lag_hours
        elif dep.type == "SS":
            cand = start + dep.lag_hours
        elif dep.type == "FF":
            cand = finish + dep.lag_hours - _duration(task, overrides)
        else:
            cand = start + dep.lag_hours - _duration(task, overrides)
        if cand > est:
            est = cand
    return est


def _overlaps(start: Decimal, finish: Decimal, other_start: Decimal, other_finish: Decimal) -> bool:
    return start < other_finish and other_start < finish


def _fits(
    task: Task,
    start: Decimal,
    scheduled,
    index,
    capacity,
    max_parallel,
    overrides: dict[str, Decimal] | None = None,
) -> bool:
    dur = _duration(task, overrides)
    if dur == 0:
        return True
    finish = start + dur
    agent_load = 0
    global_load = 0
    for other_id, (other_start, other_finish) in scheduled.items():
        if _duration(index[other_id], overrides) == 0:
            continue
        if not _overlaps(start, finish, other_start, other_finish):
            continue
        global_load += 1
        if index[other_id].assignee == task.assignee:
            agent_load += 1
    return agent_load < capacity[task.assignee] and global_load < max_parallel


def _resource_schedule(
    tasks,
    index,
    slack,
    capacity,
    max_parallel,
    down,
    overrides: dict[str, Decimal] | None = None,
    not_before: Decimal | None = None,
    fixed: dict[str, tuple[Decimal, Decimal]] | None = None,
):
    scheduled: dict[str, tuple[Decimal, Decimal]] = dict(fixed or {})
    remaining = {task.id for task in tasks if task.id not in scheduled}
    t = not_before if not_before is not None else Decimal(0)
    steps = 0
    limit = len(tasks) * len(tasks) * 4 + 8
    while remaining:
        steps += 1
        if steps > limit:
            stuck = ", ".join(sorted(remaining))
            raise ScheduleError(f"no feasible start for {stuck}")
        eligible = []
        for task_id in remaining:
            est = _precedence_est(index[task_id], scheduled, index, overrides)
            if est is None:
                continue
            if not_before is not None and est < not_before:
                est = not_before
            if est <= t:
                eligible.append(task_id)
        eligible.sort(
            key=lambda task_id: (
                slack[task_id],
                -_duration(index[task_id], overrides),
                -down[task_id],
                task_id,
            )
        )
        progressed = False
        for task_id in eligible:
            task = index[task_id]
            if _fits(task, t, scheduled, index, capacity, max_parallel, overrides):
                scheduled[task_id] = (t, t + _duration(task, overrides))
                remaining.remove(task_id)
                progressed = True
        if progressed:
            continue
        candidates = []
        for _start, finish in scheduled.values():
            if finish > t:
                candidates.append(finish)
        for task_id in remaining:
            est = _precedence_est(index[task_id], scheduled, index, overrides)
            if est is not None and not_before is not None and est < not_before:
                est = not_before
            if est is not None and est > t:
                candidates.append(est)
        if not candidates:
            stuck = ", ".join(sorted(remaining))
            raise ScheduleError(f"no feasible start for {stuck}")
        nxt = min(candidates)
        if nxt <= t:
            stuck = ", ".join(sorted(remaining))
            raise ScheduleError(f"no feasible start for {stuck}")
        t = nxt
    return scheduled


def _binding(pred: str, succ: str, dep: Dependency, es, ef) -> bool:
    lag = dep.lag_hours
    if dep.type == "FS":
        return ef[pred] + lag == es[succ]
    if dep.type == "SS":
        return es[pred] + lag == es[succ]
    if dep.type == "FF":
        return ef[pred] + lag == ef[succ]
    return es[pred] + lag == ef[succ]


def _one_critical_path(topo, succs, es, ef, slack) -> list[str]:
    critical = {node for node, amount in slack.items() if amount == 0}
    if not critical:
        return []
    preds_crit = {node: [] for node in critical}
    for node in critical:
        for succ, dep in succs[node]:
            if succ in critical and _binding(node, succ, dep, es, ef):
                preds_crit.setdefault(succ, []).append(node)
    starts = [
        node
        for node in topo
        if node in critical and not any(pred in critical for pred in preds_crit.get(node, []))
    ]
    # A critical node whose critical predecessors are not binding still
    # needs a start. Fall back to critical nodes with no critical pred.
    if not starts:
        starts = [node for node in topo if node in critical]
    path = []
    seen = set()
    node = starts[0]
    while node and node not in seen:
        seen.add(node)
        path.append(node)
        nxts = []
        for succ, dep in succs[node]:
            if succ in critical and _binding(node, succ, dep, es, ef):
                nxts.append(succ)
        nxts.sort(key=lambda succ: (es[succ], succ))
        node = nxts[0] if nxts else None
    return path


def schedule_tasks(
    tasks: list[Task],
    capacity: dict[str, int],
    max_parallel: int,
    *,
    duration_overrides: dict[str, Decimal] | None = None,
    not_before: Decimal | None = None,
    fixed: dict[str, tuple[Decimal, Decimal]] | None = None,
) -> ScheduleResult:
    """Schedule non-cancelled tasks. Raises ScheduleError on a bad graph.

    `duration_overrides`, `not_before`, and `fixed` are the from-now anchor.
    Leave them unset for the origin-based schedule. `fixed` tasks keep the
    given start and finish (in-progress and in-review work). Other tasks
    cannot start before `not_before`.
    """
    selected = _active(tasks)
    if max_parallel < 1:
        raise ScheduleError("max_parallel must be >= 1")
    if not selected:
        return ScheduleResult(
            by_id={},
            topo=[],
            critical_path=[],
            critical_tasks=[],
            cpm_duration=Decimal(0),
            cpm_std_dev=Decimal(0),
            makespan=Decimal(0),
            effort=Decimal(0),
            mode="from-now" if not_before is not None else "origin",
            now_hours=not_before,
        )
    ids = [task.id for task in selected]
    if len(ids) != len(set(ids)):
        raise ScheduleError("duplicate task id")
    index, succs, _preds = _links(selected)
    for task in selected:
        if task.assignee not in capacity:
            raise ScheduleError(f"no capacity for assignee {task.assignee}")
        if capacity[task.assignee] < 1:
            raise ScheduleError(f"assignee {task.assignee} has no capacity")
    cycle = find_cycle(selected)
    if cycle:
        raise ScheduleError("cycle: " + " -> ".join(cycle))
    topo = topo_sort(selected)
    es, ef, ls, lf, slack, project_end = _cpm(
        selected,
        topo,
        index,
        succs,
        duration_overrides,
        not_before,
        fixed,
    )
    down = _downstream(succs)
    scheduled = _resource_schedule(
        selected,
        index,
        slack,
        capacity,
        max_parallel,
        down,
        duration_overrides,
        not_before,
        fixed,
    )
    path = _one_critical_path(topo, succs, es, ef, slack)
    critical_tasks = [node for node in topo if slack[node] == 0]
    variance = sum(
        (index[node].estimate_hours.std_dev ** 2) for node in path
    )
    std_dev = variance.sqrt() if variance > 0 else Decimal(0)
    by_id = {}
    for task in selected:
        start, finish = scheduled[task.id]
        by_id[task.id] = TaskTiming(
            id=task.id,
            assignee=task.assignee,
            expected=_duration(task, duration_overrides),
            std_dev=task.estimate_hours.std_dev,
            earliest_start=es[task.id],
            earliest_finish=ef[task.id],
            latest_start=ls[task.id],
            latest_finish=lf[task.id],
            slack=slack[task.id],
            critical=slack[task.id] == 0,
            start=start,
            finish=finish,
        )
    makespan = max(item.finish for item in by_id.values())
    effort = sum((item.expected for item in by_id.values()), Decimal(0))
    return ScheduleResult(
        by_id=by_id,
        topo=topo,
        critical_path=path,
        critical_tasks=critical_tasks,
        cpm_duration=project_end,
        cpm_std_dev=std_dev,
        makespan=makespan,
        effort=effort,
        mode="from-now" if not_before is not None else "origin",
        now_hours=not_before,
    )
