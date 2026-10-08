# SPDX-License-Identifier: Apache-2.0
"""Self-contained task briefs for Orchestrator and Planner handoff.

`ready` is still the queue. `brief` is the next step: one markdown file a
bot can run without a human translating the plan. Briefs are generated.
Do not edit them by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

from planner.attest import load_manifest, manifest_path
from planner.calibration import calibration_for, preview_hours, resolve_factor
from planner.model import Plan, Task
from planner.reports import ScheduleResult, is_dispatchable
from planner.util import fmt_hours, plan_directory_label


def _paragraph(text: str) -> str:
    return " ".join(text.split())


def _hash_line(plan: Plan, task_id: str, artifact: str) -> str:
    if plan.directory is None:
        return "not recorded"
    path = manifest_path(plan, task_id)
    if not path.is_file():
        return "not recorded"
    try:
        manifest = load_manifest(path)
    except ValueError:
        return "not recorded"
    record = manifest.artifacts.get(artifact)
    if record is None:
        return "not recorded"
    return record.sha256


def render_brief(plan: Plan, task: Task) -> str:
    cal = calibration_for(plan)
    raw, adjusted, count, basis = preview_hours(task, cal, plan)
    factor, _count, _basis = resolve_factor(cal, task, plan)
    stored = task.estimate_hours.calibrated
    if stored is None:
        stored_text = "(none)"
    else:
        stored_text = f"{fmt_hours(stored)}h"
    retries_left = task.max_retries - task.actuals.attempts
    if retries_left < 0:
        retries_left = 0
    lines = [
        f"# Brief: {task.id} {task.title}",
        "",
        "Do not expand scope. Do the objective, produce the outputs, and stop.",
        "",
        f"- Plan: {plan.plan_id}",
        f"- Plan directory: {plan_directory_label(plan.directory, plan.plan_id)}",
        f"- Task: {task.id}",
        f"- Assignee: {task.assignee}",
        f"- Status: {task.status}",
        f"- Task type: {task.task_type}",
        "",
        "## Goal",
        "",
        _paragraph(plan.goal),
        "",
        "## Objective",
        "",
        _paragraph(task.objective),
        "",
        "## Inputs",
        "",
    ]
    if not task.inputs:
        lines.append("- None. The objective is the input.")
    for item in task.inputs:
        if item.from_task:
            digest = _hash_line(plan, item.from_task, item.artifact)
            lines.append(
                f"- `{item.artifact}` from {item.from_task}. "
                f"Path: `artifacts/{item.from_task}/{item.artifact}`. "
                f"sha256: {digest}."
            )
        else:
            lines.append(
                f"- `{item.artifact}` is a provided input. "
                "It is not produced by a task, so there is no artifact hash."
            )
    lines.extend(["", "## Outputs", ""])
    lines.append(
        f"Write each output and record it with `python -m planner record-artifact` "
        f"so `artifacts/{task.id}/manifest.yaml` has a sha256."
    )
    lines.append("")
    for item in task.outputs:
        kind = item.type or "file"
        lines.append(
            f"- `{item.artifact}` ({kind}). "
            f"Expected path: `artifacts/{task.id}/{item.artifact}`."
        )
    lines.extend(["", "## Acceptance criteria", ""])
    for criterion in task.acceptance_criteria:
        lines.append(f"- {criterion}")
    lines.extend(
        [
            "",
            "## Estimate",
            "",
            f"- Raw PERT expected: {fmt_hours(raw)}h "
            f"(optimistic {fmt_hours(task.estimate_hours.optimistic)}, "
            f"likely {fmt_hours(task.estimate_hours.likely)}, "
            f"pessimistic {fmt_hours(task.estimate_hours.pessimistic)}).",
            f"- Calibration: factor {fmt_hours(factor)}, n={count}, basis {basis}.",
            f"- Preview calibrated hours: {fmt_hours(adjusted)}h.",
            f"- Stored estimate_hours.calibrated: {stored_text}.",
            "",
            "## Budget",
            "",
            f"- tokens: {task.budget.tokens}",
            f"- hours: {fmt_hours(task.budget.hours)}",
            f"- usd: {fmt_hours(task.budget.usd)}",
            f"- retries left: {retries_left} (max_retries {task.max_retries}, "
            f"attempts so far {task.actuals.attempts}).",
            "",
            "## Report back",
            "",
            "Return this JSON to the Planner. Do not set status to done. "
            "Accuracy runs attest-done. The Planner runs complete.",
            "",
            "```json",
            json.dumps(
                {
                    "plan_id": plan.plan_id,
                    "task_id": task.id,
                    "status": "in_review",
                    "actuals": {
                        "hours": None,
                        "attempts": task.actuals.attempts + 1,
                    },
                    "artifacts": [item.artifact for item in task.outputs],
                    "notes": "",
                },
                indent=2,
            ),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def brief_targets(plan: Plan, result: ScheduleResult | None, task_id: str | None) -> list[Task]:
    known = {task.id: task for task in plan.tasks}
    if task_id:
        task = known.get(task_id)
        if task is None:
            raise ValueError(f"unknown task {task_id}")
        return [task]
    if result is None:
        raise ValueError("a schedule is required to brief every dispatchable task")
    return [task for task in plan.tasks if is_dispatchable(task, known)]


def write_briefs(plan: Plan, tasks: list[Task]) -> list[Path]:
    if plan.directory is None:
        raise ValueError("plan has no directory")
    folder = plan.directory / "briefs"
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for task in tasks:
        path = folder / f"{task.id}.md"
        path.write_text(render_brief(plan, task), encoding="utf-8")
        written.append(path)
    return written
