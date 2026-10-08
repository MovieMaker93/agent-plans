# SPDX-License-Identifier: Apache-2.0
"""Instantiate a plan from plans/_templates/<kind>/."""

from __future__ import annotations

from pathlib import Path

from planner.util import bundled_plans_dir, template_library_dirs


def kinds(root: Path) -> list[str]:
    return [path.name for path in template_library_dirs(root)]


def instantiate(root: Path, kind: str, into: Path, plan_id: str, title: str | None = None) -> Path:
    source_dir = bundled_plans_dir(root) / "_templates" / kind
    source = source_dir / "plan.yaml"
    if not source.is_file():
        known = ", ".join(kinds(root)) or "(none)"
        raise ValueError(f"unknown template '{kind}'. Known kinds: {known}")
    if not plan_id.strip():
        raise ValueError("--plan-id must not be empty")
    into.mkdir(parents=True, exist_ok=True)
    dest = into / "plan.yaml"
    if dest.exists():
        raise ValueError(f"{dest} already exists")
    text = source.read_text(encoding="utf-8")
    lines = []
    replaced_id = False
    replaced_title = title is None
    for line in text.splitlines(keepends=True):
        if line.startswith("plan_id:"):
            lines.append(f"plan_id: {plan_id.strip()}\n")
            replaced_id = True
            continue
        if title is not None and line.startswith("title:"):
            lines.append(f"title: {title.strip()}\n")
            replaced_title = True
            continue
        lines.append(line if line.endswith("\n") else line + "\n")
    if not replaced_id:
        raise ValueError(f"{source} has no plan_id line")
    if not replaced_title:
        raise ValueError(f"{source} has no title line")
    dest.write_text("".join(lines), encoding="utf-8")
    log = into / "log.md"
    if not log.exists():
        log.write_text(
            "\n".join(
                [
                    "# Decision log",
                    "",
                    "Append-only. Add new entries at the bottom. Do not edit earlier entries.",
                    "",
                    "## 2026-10-08 v1",
                    "",
                    f"- Reason: Instantiated from plans/_templates/{kind}.",
                    f"- Change: Created the plan from the {kind} template. template_kind stays {kind}.",
                    "- Next: Set the goal, the deadline, and schedule_origin, then validate and schedule.",
                    "- Frozen: none (no task is in progress).",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return dest
